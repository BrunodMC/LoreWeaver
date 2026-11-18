import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import audit, composite, db, generation, knowledge, ornament, search as search_mod, statblock
from ..entity_types import ENTITY_TYPES, NAV_GROUPS, STATUSES, EntityType

def local_time(utc_value: str) -> str:
    """SQLite stores UTC ('YYYY-MM-DD HH:MM:SS'); show it in this machine's local time."""
    utc = datetime.strptime(utc_value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return utc.astimezone().strftime("%Y-%m-%d %H:%M")


templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
templates.env.globals["entity_types"] = ENTITY_TYPES
templates.env.globals["nav_groups"] = NAV_GROUPS
templates.env.globals["ornament"] = ornament


def roman(number: int) -> str:
    """Catalogue numbers, the way Haven carves them: 52 → LII."""
    out = ""
    for value, numeral in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                           (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while number >= value:
            out, number = out + numeral, number - value
    return out or "N"


templates.env.filters["roman"] = roman
templates.env.filters["plural"] = lambda etype, party_view=False: (etype.party_plural or etype.plural) if party_view else etype.plural
templates.env.filters["statblock"] = statblock.render
templates.env.globals["statblock_mechanics"] = statblock.mechanics
templates.env.globals["mechanical_fields"] = statblock.MECHANICAL
templates.env.filters["lore"] = knowledge.render
templates.env.filters["local_time"] = local_time

router = APIRouter()

VIEW_COOKIE = "view"

STATUS_FILTERS = {
    None: ("canon", "draft"),
    "canon": ("canon",),
    "draft": ("draft",),
    "archived": ("archived",),
    "all": STATUSES,
}


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.get_connection()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def is_party_view(request: Request) -> bool:
    return request.cookies.get(VIEW_COOKIE) == "party"


def render(request: Request, name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request, name, {"party_view": is_party_view(request), **context}, status_code=status_code
    )


def entity_type_or_404(type_key: str) -> EntityType:
    etype = ENTITY_TYPES.get(type_key)
    if etype is None:
        raise HTTPException(404, "Unknown entity type")
    return etype


def entity_or_404(conn: sqlite3.Connection, type_key: str, entity_id: int) -> dict:
    entity = db.get_entity(conn, entity_id)
    if entity is None or entity["type"] != type_key:
        raise HTTPException(404, "Entity not found")
    return entity


def link_of_entity_or_404(conn: sqlite3.Connection, entity_id: int, link_id: int) -> dict:
    link = db.get_link(conn, link_id)
    if link is None or entity_id not in (link["from_entity_id"], link["to_entity_id"]):
        raise HTTPException(404, "Link not found")
    return link


def extra_fields(etype: EntityType, entity: dict) -> dict:
    spec_keys = {f.key for f in etype.fields}
    return {k: v for k, v in entity["fields"].items() if k not in spec_keys}


def field_error(etype: EntityType, form) -> str | None:
    for f in etype.fields:
        value = str(form.get(f.key, "")).strip()
        if f.widget == "select" and value and value not in f.options:
            return f"{f.label} must be one of: {', '.join(f.options)}."
    labels = [f.label for f in etype.fields if knowledge.has_unclosed_secret(str(form.get(f.key, "")))]
    if labels:
        return (f"Unclosed || secret marker in: {', '.join(labels)}. "
                "Secrets must be wrapped on both sides, like ||this||.")
    return None


def render_links(request: Request, conn: sqlite3.Connection, etype: EntityType,
                 entity: dict, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    return render(request, "_links_section.html", {
        "etype": etype,
        "entity": entity,
        "links": db.links_for(conn, entity["id"], is_party_view(request)),
        "targets": db.link_targets(conn, entity["id"]),
        "labels": db.relation_labels(conn),
        "error": error,
    }, status_code)


def under_review(conn: sqlite3.Connection, entity: dict) -> bool:
    """An AI draft promotes through its review panel (which also creates the chosen links)."""
    return entity["status"] == "draft" and db.lineage_for_entity(conn, entity["id"]) is not None


def render_status(request: Request, conn: sqlite3.Connection, etype: EntityType, entity_id: int) -> HTMLResponse:
    entity = db.get_entity(conn, entity_id)
    return render(request, "_status_actions.html", {
        "etype": etype, "entity": entity, "statuses": STATUSES, "reviewing": under_review(conn, entity),
    })


def render_form(request: Request, etype: EntityType, entity: dict | None,
                values: dict, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    return render(request, "form.html", {
        "etype": etype, "entity": entity, "values": values,
        "statuses": STATUSES, "error": error,
    }, status_code)


# ── View mode ──────────────────────────────────────────────────────────────

@router.get("/view/{mode}")
async def set_view(mode: str, next: str = "/"):
    if mode not in ("dm", "party"):
        raise HTTPException(404, "Unknown view")
    if not next.startswith("/") or next.startswith("//"):
        next = "/"  # only same-site paths, so this can't become an open redirect
    response = RedirectResponse(next, status_code=303)
    response.set_cookie(VIEW_COOKIE, mode, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return response


# ── Pages ──────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def index(request: Request, conn: Conn):
    return render(request, "index.html", {
        "counts": db.status_counts(conn),
        "party_counts": db.party_counts(conn),
        "statuses": STATUSES,
    })


@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, conn: Conn, q: str = "", status: str | None = None,
                 type: str | None = None):
    if status not in STATUS_FILTERS:
        status = None
    if type not in ENTITY_TYPES:
        type = None
    party_view = is_party_view(request)
    results = search_mod.search(conn, q, STATUS_FILTERS[status], party_view, type)
    return render(request, "search.html", {
        "query": q, "results": results, "status_filter": status, "type_filter": type,
    })


@router.get("/{type_key}", response_class=HTMLResponse)
async def list_view(request: Request, type_key: str, conn: Conn, status: str | None = None):
    etype = entity_type_or_404(type_key)
    if status not in STATUS_FILTERS:
        status = None
    return render(request, "list.html", {
        "etype": etype,
        "entities": db.list_entities(conn, type_key, STATUS_FILTERS[status], is_party_view(request)),
        "status_filter": status,
    })


@router.get("/{type_key}/new", response_class=HTMLResponse)
async def new_form(request: Request, type_key: str):
    etype = entity_type_or_404(type_key)
    return render_form(request, etype, None, {"name": "", "status": "canon", "known_to_party": int(etype.default_known), "fields": {}})


@router.post("/{type_key}")
async def create(request: Request, type_key: str, conn: Conn):
    etype = entity_type_or_404(type_key)
    form = await request.form()
    name = str(form.get("name", "")).strip()
    status = str(form.get("status", "canon"))
    known = form.get("known") == "1"
    fields = db.merge_fields(type_key, {k: str(v) for k, v in form.items()})
    error = ("A name is required." if not name
             else "Invalid status." if status not in STATUSES
             else field_error(etype, form))
    if error:
        values = {"name": name, "status": status, "known_to_party": int(known), "fields": fields}
        return render_form(request, etype, None, values, error, 422)
    entity_id = db.create_entity(conn, type_key, name, fields, status, known)
    db.set_party_name(conn, entity_id, str(form.get("party_name", "")))
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


def encounter_of(conn: sqlite3.Connection, entity: dict) -> dict | None:
    """For a member drafted for a composed encounter: that encounter, to get back to its cast."""
    composite_id = db.composite_for_entity(conn, entity["id"])
    comp = db.get_composite(conn, composite_id) if composite_id else None
    if comp is None or not comp["encounter_id"] or comp["encounter_id"] == entity["id"]:
        return None
    return db.get_entity(conn, comp["encounter_id"])


def secrecy_state(conn: sqlite3.Connection, entity_id: int, just_applied: str | None) -> dict:
    """The secrecy check's panel: a check running, an open one to rule on, or what couldn't be hidden."""
    failed = []
    if just_applied and just_applied.isdigit():
        settled = audit.get(conn, int(just_applied))
        if settled and settled["entity_id"] == entity_id:
            failed = [f for f in settled["findings"] if settled["state"].get(f["key"]) == "failed"]
    return {"task": audit.task_for("secrecy", entity_id), "audit": audit.latest_open(conn, "secrecy", entity_id),
            "failed": failed}


@router.get("/{type_key}/{entity_id}", response_class=HTMLResponse)
async def detail(request: Request, type_key: str, entity_id: int, conn: Conn):
    etype = entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    party_view = is_party_view(request)
    if party_view and not db.visible_to_party(entity):
        return render(request, "unknown.html", {"etype": etype}, 404)
    if party_view:
        entity = db.as_party_sees(entity)  # the page (title, caption, page title) wears the name they know
    review = None
    if not party_view and under_review(conn, entity):
        review = generation.review_state(conn, entity)
    return render(request, "detail.html", {
        "etype": etype,
        "entity": entity,
        "review": review,
        "reviewing": review is not None,
        "cast": composite.cast_review(conn, entity) if not party_view and entity["type"] == "encounter" else None,
        "back_to_cast": None if party_view else encounter_of(conn, entity),
        "can_reveal": not party_view and db.visible_to_party(entity),
        "secrecy": None if party_view else secrecy_state(conn, entity_id, request.query_params.get("audit")),
        "session_reveals": db.reveals(conn, entity_id) if not party_view and entity["type"] == "session" else None,
        "has_history": not party_view and db.lineage_for_entity(conn, entity_id) is not None,
        "extra_fields": extra_fields(etype, entity),
        "statuses": STATUSES,
        "links": db.links_for(conn, entity_id, party_view),
        "targets": db.link_targets(conn, entity_id),
        "labels": db.relation_labels(conn),
        "error": None,
    })


@router.get("/{type_key}/{entity_id}/edit", response_class=HTMLResponse)
async def edit_form(request: Request, type_key: str, entity_id: int, conn: Conn):
    etype = entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    return render_form(request, etype, entity, entity)


@router.post("/{type_key}/{entity_id}")
async def update(request: Request, type_key: str, entity_id: int, conn: Conn):
    etype = entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    form = await request.form()
    name = str(form.get("name", "")).strip()
    fields = db.merge_fields(type_key, {k: str(v) for k, v in form.items()}, entity["fields"])
    error = "A name is required." if not name else field_error(etype, form)
    if error:
        return render_form(request, etype, entity, {**entity, "name": name, "fields": fields}, error, 422)
    db.update_entity(conn, entity_id, name, fields)
    db.set_party_name(conn, entity_id, str(form.get("party_name", "")))
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


# ── HTMX partials ──────────────────────────────────────────────────────────

@router.post("/{type_key}/{entity_id}/status", response_class=HTMLResponse)
async def change_status(request: Request, type_key: str, entity_id: int, conn: Conn):
    etype = entity_type_or_404(type_key)
    entity_or_404(conn, type_key, entity_id)
    status = str((await request.form()).get("status", ""))
    if status not in STATUSES:
        raise HTTPException(400, "Invalid status")
    db.set_status(conn, entity_id, status)
    return render_status(request, conn, etype, entity_id)


@router.post("/{type_key}/{entity_id}/known", response_class=HTMLResponse)
async def change_known(request: Request, type_key: str, entity_id: int, conn: Conn):
    etype = entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    known = (await request.form()).get("known") == "1"
    db.set_known(conn, entity_id, known)
    if known and not entity["known_to_party"]:
        db.record_reveal(conn, entity)  # the whole entry, logged for the session
    return render_status(request, conn, etype, entity_id)


@router.post("/{type_key}/{entity_id}/reveal-name")
async def reveal_name(request: Request, type_key: str, entity_id: int, conn: Conn):
    """The party learns the real name: the alias goes, and the reveal is logged for the session."""
    if is_party_view(request):
        raise HTTPException(404, "Not available in party view")
    entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    alias = (entity.get("party_name") or "").strip()
    if alias:
        db.set_party_name(conn, entity_id, "")
        if entity["known_to_party"]:
            db.record_reveal(conn, entity, "True name", f"{entity['name']} (known until now as {alias})")
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


@router.post("/{type_key}/{entity_id}/tease/{field_key}/{index}")
async def tease_passage(request: Request, type_key: str, entity_id: int, field_key: str, index: int, conn: Conn):
    """Turn one secret passage into a tease (the party sees it struck out in ink), or back."""
    if is_party_view(request):
        raise HTTPException(404, "Not available in party view")
    etype = entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    field = next((f for f in etype.fields if f.key == field_key), None)
    if field is None or field.dm_only:
        raise HTTPException(404, "No such field the party could see")
    try:
        text, _ = knowledge.toggle_tease(entity["fields"].get(field_key, ""), index, str((await request.form()).get("tag", "")))
    except knowledge.PassageGone as exc:
        raise HTTPException(409, str(exc)) from None
    db.update_entity(conn, entity_id, entity["name"], dict(entity["fields"], **{field_key: text}))
    if request.headers.get("HX-Request"):
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


@router.post("/{type_key}/{entity_id}/reveal/{field_key}/{index}")
async def reveal_passage(request: Request, type_key: str, entity_id: int, field_key: str, index: int, conn: Conn):
    """Reveal one ||secret|| passage of an entry the party already knows: its bars go, and the reveal
    is logged for the session."""
    if is_party_view(request):
        raise HTTPException(404, "Not available in party view")
    etype = entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    field = next((f for f in etype.fields if f.key == field_key), None)
    if field is None or field.dm_only:
        raise HTTPException(404, "No such revealable field")
    if not db.visible_to_party(entity):
        raise HTTPException(409, "Reveal the entry itself first: the party can't see any of it yet")
    try:
        text, passage = knowledge.reveal_passage(entity["fields"].get(field_key, ""), index,
                                                 str((await request.form()).get("tag", "")))
    except knowledge.PassageGone as exc:
        raise HTTPException(409, str(exc)) from None
    db.update_entity(conn, entity_id, entity["name"], dict(entity["fields"], **{field_key: text}))
    db.record_reveal(conn, entity, field.label, passage)
    if request.headers.get("HX-Request"):
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


@router.post("/{type_key}/{entity_id}/ai-context", response_class=HTMLResponse)
async def change_ai_context(request: Request, type_key: str, entity_id: int, conn: Conn):
    etype = entity_type_or_404(type_key)
    entity_or_404(conn, type_key, entity_id)
    db.set_ai_context(conn, entity_id, (await request.form()).get("included") == "1")
    return render_status(request, conn, etype, entity_id)


@router.post("/{type_key}/{entity_id}/links", response_class=HTMLResponse)
async def create_link(request: Request, type_key: str, entity_id: int, conn: Conn):
    etype = entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    form = await request.form()
    try:
        target_id = int(str(form.get("to_id", "")))
    except ValueError:
        return render_links(request, conn, etype, entity, "Pick an entity to link to.", 422)
    if db.get_entity(conn, target_id) is None:
        return render_links(request, conn, etype, entity, "That entity no longer exists.", 422)
    try:
        added = db.add_link(conn, entity_id, target_id, str(form.get("label", "")), form.get("known") == "1")
    except ValueError as exc:
        return render_links(request, conn, etype, entity, str(exc), 422)
    return render_links(request, conn, etype, entity, None if added else "That link already exists.")


@router.post("/{type_key}/{entity_id}/links/{link_id}/known", response_class=HTMLResponse)
async def change_link_known(request: Request, type_key: str, entity_id: int, link_id: int, conn: Conn):
    etype = entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    link_of_entity_or_404(conn, entity_id, link_id)
    db.set_link_known(conn, link_id, (await request.form()).get("known") == "1")
    return render_links(request, conn, etype, entity)


@router.post("/{type_key}/{entity_id}/links/{link_id}/delete", response_class=HTMLResponse)
async def delete_link(request: Request, type_key: str, entity_id: int, link_id: int, conn: Conn):
    etype = entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    link_of_entity_or_404(conn, entity_id, link_id)
    db.remove_link(conn, link_id)
    return render_links(request, conn, etype, entity)
