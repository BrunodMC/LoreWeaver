"""Revealing an entry through the secrecy check: audit first, rule on the findings, then reveal."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, db
from .entities import Conn, entity_or_404, entity_type_or_404, render
from .generate import dm_only

router = APIRouter()


def refresh(request: Request, url: str):
    if request.headers.get("HX-Request"):
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return RedirectResponse(url, status_code=303)


@router.post("/{type_key}/{entity_id}/reveal-check")
async def start_check(request: Request, type_key: str, entity_id: int, conn: Conn):
    """'Reveal to party…': check the visible text for leaks before anything is revealed."""
    dm_only(request)
    entity_type_or_404(type_key)
    entity_or_404(conn, type_key, entity_id)
    open_audit = audit.latest_open(conn, "secrecy", entity_id)
    if open_audit:
        audit.dismiss(conn, open_audit)  # a fresh check replaces a stale one
    audit.start("secrecy", entity_id)
    return refresh(request, f"/{type_key}/{entity_id}")


@router.get("/{type_key}/{entity_id}/reveal-check/status", response_class=HTMLResponse)
async def check_status(request: Request, type_key: str, entity_id: int, conn: Conn):
    dm_only(request)
    task = audit.task_for("secrecy", entity_id)
    if task is None or task.status != "running":
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return render(request, "_audit_task.html", {"task": task, "url": f"/{type_key}/{entity_id}"})


def open_audit_or_404(conn, entity_id: int, audit_id: int) -> dict:
    found = audit.get(conn, audit_id)
    if found is None or found["entity_id"] != entity_id or found["status"] != "open":
        raise HTTPException(404, "No such open check (it may have been settled already)")
    return found


@router.post("/{type_key}/{entity_id}/audits/{audit_id}/apply")
async def apply(request: Request, type_key: str, entity_id: int, audit_id: int, conn: Conn):
    """Hide the ticked passages; with action=reveal, then reveal the entry and log it for the session."""
    dm_only(request)
    entity_type_or_404(type_key)
    entity_or_404(conn, type_key, entity_id)
    found = open_audit_or_404(conn, entity_id, audit_id)
    form = await request.form()
    audit.apply(conn, found, set(form.getlist("finding")))
    entity = db.get_entity(conn, entity_id)
    if form.get("action") == "reveal" and not entity["known_to_party"]:
        db.set_known(conn, entity_id, True)
        db.record_reveal(conn, entity)
    return RedirectResponse(f"/{type_key}/{entity_id}?audit={audit_id}", status_code=303)


@router.post("/{type_key}/{entity_id}/audits/{audit_id}/dismiss")
async def dismiss(request: Request, type_key: str, entity_id: int, audit_id: int, conn: Conn):
    dm_only(request)
    entity_type_or_404(type_key)
    entity_or_404(conn, type_key, entity_id)
    audit.dismiss(conn, open_audit_or_404(conn, entity_id, audit_id))
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)
