"""Marking a death, revealing it, undoing it, and rippling it through the linked entries."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import db, deaths
from .entities import Conn, entity_or_404, entity_type_or_404
from .generate import dm_only

router = APIRouter()


def mortal_or_404(request: Request, conn, type_key: str, entity_id: int) -> dict:
    dm_only(request)
    entity_type_or_404(type_key)
    if type_key not in deaths.DEATH_TYPES:
        raise HTTPException(404, "Only people and creatures can die")
    entity = entity_or_404(conn, type_key, entity_id)
    if deaths.mortality(conn, entity) != "mortal" and not entity.get("deceased"):
        raise HTTPException(409, "Only an individual can die (and one with an NPC page dies there)")
    return entity


def picked(form) -> set[int]:
    return {int(v) for v in form.getlist("target") if str(v).isdigit()}


@router.post("/{type_key}/{entity_id}/deceased")
async def mark_deceased(request: Request, type_key: str, entity_id: int, conn: Conn):
    entity = mortal_or_404(request, conn, type_key, entity_id)
    form = await request.form()
    deaths.mark(conn, entity, str(form.get("how", "")), form.get("party_knows") == "1")
    chosen = picked(form)
    if chosen:
        deaths.start_ripple(conn, db.get_entity(conn, entity_id), str(form.get("model", "")) or None, chosen)
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


@router.post("/{type_key}/{entity_id}/deceased/reveal")
async def reveal_death(request: Request, type_key: str, entity_id: int, conn: Conn):
    deaths.reveal(conn, mortal_or_404(request, conn, type_key, entity_id))
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


@router.post("/{type_key}/{entity_id}/deceased/ripple")
async def ripple(request: Request, type_key: str, entity_id: int, conn: Conn):
    """Ripple a death marked earlier (or again, after new links)."""
    entity = mortal_or_404(request, conn, type_key, entity_id)
    if not entity.get("deceased"):
        raise HTTPException(409, "Mark them deceased first")
    form = await request.form()
    deaths.start_ripple(conn, entity, str(form.get("model", "")) or None, picked(form))
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


@router.post("/{type_key}/{entity_id}/alive")
async def alive(request: Request, type_key: str, entity_id: int, conn: Conn):
    mortal_or_404(request, conn, type_key, entity_id)
    deaths.undo(conn, entity_id)
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)
