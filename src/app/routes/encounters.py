"""Composing an encounter: brief → spines → cast pool (with the Battle Point meter) → drafts. DM view only."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import battle, composite, context, db, generation, search as search_mod
from .entities import Conn, render
from .generate import context_ids, dm_only, parse_ids

router = APIRouter(prefix="/encounters")

CAST_RESULTS = 8


def default_pcs(conn) -> int:
    count = conn.execute("SELECT COUNT(*) FROM entities WHERE type = 'pc' AND status != 'archived'").fetchone()[0]
    return count or 4


def workspace_or_404(conn, composite_id: int) -> dict:
    found = db.get_composite(conn, composite_id)
    if found is None:
        raise HTTPException(404, "No such encounter workspace")
    return found


def back(composite_id: int) -> RedirectResponse:
    return RedirectResponse(f"/encounters/{composite_id}", status_code=303)


def conflict(exc: Exception) -> None:
    raise HTTPException(409, str(exc)) from exc


def parse_pcs(value, fallback: int) -> int:
    try:
        return max(1, min(12, int(value)))
    except (TypeError, ValueError):
        return fallback


# ── Starting ───────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new(request: Request, conn: Conn, brief: str = ""):
    dm_only(request)
    allowed = context_ids(conn)
    anchors = [e for e in (db.get_entity(conn, i) for i in parse_ids(request.query_params.getlist("anchor")))
               if e and e["id"] in allowed]
    return render(request, "compose.html", {"brief": brief, "anchors": anchors, "pcs": default_pcs(conn),
                                            "error": None})


@router.post("", response_class=HTMLResponse)
async def create(request: Request, conn: Conn):
    dm_only(request)
    form = await request.form()
    brief = str(form.get("brief", "")).strip()
    allowed = context_ids(conn)
    anchor_ids = [a for a in dict.fromkeys(parse_ids(form.getlist("anchor"))) if a in allowed]
    pcs = parse_pcs(form.get("pcs"), default_pcs(conn))
    if not brief:
        anchors = [db.get_entity(conn, a) for a in anchor_ids]
        return render(request, "compose.html", {
            "brief": brief, "anchors": anchors, "pcs": pcs,
            "error": "Write a brief, even a line: where the party is and what's going on."}, 422)
    composite_id = db.create_composite(conn, brief, anchor_ids, pcs)
    if form.get("action") == "skip":  # the brief already is the premise
        composite.choose_spine(conn, composite_id, brief)
        composite.start_task(composite_id, "pool")
    else:
        composite.start_task(composite_id, "spines")
    return back(composite_id)


# ── The workspace ──────────────────────────────────────────────────────────

def workspace_context(conn, comp: dict) -> dict:
    meter = generation.composite_meter(comp)
    groups = []
    for kind in context.POOL_TYPES:
        members = [m for m in comp["pool"] if m["type"] == kind]
        if members:
            groups.append((kind, members))
    return {
        "comp": comp, "task": composite.TASKS.get(comp["id"]), "meter": meter, "groups": groups,
        "toggles": battle.TOGGLES, "roles": context.ADVERSARY_ROLES,
        "anchors": [e for e in (db.get_entity(conn, a) for a in comp["anchors"]) if e],
        "progress": composite.progress(conn, comp) if comp["stage"] in ("drafting", "done") else None,
        "ticked": len(generation.ticked_cast(comp)),
    }


@router.get("/{composite_id}", response_class=HTMLResponse)
async def workspace(request: Request, composite_id: int, conn: Conn):
    dm_only(request)
    comp = workspace_or_404(conn, composite_id)
    return render(request, "encounter_workspace.html", workspace_context(conn, comp))


@router.get("/{composite_id}/status", response_class=HTMLResponse)
async def status(request: Request, composite_id: int, conn: Conn):
    """Polled while a spine, pool or sheet call runs; reloads the page when it ends."""
    dm_only(request)
    workspace_or_404(conn, composite_id)
    task = composite.TASKS.get(composite_id)
    if task is None or task.status != "running":
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return render(request, "_composite_task.html", {"task": task, "comp_id": composite_id})


@router.get("/{composite_id}/progress", response_class=HTMLResponse)
async def progress(request: Request, composite_id: int, conn: Conn):
    """Polled while members are drafted; each row lands as its draft does."""
    dm_only(request)
    comp = workspace_or_404(conn, composite_id)
    return render(request, "_composite_progress.html", {"comp": comp, "progress": composite.progress(conn, comp)})


@router.post("/{composite_id}/spines")
async def respine(request: Request, composite_id: int, conn: Conn):
    dm_only(request)
    workspace_or_404(conn, composite_id)
    db.update_composite(conn, composite_id, stage="spines")
    try:
        composite.start_task(composite_id, "spines")
    except composite.CompositeError as exc:
        conflict(exc)
    return back(composite_id)


@router.post("/{composite_id}/choose")
async def choose(request: Request, composite_id: int, conn: Conn):
    dm_only(request)
    workspace_or_404(conn, composite_id)
    form = await request.form()
    try:
        composite.choose_spine(conn, composite_id, str(form.get("spine", "")))
        composite.start_task(composite_id, "pool")
    except composite.CompositeError as exc:
        conflict(exc)
    return back(composite_id)


@router.post("/{composite_id}/back-to-spines")
async def back_to_spines(request: Request, composite_id: int, conn: Conn):
    """Change the premise: back to the proposals (the pool is replaced when a premise is chosen)."""
    dm_only(request)
    workspace_or_404(conn, composite_id)
    db.update_composite(conn, composite_id, stage="spines")
    return back(composite_id)


@router.post("/{composite_id}/pool", response_class=HTMLResponse)
async def save_pool(request: Request, composite_id: int, conn: Conn):
    """Autosaved on every change (HTMX gets the meter back); a plain submit saves and reloads."""
    dm_only(request)
    workspace_or_404(conn, composite_id)
    comp = composite.save_pool(conn, composite_id, await request.form())
    if request.headers.get("HX-Request"):
        return render(request, "_meter.html", {"meter": generation.composite_meter(comp), "comp": comp,
                                               "ticked": len(generation.ticked_cast(comp))})
    return back(composite_id)


@router.post("/{composite_id}/more")
async def more(request: Request, composite_id: int, conn: Conn):
    dm_only(request)
    workspace_or_404(conn, composite_id)
    composite.save_pool(conn, composite_id, await request.form())
    try:
        composite.start_task(composite_id, "more")
    except composite.CompositeError as exc:
        conflict(exc)
    return back(composite_id)


@router.get("/{composite_id}/cast-search", response_class=HTMLResponse)
async def cast_search(request: Request, composite_id: int, conn: Conn, q: str = ""):
    dm_only(request)
    workspace_or_404(conn, composite_id)
    allowed = context_ids(conn)
    results = [r for r in search_mod.search(conn, q, ("canon", "draft"))
               if r.entity["id"] in allowed and r.entity["type"] in context.POOL_TYPES]
    return render(request, "_cast_results.html", {"results": results[:CAST_RESULTS], "query": q,
                                                  "comp_id": composite_id})


@router.post("/{composite_id}/cast/{entity_id}")
async def cast_existing(request: Request, composite_id: int, entity_id: int, conn: Conn):
    dm_only(request)
    workspace_or_404(conn, composite_id)
    try:
        composite.add_existing(conn, composite_id, entity_id)
    except composite.CompositeError as exc:
        conflict(exc)
    if request.headers.get("HX-Request"):
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return back(composite_id)


@router.post("/{composite_id}/draft")
async def draft(request: Request, composite_id: int, conn: Conn):
    dm_only(request)
    workspace_or_404(conn, composite_id)
    composite.save_pool(conn, composite_id, await request.form())
    try:
        composite.start_drafting(conn, composite_id)
    except composite.CompositeError as exc:
        conflict(exc)
    return back(composite_id)


@router.post("/{composite_id}/redraft")
async def redraft(request: Request, composite_id: int, conn: Conn):
    """The sheet call failed: try it again with the cast as saved (no form, so no ticks are lost)."""
    dm_only(request)
    comp = workspace_or_404(conn, composite_id)
    if comp["encounter_id"]:
        raise HTTPException(409, "The encounter sheet is already drafted")
    try:
        composite.start_drafting(conn, composite_id)
    except composite.CompositeError as exc:
        conflict(exc)
    return back(composite_id)


@router.post("/{composite_id}/back-to-cast")
async def back_to_cast(request: Request, composite_id: int, conn: Conn):
    dm_only(request)
    comp = workspace_or_404(conn, composite_id)
    if comp["encounter_id"] or composite.running_task(composite_id):
        raise HTTPException(409, "The encounter is already drafted")
    db.update_composite(conn, composite_id, stage="pool")
    return back(composite_id)


@router.post("/{composite_id}/members/{key}/retry")
async def retry(request: Request, composite_id: int, key: str, conn: Conn):
    dm_only(request)
    workspace_or_404(conn, composite_id)
    try:
        composite.retry_member(conn, composite_id, key)
    except composite.CompositeError as exc:
        conflict(exc)
    return back(composite_id)


def sheet_or_404(conn, comp: dict) -> dict:
    sheet = db.get_entity(conn, comp["encounter_id"]) if comp["encounter_id"] else None
    if sheet is None:
        raise HTTPException(404, "This workspace has no encounter draft")
    return sheet


@router.post("/{composite_id}/promote-all")
async def promote_all(request: Request, composite_id: int, conn: Conn):
    dm_only(request)
    sheet = sheet_or_404(conn, workspace_or_404(conn, composite_id))
    try:
        composite.promote_all(conn, sheet)
    except composite.CompositeError as exc:
        conflict(exc)
    return RedirectResponse(f"/encounter/{sheet['id']}", status_code=303)


@router.post("/{composite_id}/discard-all")
async def discard_all(request: Request, composite_id: int, conn: Conn):
    dm_only(request)
    sheet = sheet_or_404(conn, workspace_or_404(conn, composite_id))
    try:
        composite.discard_all(conn, sheet)
    except composite.CompositeError as exc:
        conflict(exc)
    return RedirectResponse("/drafts", status_code=303)


@router.post("/{composite_id}/discard")
async def discard(request: Request, composite_id: int, conn: Conn):
    """Throw away a workspace that hasn't been drafted yet. Drafted encounters are discarded as drafts."""
    dm_only(request)
    comp = workspace_or_404(conn, composite_id)
    if comp["stage"] not in ("spines", "pool") or composite.running_task(composite_id):
        raise HTTPException(409, "Only a workspace that isn't drafting can be discarded")
    db.delete_composite(conn, composite_id)
    return RedirectResponse("/drafts", status_code=303)
