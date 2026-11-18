"""Running a generation: bible in, checked draft out, stored as a draft entity with its version history.

A generation takes a minute or two, so routes start it as a background job and poll. Jobs live in
memory: this is a one-GM local app, and a job lost to a server restart is simply started again.
"""
import difflib
import logging
import re
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field

from . import battle, context, db, llm

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # one retry, told what was wrong; after that the GM sees the problems
GENERATABLE = ("npc", "location", "faction", "arc", "item", "adversary", "environment", "encounter")  # PCs belong to players; sessions to play
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
    composite_id: int | None = None  # part of a composite encounter (found from the history on regeneration)


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
    scene = composite_scene(conn, request)
    system = context.system_prompt(conn, entities)
    prompt = context.user_prompt(request.entity_type, request.brief, anchors, current, request.notes,
                                 scene.text if scene else "")
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

    if scene and scene.is_sheet:  # the arithmetic is the app's, not the model's
        draft.fields.pop("battle_points", None)
        if scene.budget_line:
            draft.fields["battle_points"] = scene.budget_line

    meta = dict(brief=request.brief, notes=request.notes, anchors=[a["id"] for a in anchors],
                model=result.model, usage=result.usage, composite_id=scene.composite_id if scene else None)
    if entity is None:
        entity_id = db.create_entity(conn, request.entity_type, draft.name, draft.fields, status="draft")
        db.record_generation(conn, entity_type=request.entity_type, kind="generated", entity_id=entity_id,
                             output=draft.as_output(), **meta)
    else:
        entity_id = entity["id"]
        lineage = snapshot_if_edited(conn, entity)
        db.update_entity(conn, entity_id, draft.name, draft.fields)
        db.record_generation(conn, entity_type=request.entity_type, kind="generated", entity_id=entity_id,
                             lineage_id=lineage, output=draft.as_output(), **meta)
    if scene:
        if scene.is_sheet:
            db.update_composite(conn, scene.composite_id, encounter_id=entity_id)
        elif scene.encounter_id:
            db.add_link(conn, entity_id, scene.encounter_id, "appears in")  # no-op if it already exists
    return entity_id


# ── Composite encounters: what a member or sheet draft adds to its request ──

@dataclass
class Scene:
    composite_id: int
    is_sheet: bool
    text: str
    budget_line: str
    encounter_id: int | None


def ticked_cast(composite: dict) -> list[dict]:
    return [m for m in composite["pool"] if m.get("ticked")]


def composite_meter(composite: dict) -> battle.Meter:
    adversaries = [m for m in ticked_cast(composite) if m["type"] == "adversary"]
    return battle.meter(composite["pcs"], adversaries, composite["budget"].get("toggles", []))


def composite_scene(conn, request: GenerationRequest) -> Scene | None:
    """The composite block for this request, or None for an ordinary draft. A regenerated member finds
    its composite through its history and reads the encounter sheet as it is now, hand edits included."""
    composite_id = request.composite_id
    if composite_id is None and request.entity_id is not None:
        composite_id = db.composite_for_entity(conn, request.entity_id)
    composite = db.get_composite(conn, composite_id) if composite_id else None
    if composite is None:
        return None
    is_sheet = request.entity_type == "encounter" and composite["encounter_id"] in (None, request.entity_id)
    sheet = None if is_sheet else db.get_entity(conn, composite["encounter_id"]) if composite["encounter_id"] else None
    if not is_sheet and sheet is None:
        return None  # the encounter was discarded: draft the member as an ordinary entry
    budget_line = composite_meter(composite).summary()
    text = context.composite_scene(composite["spine"], ticked_cast(composite), sheet, budget_line)
    return Scene(composite["id"], is_sheet, text, budget_line, None if is_sheet else sheet["id"])


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


MAX_PARALLEL = 4  # a composite fans out; keep the subscription's limits and the machine comfortable
_slots = threading.BoundedSemaphore(MAX_PARALLEL)


def _work(job: Job) -> None:
    try:
        with _slots, closing(db.get_connection()) as conn:
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


# ── Reviewing a draft's versions ───────────────────────────────────────────

def same_state(entity: dict, version: dict) -> bool:
    out = version["output"]
    return out.get("name") == entity["name"] and out.get("fields") == entity["fields"]


def changed_keys(previous: dict | None, output: dict) -> set[str]:
    if previous is None:
        return set()
    before, after = previous.get("fields", {}), output.get("fields", {})
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    if previous.get("name") != output.get("name"):
        changed.add("name")
    return changed


def _fill_target_types(conn, output: dict) -> None:
    """Drafts generated before target types were stored lack them; look them up (None if deleted)."""
    for items, id_key, type_key in ((output.get("proposed_links", []), "target_id", "target_type"),
                                    (output.get("open_threads_touched", []), "entry_id", "entry_type")):
        for item in items:
            if type_key not in item:
                target = db.get_entity(conn, item[id_key])
                item[type_key] = target["type"] if target else None


def review_state(conn, entity: dict) -> dict | None:
    """Everything the review panel needs, or None if the draft has no generation history."""
    lineage = db.lineage_for_entity(conn, entity["id"])
    if lineage is None:
        return None
    versions = db.lineage_versions(conn, lineage)
    for i, v in enumerate(versions):
        _fill_target_types(conn, v["output"])
        v["number"] = i + 1
        v["changed"] = changed_keys(versions[i - 1]["output"] if i else None, v["output"])
        v["is_current"] = same_state(entity, v)
    current = next((v for v in reversed(versions) if v["is_current"]), None)
    # Links and threads under review come from the generated version now in place, or the latest one
    # behind the GM's edits (an edit snapshot carries no proposals of its own).
    upto = current["id"] if current else versions[-1]["id"]
    source = next((v for v in reversed(versions) if v["kind"] == "generated" and v["id"] <= upto), None)
    first = next((v for v in versions if v["kind"] == "generated"), None)
    found = decisions(source["output"]) if source else []
    return {"lineage": lineage, "versions": versions, "current": current, "source": source,
            "brief": first["brief"] if first else "", "anchors": source["anchors"] if source else [],
            "decisions": found, "open_decisions": sum(1 for d in found if not d["state"]),
            "rejected_text": "\n".join(f"Drop this: {d['text']}" for d in found if d["state"] == "rejected")}


# ── Decisions: what a draft asks the GM to settle ──────────────────────────

def decisions(output: dict) -> list[dict]:
    """Open threads the draft answers and claims it makes about existing entries, as one checklist.
    Keys are stable per version (t0, t1… for threads, c0, c1… for implications)."""
    state = output.get("decision_state", {})
    mirror = output.get("decision_mirror", {})
    found = []
    for i, t in enumerate(output.get("open_threads_touched", [])):
        found.append({"key": f"t{i}", "kind": "thread", "entry_id": t["entry_id"], "entry_type": t.get("entry_type"),
                      "entry": t["entry"], "title": t["thread"], "text": t["proposal"], "state": state.get(f"t{i}"),
                      "mirrored_from": mirror.get(f"t{i}")})
    for i, c in enumerate(output.get("canon_implications", [])):
        found.append({"key": f"c{i}", "kind": "implication", "entry_id": c["entry_id"],
                      "entry_type": c.get("entry_type"), "entry": c["entry"], "title": "",
                      "text": c["implication"], "state": state.get(f"c{i}"), "mirrored_from": mirror.get(f"c{i}")})
    return found


def note_line(source_name: str, decision: dict) -> str:
    """The barred line an accepted decision leaves on the entry it's about. Source, no date: the
    drafting history already records when."""
    if decision["kind"] == "thread":
        text = f"From {source_name}, on “{decision['title']}”: {decision['text']}"
    else:
        text = f"From {source_name}: {decision['text']}"
    return context.barred(text)


def resolve(conn, entity: dict, source: dict, key: str, accept: bool) -> None:
    """Accept (note it on the entry it's about) or reject (feed the next regeneration) one decision."""
    decision = next((d for d in decisions(source["output"]) if d["key"] == key), None)
    if decision is None:
        raise GenerationFailed("No such decision on this draft.")
    if decision["state"]:
        return  # already settled; clicking twice must not add the note twice
    if accept:
        target = db.get_entity(conn, decision["entry_id"])
        if target is None:
            raise GenerationFailed(f"{decision['entry']} no longer exists.")
        fields = dict(target["fields"])
        fields["notes"] = "\n\n".join(p for p in (fields.get("notes", "").strip(),
                                                   note_line(entity["name"], decision)) if p)
        db.update_entity(conn, target["id"], target["name"], fields)
    output = source["output"]
    output.setdefault("decision_state", {})[key] = "accepted" if accept else "rejected"
    db.update_generation_output(conn, source["id"], output)
    mirror_to_siblings(conn, entity, decision, accept)


def same_claim(a: str, b: str) -> bool:
    """Near-identical wording: most of the same words, or one sentence inside the other. Paraphrases that
    say it very differently are left alone: settling a different claim by mistake is the worse error."""
    a, b = " ".join(a.casefold().split()), " ".join(b.casefold().split())
    if a in b or b in a:
        return True
    words_a, words_b = set(re.findall(r"\w{3,}", a)), set(re.findall(r"\w{3,}", b))
    overlap = len(words_a & words_b) / max(1, len(words_a | words_b))
    return overlap >= 0.6 or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def mirror_to_siblings(conn, entity: dict, decision: dict, accept: bool) -> None:
    """In a composed encounter, the same claim often turns up on two members' drafts. Ruling on it once
    rules on it everywhere in the cast, marked as settled elsewhere; the note is only ever added once."""
    composite_id = db.composite_for_entity(conn, entity["id"])
    if composite_id is None:
        return
    for sibling in db.composite_members(conn, composite_id):
        if sibling["id"] == entity["id"] or sibling["status"] != "draft":
            continue
        review = review_state(conn, sibling)
        source = review["source"] if review else None
        if source is None:
            continue
        output, changed = source["output"], False
        for other in decisions(output):
            if (not other["state"] and other["kind"] == decision["kind"] and other["entry_id"] == decision["entry_id"]
                    and (decision["kind"] != "thread" or other["title"] == decision["title"])
                    and same_claim(other["text"], decision["text"])):
                output.setdefault("decision_state", {})[other["key"]] = "accepted" if accept else "rejected"
                output.setdefault("decision_mirror", {})[other["key"]] = entity["name"]
                changed = True
        if changed:
            db.update_generation_output(conn, source["id"], output)


def pending_drafts(conn) -> list[dict]:
    """AI drafts awaiting review, newest activity first, each with its review state."""
    rows = conn.execute(
        "SELECT e.* FROM entities e WHERE e.status = 'draft' "
        "AND EXISTS (SELECT 1 FROM generations g WHERE g.entity_id = e.id)"
    ).fetchall()
    pending = []
    for row in rows:
        entity = db.entity_from_row(row)
        review = review_state(conn, entity)
        pending.append({"entity": entity, "review": review, "updated": review["versions"][-1]["created_at"]})
    return sorted(pending, key=lambda p: p["updated"], reverse=True)


def running_jobs() -> list[Job]:
    return [job for job in list(JOBS.values()) if job.status == "running"]


def restore(conn, entity: dict, version: dict) -> None:
    """Put an earlier version back in place, keeping the current state in the history first.
    The GM's own Notes stay as they are now: they aren't part of the AI's versions."""
    snapshot_if_edited(conn, entity)
    out = version["output"]
    fields = {k: v for k, v in out["fields"].items() if k != "notes"}
    if entity["fields"].get("notes"):
        fields["notes"] = entity["fields"]["notes"]
    db.update_entity(conn, entity["id"], out["name"], fields)


def promote(conn, entity: dict, links: list[dict], keep_notes: str = "") -> int:
    """Make the draft canon and create the chosen proposed links. Returns how many links were made.
    keep_notes, if given, is appended to the entry's Notes, fully barred; otherwise the AI's notes
    stay behind in the generation history."""
    made = 0
    for link in links:
        target = db.get_entity(conn, link["target_id"])
        if target is None or target["id"] == entity["id"]:
            continue  # the target was deleted after the draft was generated
        ends = (entity["id"], target["id"]) if link["direction"] == "from_new" else (target["id"], entity["id"])
        made += db.add_link(conn, *ends, link["label"], known=link["party_knows"])
    kept = context.barred(keep_notes)
    if kept:
        fields = dict(entity["fields"])
        fields["notes"] = "\n\n".join(part for part in (fields.get("notes", "").strip(), kept) if part)
        db.update_entity(conn, entity["id"], entity["name"], fields)
    db.set_status(conn, entity["id"], "canon")
    return made


def move_legacy_notes(conn) -> int:
    """Drafts generated before 2026-11-14 had the AI's notes written into their Notes field (barred).
    Take them back out, from the entry and from its stored versions, wherever Notes holds exactly
    those notes: anything the GM wrote there is left alone. Idempotent; returns entries changed."""
    changed = 0
    for (lineage,) in conn.execute("SELECT DISTINCT lineage_id FROM generations").fetchall():
        versions = db.lineage_versions(conn, lineage)
        ai_notes = {context.barred(v["output"].get("dm_notes", "")) for v in versions if v["kind"] == "generated"}
        ai_notes.discard("")
        if not ai_notes:
            continue
        for v in versions:
            fields = v["output"].get("fields", {})
            if fields.get("notes") in ai_notes:
                del fields["notes"]
                db.update_generation_output(conn, v["id"], v["output"])
        entity_ids = {v["entity_id"] for v in versions if v["entity_id"] is not None}
        for entity_id in entity_ids:
            entity = db.get_entity(conn, entity_id)
            if entity and entity["fields"].get("notes") in ai_notes:
                fields = {k: v for k, v in entity["fields"].items() if k != "notes"}
                db.update_entity(conn, entity_id, entity["name"], fields)
                changed += 1
    return changed
