"""Composite encounters: brief → three spines → a cast pool → the encounter sheet → member drafts.

Each arrow is a checkpoint the GM can edit. The spine and pool calls are single calls (one call asked
for distinct options spreads them apart; parallel calls converge). The sheet is drafted alone, so it
can be the shared source of truth; then the members are drafted in parallel, each reading the sheet.
The workspace lives in the composites table; only in-flight calls live in memory.
"""
import logging
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field

from . import battle, context, db, generation, knowledge, llm

log = logging.getLogger(__name__)

TASK_KINDS = {"spines": "Proposing premises", "pool": "Gathering a cast pool", "more": "Adding to the pool",
              "draft": "Drafting the encounter sheet"}


@dataclass
class Task:
    """One spine, pool or sheet call in flight for a composite."""
    composite_id: int
    kind: str
    status: str = "running"  # running | done | failed
    error: str = ""
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> int:
        return int(time.monotonic() - self.started)

    @property
    def label(self) -> str:
        return TASK_KINDS[self.kind]


TASKS: dict[int, Task] = {}                 # composite id → its latest task
MEMBER_JOBS: dict[int, dict[str, str]] = {}  # composite id → {pool key: generation job id}
_lock = threading.Lock()


class CompositeError(Exception):
    pass


def get(conn, composite_id: int) -> dict:
    composite = db.get_composite(conn, composite_id)
    if composite is None:
        raise CompositeError("No such encounter workspace.")
    return composite


def running_task(composite_id: int) -> Task | None:
    task = TASKS.get(composite_id)
    return task if task and task.status == "running" else None


# ── The calls ──────────────────────────────────────────────────────────────

def _anchors(composite: dict, entities: list[dict]) -> list[dict]:
    by_id = {e["id"]: e for e in entities}
    return [by_id[a] for a in composite["anchors"] if a in by_id]  # an anchor archived since is dropped


def _ask(conn, entities: list[dict], prompt: str, schema: dict, check) -> list[dict]:
    system = context.system_prompt(conn, entities)
    attempt = prompt
    for _ in range(generation.MAX_ATTEMPTS):
        result = llm.generate(system, attempt, schema)
        items, problems = check(result.data)
        if not problems:
            return items
        attempt = context.retry_prompt(prompt, problems)
    raise CompositeError("The answer still had problems after a retry:\n" + "\n".join(f"- {p}" for p in problems))


def propose_spines(conn, composite_id: int) -> None:
    composite = get(conn, composite_id)
    entities = context.context_entities(conn)
    prompt = context.spine_prompt(composite["brief"], _anchors(composite, entities), composite["pcs"])
    spines = _ask(conn, entities, prompt, context.spine_schema(), context.check_spines)
    db.update_composite(conn, composite_id, spines=spines, stage="spines")


def propose_pool(conn, composite_id: int, more: bool = False) -> None:
    composite = get(conn, composite_id)
    entities = context.context_entities(conn)
    existing = composite["pool"] if more else []
    prompt = context.pool_prompt(composite["brief"], _anchors(composite, entities), composite["pcs"],
                                 composite["spine"], existing)
    members = _ask(conn, entities, prompt, context.pool_schema(),
                   lambda data: context.check_pool(data, entities))
    taken = {(m["type"], m["name"].casefold()) for m in existing}
    fresh = []
    for m in members:
        if (m["type"], m["name"].casefold()) not in taken:
            taken.add((m["type"], m["name"].casefold()))
            fresh.append(dict(m, key=uuid.uuid4().hex[:8], ticked=False))
    db.update_composite(conn, composite_id, pool=existing + fresh, stage="pool")


def draft(conn, composite_id: int) -> int:
    """Draft the encounter sheet, then start every new member's draft in parallel. Returns the sheet's id."""
    composite = get(conn, composite_id)
    cast = generation.ticked_cast(composite)
    in_context = {e["id"] for e in context.context_entities(conn)}
    reused = [m["existing_id"] for m in cast if m.get("existing_id")]
    anchors = [a for a in dict.fromkeys(composite["anchors"] + reused) if a in in_context]
    sheet_id = generation.run(conn, generation.GenerationRequest(
        "encounter", composite["spine"], anchors, composite_id=composite_id,
        entity_id=composite["encounter_id"] if composite["encounter_id"] else None))
    for member in cast:
        if not member.get("existing_id"):
            start_member(composite_id, member)
    return sheet_id


def member_brief(member: dict) -> str:
    return context.pool_line(member)


def start_member(composite_id: int, member: dict) -> generation.Job:
    job = generation.start(generation.GenerationRequest(member["type"], member_brief(member),
                                                        composite_id=composite_id))
    with _lock:
        MEMBER_JOBS.setdefault(composite_id, {})[member["key"]] = job.id
    return job


# ── Background tasks ───────────────────────────────────────────────────────

def _work(task: Task, fn) -> None:
    try:
        with closing(db.get_connection()) as conn:
            fn(conn)
        task.status = "done"
    except (CompositeError, generation.GenerationFailed, llm.LLMError) as exc:
        task.error, task.status = str(exc), "failed"
    except Exception as exc:  # a crash must still end the task, or the page polls forever
        log.exception("Composite task %s for #%s crashed", task.kind, task.composite_id)
        task.error, task.status = f"Unexpected error: {exc}", "failed"


def start_task(composite_id: int, kind: str) -> Task:
    if running_task(composite_id):
        raise CompositeError("Something is already being drafted for this encounter.")
    fns = {"spines": lambda conn: propose_spines(conn, composite_id),
           "pool": lambda conn: propose_pool(conn, composite_id),
           "more": lambda conn: propose_pool(conn, composite_id, more=True),
           "draft": lambda conn: draft(conn, composite_id)}
    task = Task(composite_id, kind)
    with _lock:
        TASKS[composite_id] = task
    if generation.INLINE:
        _work(task, fns[kind])
    else:
        threading.Thread(target=_work, args=(task, fns[kind]), daemon=True,
                         name=f"composite-{composite_id}-{kind}").start()
    return task


# ── What the GM does between calls ─────────────────────────────────────────

def choose_spine(conn, composite_id: int, spine: str) -> None:
    """Settle the premise (one of the proposals as edited, or the brief itself) and clear the old pool."""
    spine = spine.strip()
    if not spine:
        raise CompositeError("The premise is empty.")
    db.update_composite(conn, composite_id, spine=spine, pool=[], stage="pool")


def save_pool(conn, composite_id: int, form) -> dict:
    """Apply the pool form: ticks, in-place edits and Battle Point toggles. Returns the composite."""
    composite = get(conn, composite_id)
    pool = []
    for member in composite["pool"]:
        key = member["key"]
        if form.get(f"drop_{key}") == "1":
            continue
        member = dict(member, ticked=form.get(f"tick_{key}") == "1")
        if not member.get("existing_id"):  # a reused entry keeps the bible's name and role
            name = " ".join(str(form.get(f"name_{key}", member["name"])).split())
            if name and knowledge.MARKER not in name:
                member["name"] = name
            if member["type"] == "adversary" and form.get(f"role_{key}") in context.ADVERSARY_ROLES:
                member["role"] = form.get(f"role_{key}")
        member["pitch"] = str(form.get(f"pitch_{key}", member["pitch"])).strip()
        if member["type"] == "adversary":
            try:
                member["count"] = max(1, min(20, int(form.get(f"count_{key}", member["count"]))))
            except (TypeError, ValueError):
                pass
        pool.append(member)
    toggles = [t for t in form.getlist("toggle") if t in battle.TOGGLES]
    try:
        pcs = max(1, min(12, int(form.get("pcs", composite["pcs"]))))
    except (TypeError, ValueError):
        pcs = composite["pcs"]
    db.update_composite(conn, composite_id, pool=pool, budget={"toggles": toggles}, pcs=pcs)
    return get(conn, composite_id)


def add_existing(conn, composite_id: int, entity_id: int) -> None:
    """Cast an existing bible entry (ticked: the GM picked it on purpose)."""
    composite = get(conn, composite_id)
    entry = next((e for e in context.context_entities(conn) if e["id"] == entity_id), None)
    if entry is None or entry["type"] not in context.POOL_TYPES:
        raise CompositeError("That entry can't join the cast.")
    if any(m.get("existing_id") == entity_id for m in composite["pool"]):
        return
    member = {"key": uuid.uuid4().hex[:8], "ticked": True, "type": entry["type"], "name": entry["name"],
              "pitch": "", "role": entry["fields"].get("role", "") if entry["type"] == "adversary" else "",
              "count": 1, "existing_id": entry["id"], "existing_ref": context.ref(entry)}
    db.update_composite(conn, composite_id, pool=composite["pool"] + [member])


def start_drafting(conn, composite_id: int) -> Task:
    composite = get(conn, composite_id)
    if not generation.ticked_cast(composite):
        raise CompositeError("Tick at least one cast member.")
    db.update_composite(conn, composite_id, stage="drafting")
    return start_task(composite_id, "draft")


# ── Progress ───────────────────────────────────────────────────────────────

def progress(conn, composite: dict) -> dict:
    """The sheet and each new member's state: drafting, failed (retryable), done, or lost to a restart."""
    drafted = db.composite_members(conn, composite["id"])
    jobs = MEMBER_JOBS.get(composite["id"], {})
    sheet = db.get_entity(conn, composite["encounter_id"]) if composite["encounter_id"] else None
    rows = []
    for member in generation.ticked_cast(composite):
        if member.get("existing_id"):
            rows.append({"member": member, "state": "existing", "entity": db.get_entity(conn, member["existing_id"])})
            continue
        job = generation.get_job(jobs.get(member["key"], ""))
        entity = None
        if job and job.entity_id:
            entity = db.get_entity(conn, job.entity_id)
        if entity is None:  # after a restart: the member keeps its cast name, so find it by name
            entity = next((e for e in drafted if e["type"] == member["type"] and e["name"] == member["name"]), None)
        if entity is not None:
            state = "done"
        elif job and job.status == "running":
            state = "running"
        elif job and job.status == "failed":
            state = "failed"
        else:
            state = "waiting" if running_task(composite["id"]) or sheet is None else "missing"
        rows.append({"member": member, "state": state, "entity": entity, "job": job})
    busy = running_task(composite["id"]) is not None or any(r["state"] == "running" for r in rows)
    if composite["stage"] == "drafting" and sheet and not busy:
        db.update_composite(conn, composite["id"], stage="done")
    return {"sheet": sheet, "rows": rows, "busy": busy}


def retry_member(conn, composite_id: int, key: str) -> None:
    composite = get(conn, composite_id)
    member = next((m for m in generation.ticked_cast(composite) if m["key"] == key), None)
    if member is None or member.get("existing_id") or not composite["encounter_id"]:
        raise CompositeError("Nothing to retry there.")
    start_member(composite_id, member)


# ── Reviewing the whole cast ───────────────────────────────────────────────

def cast_review(conn, encounter: dict) -> dict | None:
    """The cast panel for an encounter made by a composite (None for a hand-made or lone encounter).
    Promote all is blocked while anything is still drafting or any draft has unsettled decisions:
    promoting must never settle an open thread silently, and one button is the easiest way to."""
    composite_id = db.composite_for_entity(conn, encounter["id"])
    comp = db.get_composite(conn, composite_id) if composite_id else None
    if comp is None or comp["encounter_id"] != encounter["id"]:
        return None
    state = progress(conn, comp)
    drafts = [e for e in [encounter] + [r["entity"] for r in state["rows"] if r["state"] == "done"]
              if e["status"] == "draft"]
    blocked = []
    for e in drafts:
        review = generation.review_state(conn, e)
        if review and review["open_decisions"]:
            blocked.append({"entity": e, "open": review["open_decisions"]})
    return {"comp": comp, "rows": state["rows"], "busy": state["busy"], "drafts": drafts, "blocked": blocked}


def promote_all(conn, encounter: dict) -> int:
    """Promote the encounter and its member drafts, each with all its proposed links (the review panel's
    defaults). Returns how many entries were promoted."""
    cast = cast_review(conn, encounter)
    if cast is None:
        raise CompositeError("This encounter wasn't composed with a cast.")
    if cast["busy"]:
        raise CompositeError("Some members are still being drafted.")
    if cast["blocked"]:
        raise CompositeError("Settle the open decisions first: " +
                             ", ".join(b["entity"]["name"] for b in cast["blocked"]) + ".")
    for entity in cast["drafts"]:
        review = generation.review_state(conn, entity)
        source = review["source"] if review else None
        generation.promote(conn, entity, source["output"].get("proposed_links", []) if source else [])
    return len(cast["drafts"])


def discard_all(conn, encounter: dict) -> int:
    """Delete the encounter and its member drafts (their texts stay in the generation history).
    Entries already promoted, and reused bible entries, are left alone."""
    cast = cast_review(conn, encounter)
    if cast is None:
        raise CompositeError("This encounter wasn't composed with a cast.")
    if cast["busy"]:
        raise CompositeError("Some members are still being drafted.")
    for entity in cast["drafts"]:
        db.delete_entity(conn, entity["id"])
    return len(cast["drafts"])


def workspaces(conn) -> list[dict]:
    """Encounters still being composed (not yet drafted), newest first, for the drafts inbox."""
    rows = conn.execute("SELECT * FROM composites WHERE stage IN ('spines', 'pool') "
                        "ORDER BY updated_at DESC, id DESC").fetchall()
    return [db.composite_from_row(r) for r in rows]
