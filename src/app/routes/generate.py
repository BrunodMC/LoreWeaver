"""The generate form, its anchor picker, and the job status page. DM view only."""
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import context, db, generation, search as search_mod
from ..entity_types import ENTITY_TYPES
from .entities import Conn, is_party_view, render

router = APIRouter(prefix="/generate")

ANCHOR_RESULTS = 8


def dm_only(request: Request) -> None:
    if is_party_view(request):
        raise HTTPException(404, "Not available in party view")


def context_ids(conn) -> set[int]:
    return {e["id"] for e in context.context_entities(conn)}


def render_form(request: Request, conn, *, type_key: str = "npc", brief: str = "",
                anchor_ids: list[int] = (), deep: bool = False, error: str | None = None,
                status_code: int = 200) -> HTMLResponse:
    allowed = context_ids(conn)
    anchors = [e for e in (db.get_entity(conn, i) for i in dict.fromkeys(anchor_ids)) if e and e["id"] in allowed]
    return render(request, "generate.html", {
        "types": [ENTITY_TYPES[k] for k in generation.GENERATABLE],
        "type_key": type_key, "brief": brief, "anchors": anchors, "deep": deep, "error": error,
    }, status_code)


def parse_ids(values) -> list[int]:
    ids = []
    for value in values:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


@router.get("", response_class=HTMLResponse)
async def form(request: Request, conn: Conn, type: str = "npc", brief: str = "", deep: bool = False):
    dm_only(request)
    if type not in generation.GENERATABLE:
        type = "npc"
    anchors = parse_ids(request.query_params.getlist("anchor"))
    return render_form(request, conn, type_key=type, brief=brief, anchor_ids=anchors, deep=deep)


@router.get("/anchors", response_class=HTMLResponse)
async def anchor_search(request: Request, conn: Conn, q: str = ""):
    """Type-ahead results: entries the model will actually see (canon and opted-in drafts)."""
    dm_only(request)
    allowed = context_ids(conn)
    results = [r for r in search_mod.search(conn, q, ("canon", "draft")) if r.entity["id"] in allowed]
    return render(request, "_anchor_results.html", {"results": results[:ANCHOR_RESULTS], "query": q})


@router.get("/anchor-chip/{entity_id}", response_class=HTMLResponse)
async def anchor_chip(request: Request, entity_id: int, conn: Conn):
    dm_only(request)
    entity = db.get_entity(conn, entity_id)
    if entity is None or entity_id not in context_ids(conn):
        raise HTTPException(404, "Not an entry the AI can see")
    return render(request, "_anchor_chip.html", {"anchor": entity})


@router.post("", response_class=HTMLResponse)
async def start(request: Request, conn: Conn):
    dm_only(request)
    form_data = await request.form()
    type_key = str(form_data.get("type", ""))
    brief = str(form_data.get("brief", "")).strip()
    anchor_ids = list(dict.fromkeys(parse_ids(form_data.getlist("anchor"))))
    deep = form_data.get("deep") == "1"

    error = None
    if type_key not in generation.GENERATABLE:
        error, type_key = "Pick what to generate.", "npc"
    elif not brief and not anchor_ids:
        error = "Write a brief, pick an anchor, or both."
    elif any(a not in context_ids(conn) for a in anchor_ids):
        error = "An anchor isn't in the AI context any more (archived, or a draft that was opted out)."
    if error:
        return render_form(request, conn, type_key=type_key, brief=brief, anchor_ids=anchor_ids,
                           deep=deep, error=error, status_code=422)

    job = generation.start(generation.GenerationRequest(type_key, brief, anchor_ids, deep))
    return RedirectResponse(f"/generate/jobs/{job.id}", status_code=303)


def retry_url(job: generation.Job) -> str:
    req = job.request
    params = [("type", req.entity_type), ("brief", req.brief)] + [("anchor", a) for a in req.anchor_ids]
    if req.deep:
        params.append(("deep", "true"))
    return "/generate?" + urlencode(params)


def job_or_404(job_id: str) -> generation.Job:
    job = generation.get_job(job_id)
    if job is None:
        raise HTTPException(404, "No such generation (it may have been lost to a server restart)")
    return job


def draft_url(job: generation.Job) -> str:
    return f"/{job.request.entity_type}/{job.entity_id}"


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str):
    dm_only(request)
    job = job_or_404(job_id)
    if job.status == "done":
        return RedirectResponse(draft_url(job), status_code=303)
    return render(request, "generate_job.html", {"job": job, "retry_url": retry_url(job),
                                                 "etype": ENTITY_TYPES[job.request.entity_type]})


@router.get("/jobs/{job_id}/status", response_class=HTMLResponse)
async def job_status(request: Request, job_id: str):
    """Polled by the job page. When the draft lands, HTMX follows HX-Redirect to it."""
    dm_only(request)
    job = job_or_404(job_id)
    if job.status == "done":
        return HTMLResponse("", headers={"HX-Redirect": draft_url(job)})
    return render(request, "_job_status.html", {"job": job, "retry_url": retry_url(job)})
