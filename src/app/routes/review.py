"""Reviewing an AI draft: promote with chosen links, regenerate with notes, discard, browse versions."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import context, db, generation
from .entities import Conn, entity_or_404, entity_type_or_404, render
from .generate import dm_only

router = APIRouter()


def reviewable(request: Request, conn, type_key: str, entity_id: int,
               drafts_only: bool = True) -> tuple[dict, dict]:
    """The entry and its generation history. Changing it (promote, restore...) needs a draft;
    reading the history works for any entry that came from the generator."""
    dm_only(request)
    entity_type_or_404(type_key)
    entity = entity_or_404(conn, type_key, entity_id)
    if drafts_only and entity["status"] != "draft":
        raise HTTPException(409, "Only drafts are under review")
    review = generation.review_state(conn, entity)
    if review is None:
        raise HTTPException(404, "This entry has no generation history")
    return entity, review


def version_or_404(review: dict, version_id: int) -> dict:
    version = next((v for v in review["versions"] if v["id"] == version_id), None)
    if version is None:
        raise HTTPException(404, "No such version of this draft")
    return version


@router.get("/drafts", response_class=HTMLResponse)
async def inbox(request: Request, conn: Conn):
    """Every AI draft awaiting review, so nothing generated at 1 a.m. becomes forgotten furniture."""
    dm_only(request)
    return render(request, "drafts.html", {
        "pending": generation.pending_drafts(conn), "running": generation.running_jobs(),
    })


@router.get("/{type_key}/{entity_id}/versions/{version_id}", response_class=HTMLResponse)
async def version_view(request: Request, type_key: str, entity_id: int, version_id: int, conn: Conn):
    entity, review = reviewable(request, conn, type_key, entity_id, drafts_only=False)
    return render(request, "_version_view.html", {
        "etype": entity_type_or_404(type_key), "entity": entity, "review": review,
        "version": version_or_404(review, version_id),
    })


@router.get("/{type_key}/{entity_id}/history", response_class=HTMLResponse)
async def history(request: Request, type_key: str, entity_id: int, conn: Conn):
    """How an entry was drafted: every attempt, edit and Claude's notes, after the review is over."""
    entity, review = reviewable(request, conn, type_key, entity_id, drafts_only=False)
    return render(request, "history.html", {
        "etype": entity_type_or_404(type_key), "entity": entity, "review": review,
        "version": review["current"] or review["versions"][-1],
    })


@router.post("/{type_key}/{entity_id}/versions/{version_id}/restore")
async def restore_version(request: Request, type_key: str, entity_id: int, version_id: int, conn: Conn):
    entity, review = reviewable(request, conn, type_key, entity_id)
    generation.restore(conn, entity, version_or_404(review, version_id))
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


@router.post("/{type_key}/{entity_id}/promote")
async def promote(request: Request, type_key: str, entity_id: int, conn: Conn):
    entity, review = reviewable(request, conn, type_key, entity_id)
    form = await request.form()
    proposals = review["source"]["output"].get("proposed_links", []) if review["source"] else []
    chosen = set()
    for value in form.getlist("link"):
        try:
            chosen.add(int(value))
        except ValueError:
            continue
    ai_notes = review["source"]["output"].get("dm_notes", "") if review["source"] else ""
    keep = ai_notes if form.get("keep_notes") == "1" else ""
    generation.promote(conn, entity, [link for i, link in enumerate(proposals) if i in chosen], keep)
    return RedirectResponse(f"/{type_key}/{entity_id}", status_code=303)


@router.post("/{type_key}/{entity_id}/discard")
async def discard(request: Request, type_key: str, entity_id: int, conn: Conn):
    entity, _ = reviewable(request, conn, type_key, entity_id)
    db.delete_entity(conn, entity["id"])
    return RedirectResponse(f"/{type_key}?status=draft", status_code=303)


@router.post("/{type_key}/{entity_id}/regenerate")
async def regenerate(request: Request, type_key: str, entity_id: int, conn: Conn):
    entity, review = reviewable(request, conn, type_key, entity_id)
    form = await request.form()
    visible = {e["id"] for e in context.context_entities(conn)}
    anchors = [a for a in review["anchors"] if a in visible]  # an anchor archived since then is dropped
    job = generation.start(generation.GenerationRequest(
        type_key, review["brief"], anchors, deep=form.get("deep") == "1",
        notes=str(form.get("notes", "")).strip(), entity_id=entity["id"],
    ))
    return RedirectResponse(f"/generate/jobs/{job.id}", status_code=303)
