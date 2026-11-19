"""Unlinked mentions: link them with a relation of your choosing, or rule them out."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, mentions
from .entities import Conn, render
from .generate import dm_only

router = APIRouter()


def back(form) -> str:
    target = str(form.get("next", "/mentions"))
    return target if target.startswith("/") and not target.startswith("//") else "/mentions"


def entity_id_or_422(form, key: str) -> int:
    try:
        return int(str(form.get(key, "")))
    except ValueError:
        raise HTTPException(422, f"Missing {key}") from None


@router.get("/mentions", response_class=HTMLResponse)
async def page(request: Request, conn: Conn):
    dm_only(request)
    found = mentions.scan(conn)
    groups: dict[int, dict] = {}
    for m in found:
        groups.setdefault(m.source["id"], {"source": m.source, "found": []})["found"].append(m)
    return render(request, "mentions.html", {"groups": list(groups.values()), "total": len(found),
                                             "labels": db.relation_labels(conn)})


@router.post("/mentions/link")
async def link(request: Request, conn: Conn):
    dm_only(request)
    form = await request.form()
    source, target = entity_id_or_422(form, "source_id"), entity_id_or_422(form, "target_id")
    label = " ".join(str(form.get("label", "")).split())
    if not label:
        raise HTTPException(422, "Give the link a relation, like 'works with'")
    if db.get_entity(conn, source) is None or db.get_entity(conn, target) is None:
        raise HTTPException(404, "That entry no longer exists")
    a, b = (target, source) if form.get("direction") == "reverse" else (source, target)
    db.add_link(conn, a, b, label, form.get("known") == "1")
    return RedirectResponse(back(form), status_code=303)


@router.post("/mentions/dismiss")
async def dismiss(request: Request, conn: Conn):
    """scope=pair: not a link; scope=target: never suggest links to this entry at all."""
    dm_only(request)
    form = await request.form()
    target = entity_id_or_422(form, "target_id")
    source = None if form.get("scope") == "target" else entity_id_or_422(form, "source_id")
    mentions.dismiss(conn, source, target)
    return RedirectResponse(back(form), status_code=303)
