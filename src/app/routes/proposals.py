"""Proposed changes to an existing entry: ask ("Time passes…"), watch it think, then apply what you tick."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import proposals
from .audits import refresh
from .entities import Conn, entity_or_404, entity_type_or_404, render
from .generate import dm_only

router = APIRouter()


@router.post("/{type_key}/{entity_id}/time-passes")
async def time_passes(request: Request, type_key: str, entity_id: int, conn: Conn):
    dm_only(request)
    entity_type_or_404(type_key)
    entity_or_404(conn, type_key, entity_id)
    if type_key not in proposals.KINDS["time_passes"].types:
        raise HTTPException(404, "Time passes doesn't apply to this kind of entry")
    form = await request.form()
    elapsed = str(form.get("elapsed", "")).strip()
    if not elapsed:
        raise HTTPException(422, "Say how much time has passed")
    req = {"elapsed": elapsed, "events": str(form.get("events", "")).strip(),
           "notes": str(form.get("notes", "")).strip()}
    proposals.start("time_passes", entity_id, req, str(form.get("model", "")) or None)
    return refresh(request, f"/{type_key}/{entity_id}")


@router.get("/{type_key}/{entity_id}/proposals/status", response_class=HTMLResponse)
async def status(request: Request, type_key: str, entity_id: int):
    dm_only(request)
    task = proposals.task_for(entity_id)
    if task is None or task.status != "running":
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return render(request, "_proposal_task.html", {"task": task, "url": f"/{type_key}/{entity_id}"})


def open_or_404(conn, entity_id: int, proposal_id: int) -> dict:
    found = proposals.get(conn, proposal_id)
    if found is None or found["entity_id"] != entity_id or found["status"] != "open":
        raise HTTPException(404, "No such open proposal (it may have been settled already)")
    return found


@router.post("/{type_key}/{entity_id}/proposals/{proposal_id}/apply")
async def apply(request: Request, type_key: str, entity_id: int, proposal_id: int, conn: Conn):
    dm_only(request)
    entity_type_or_404(type_key)
    entity_or_404(conn, type_key, entity_id)
    found = open_or_404(conn, entity_id, proposal_id)
    form = await request.form()
    links = {int(v) for v in form.getlist("link") if str(v).isdigit()}
    conflicts = proposals.apply(conn, found, set(form.getlist("field")), links)
    suffix = f"?kept={','.join(conflicts)}" if conflicts else ""
    return RedirectResponse(f"/{type_key}/{entity_id}{suffix}", status_code=303)


@router.post("/{type_key}/{entity_id}/proposals/{proposal_id}/dismiss")
async def dismiss(request: Request, type_key: str, entity_id: int, proposal_id: int, conn: Conn):
    dm_only(request)
    entity_type_or_404(type_key)
    entity_or_404(conn, type_key, entity_id)
    proposals.dismiss(conn, open_or_404(conn, entity_id, proposal_id))
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)
