import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import db, knowledge
from ..entity_types import ENTITY_TYPES, STATUSES, EntityType

def local_time(utc_value: str) -> str:
    """SQLite stores UTC ('YYYY-MM-DD HH:MM:SS'); show it in this machine's local time."""
    utc = datetime.strptime(utc_value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return utc.astimezone().strftime("%Y-%m-%d %H:%M")


templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
templates.env.globals["entity_types"] = ENTITY_TYPES
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


def unclosed_secret_error(etype: EntityType, form) -> str | None:
    labels = [f.label for f in etype.fields if knowledge.has_unclosed_secret(str(form.get(f.key, "")))]
    if not labels:
        return None
    return (f"Unclosed || secret marker in: {', '.join(labels)}. "
            "Secrets must be wrapped on both sides, like ||this||.")


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


def render_status(request: Request, conn: sqlite3.Connection, etype: EntityType, entity_id: int) -> HTMLResponse:
    return render(request, "_status_actions.html", {
        "etype": etype, "entity": db.get_entity(conn, entity_id), "statuses": STATUSES,
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
    return render_form(request, etype, None, {"name": "", "status": "canon", "known_to_party": 0, "fields": {}})


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
             else unclosed_secret_error(etype, form))
    if error:
        values = {"name": name, "status": status, "known_to_party": int(known), "fields": fields}
        return render_form(request, etype, None, values, error, 422)
    entity_id = db.create_entity(conn, type_key, name, fields, status, known)
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


@router.get("/{type_key}/{entity_id}", response_class=HTMLResponse)
async def detail(request: Request, type_key: str, entity_id: int, conn: Conn):
    etype = entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    party_view = is_party_view(request)
    if party_view and not db.visible_to_party(entity):
        return render(request, "unknown.html", {"etype": etype}, 404)
    return render(request, "detail.html", {
        "etype": etype,
        "entity": entity,
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
    error = "A name is required." if not name else unclosed_secret_error(etype, form)
    if error:
        return render_form(request, etype, entity, {**entity, "name": name, "fields": fields}, error, 422)
    db.update_entity(conn, entity_id, name, fields)
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
    entity_or_404(conn, type_key, entity_id)
    db.set_known(conn, entity_id, (await request.form()).get("known") == "1")
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
