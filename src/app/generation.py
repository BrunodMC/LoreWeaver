"""Running a generation: bible in, checked draft out, stored as a draft entity with its version history.

A generation takes a minute or two, so routes start it as a background job and poll. Jobs live in
memory: this is a one-GM local app, and a job lost to a server restart is simply started again.
"""
import logging
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field

from . import context, db, llm

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # one retry, told what was wrong; after that the GM sees the problems
GENERATABLE = ("npc", "location", "faction", "arc", "item", "adversary")  # PCs belong to players; sessions to play
JOBS_TO_KEEP = 50


class GenerationFailed(Exception):
    pass


@dataclass
class GenerationRequest:
    entity_type: str
    brief: str = ""
    anchor_ids: list[int] = field(default_factory=list)
    deep: bool = False
    notes: str = ""                # regeneration only
    entity_id: int | None = None   # regeneration only: the draft being replaced


def _current_version(entity: dict) -> dict:
    return {"name": entity["name"], "fields": entity["fields"]}


def run(conn, request: GenerationRequest) -> int:
    """Generate (or regenerate) a draft and store it. Returns the draft's entity id."""
    if request.entity_type not in GENERATABLE:
        raise GenerationFailed(f"Can't generate a {request.entity_type}.")
    entity = None
    if request.entity_id is not None:
        entity = db.get_entity(conn, request.entity_id)
        if entity is None or entity["type"] != request.entity_type or entity["status"] != "draft":
            raise GenerationFailed("Only an existing draft can be regenerated.")

    entities = [e for e in context.context_entities(conn) if e["id"] != request.entity_id]
    by_id = {e["id"]: e for e in entities}
    missing = [a for a in request.anchor_ids if a not in by_id]
    if missing:
        raise GenerationFailed(f"Anchor entries {missing} aren't in the AI context (canon or opted-in drafts).")
    anchors = [by_id[a] for a in dict.fromkeys(request.anchor_ids)]

    current = _current_version(entity) if entity else None
    system = context.system_prompt(conn, entities)
    prompt = context.user_prompt(request.entity_type, request.brief, anchors, current, request.notes)
    schema = context.output_schema(request.entity_type, partial=current is not None)

    attempt_prompt = prompt
    for _ in range(MAX_ATTEMPTS):
        result = llm.generate(system, attempt_prompt, schema, deep=request.deep)
        draft = context.check(result.data, request.entity_type, entities, anchors, current)
        if not draft.problems:
            break
        attempt_prompt = context.retry_prompt(prompt, draft.problems)
    else:
        raise GenerationFailed("The draft still had problems after a retry:\n"
                               + "\n".join(f"- {p}" for p in draft.problems))

    meta = dict(brief=request.brief, notes=request.notes, anchors=[a["id"] for a in anchors],
                model=result.model, usage=result.usage)
    if entity is None:
        entity_id = db.create_entity(conn, request.entity_type, draft.name, draft.fields, status="draft")
        db.record_generation(conn, entity_type=request.entity_type, kind="generated", entity_id=entity_id,
                             output=draft.as_output(), **meta)
        return entity_id

    lineage = snapshot_if_edited(conn, entity)
    db.update_entity(conn, entity["id"], draft.name, draft.fields)
    db.record_generation(conn, entity_type=request.entity_type, kind="generated", entity_id=entity["id"],
                         lineage_id=lineage, output=draft.as_output(), **meta)
    return entity["id"]


def snapshot_if_edited(conn, entity: dict) -> int:
    """Before a draft is overwritten, keep its current state in the history unless it's already there
    (so the GM's hand edits survive). Returns the lineage id, starting one for a hand-made draft."""
    lineage = db.lineage_for_entity(conn, entity["id"])
    versions = db.lineage_versions(conn, lineage) if lineage else []
    latest = versions[-1]["output"] if versions else None
    state = _current_version(entity)
    if latest is None or {"name": latest.get("name"), "fields": latest.get("fields")} != state:
        snap = db.record_generation(conn, entity_type=entity["type"], kind="edit", entity_id=entity["id"],
                                    lineage_id=lineage, output=state)
        lineage = lineage or snap
    return lineage


# ── Background jobs ────────────────────────────────────────────────────────

@dataclass
class Job:
    id: str
    request: GenerationRequest
    status: str = "running"  # running | done | failed
    entity_id: int | None = None
    error: str = ""
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> int:
        return int(time.monotonic() - self.started)


JOBS: dict[str, Job] = {}
_jobs_lock = threading.Lock()
INLINE = False  # tests run jobs synchronously


def _work(job: Job) -> None:
    try:
        with closing(db.get_connection()) as conn:
            job.entity_id = run(conn, job.request)
        job.status = "done"
    except (GenerationFailed, llm.LLMError) as exc:
        job.error, job.status = str(exc), "failed"
    except Exception as exc:  # a crash must still end the job, or the page polls forever
        log.exception("Generation job %s crashed", job.id)
        job.error, job.status = f"Unexpected error: {exc}", "failed"


def start(request: GenerationRequest) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], request=request)
    with _jobs_lock:
        JOBS[job.id] = job
        for old in list(JOBS)[:-JOBS_TO_KEEP]:
            del JOBS[old]
    if INLINE:
        _work(job)
    else:
        threading.Thread(target=_work, args=(job,), daemon=True, name=f"generate-{job.id}").start()
    return job


def get_job(job_id: str) -> Job | None:
    return JOBS.get(job_id)
