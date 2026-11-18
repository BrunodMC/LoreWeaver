"""Portrait images: served under the party's visibility rules, uploaded and removed by the DM only."""
from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from .. import db, portraits
from .entities import Conn, entity_or_404, entity_type_or_404, is_party_view
from .generate import dm_only

router = APIRouter()


@router.get("/portrait/{entity_id}")
async def show(request: Request, entity_id: int, conn: Conn):
    """The image itself. In party view it exists only if the party may see the entry at all."""
    entity = db.get_entity(conn, entity_id)
    if entity is None or (is_party_view(request) and not db.visible_to_party(entity)):
        raise HTTPException(404, "No such portrait")
    path = portraits.find(entity_id)
    if path is None:
        raise HTTPException(404, "No such portrait")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@router.post("/{type_key}/{entity_id}/portrait")
async def upload(request: Request, type_key: str, entity_id: int, conn: Conn):
    dm_only(request)
    entity_type_or_404(type_key)
    entity_or_404(conn, type_key, entity_id)
    form = await request.form()
    image = form.get("image")
    if not isinstance(image, UploadFile) and not hasattr(image, "read"):
        raise HTTPException(422, "Choose an image to upload.")
    try:
        portraits.save(entity_id, image.content_type or "", await image.read())
    except portraits.PortraitError as exc:
        raise HTTPException(422, str(exc)) from None
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


@router.post("/{type_key}/{entity_id}/portrait/remove")
async def remove(request: Request, type_key: str, entity_id: int, conn: Conn):
    dm_only(request)
    entity_type_or_404(type_key)
    entity_or_404(conn, type_key, entity_id)
    portraits.remove(entity_id)
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


@router.post("/settings/portrait-style")
async def save_style(request: Request, conn: Conn):
    """The style every portrait prompt starts with, shared across the campaign."""
    dm_only(request)
    form = await request.form()
    db.set_setting(conn, portraits.STYLE_KEY, str(form.get("style", "")))
    back = str(form.get("next", "/"))
    return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/", status_code=303)
