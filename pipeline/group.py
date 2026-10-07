"""Stage 4 - Group. Membership is decided by CODE: every completed complaint/cancellation
belongs to exactly one issue, issue_id = '<topic>.<subtopic>' (stable, model-free, so
re-ranking never needs a model). The group ROLE (small model) only names each issue from
a bounded, seeded sample of <=15 quotes and flags misfits for human inspection."""

import hashlib
import json
import time

from . import config, llm, store

SCHEMA = {"type": "object", "properties": {
    "name": {"type": "string"}, "description": {"type": "string"},
    "representative_ids": {"type": "array", "items": {"type": "string"}},
    "misfit_ids": {"type": "array", "items": {"type": "string"}}},
    "required": ["name", "description", "representative_ids", "misfit_ids"], "additionalProperties": False}


def membership(records):
    """records: iterable of final completed dicts. Returns sorted [(issue_id, review_id)]."""
    pairs = [(f"{r['topic']}.{r['subtopic']}", r["review_id"]) for r in records
             if r.get("status") == "completed" and r["intent"] in ("complaint", "cancellation")]
    return sorted(pairs)


def evidence_sample(members, records_by_id, k=15, salt="group"):
    """Deterministic sample: lowest sha256(salt:id), preferring distinct texts (originals)."""
    ids = sorted(members, key=lambda rid: hashlib.sha256(f"{salt}:{rid}".encode()).hexdigest())
    out, seen = [], set()
    for rid in ids:
        q = records_by_id[rid]["evidence_quote"]
        if q in seen:
            continue
        seen.add(q)
        out.append({"review_id": rid, "quote": q[:300], "severity": records_by_id[rid]["severity"]})
        if len(out) == k:
            break
    return out


def name_issues(con, pairs, records_by_id, *, run_id, min_members=1, log=print, cache=None):
    """One bounded call per issue. cache: {issue_id: saved naming} keyed by evidence hash so
    a warm run makes zero new group calls under unchanged inputs/settings."""
    rates = llm.load_rates()
    ledger = llm.Ledger(spent=store.spent_usd(con), cap=config.SPEND_CAP_USD)
    system = config.prompt_text(config.GROUP_PROMPT_VERSION)
    by_issue = {}
    for iid, rid in pairs:
        by_issue.setdefault(iid, []).append(rid)
    cache = cache or {}
    out = {}
    for iid, members in sorted(by_issue.items()):
        ev = evidence_sample(members, records_by_id)
        key = hashlib.sha256(json.dumps([config.MODELS["group"], config.prompt_hash(config.GROUP_PROMPT_VERSION),
                                         iid, ev], sort_keys=True).encode()).hexdigest()[:16]
        if iid in cache and cache[iid].get("evidence_key") == key:
            out[iid] = cache[iid]
            continue
        if len(members) < min_members:
            out[iid] = {"issue_id": iid, "name": iid, "description": "Not named (below naming threshold).",
                        "representative_ids": [e["review_id"] for e in ev[:3]], "misfit_ids": [],
                        "evidence_key": key, "named_by": "code"}
            continue
        user = f"Issue key: {iid}\nMembers: {len(members)}\nQuotes:\n" + "\n".join(
            f"- {e['review_id']}: {json.dumps(e['quote'], ensure_ascii=False)}" for e in ev)
        fails = []
        try:
            msg, usage = llm.call(role="group", model=config.MODELS["group"], system=system, user=user,
                                  max_tokens=400, ledger=ledger, rates=rates, schema=SCHEMA,
                                  on_attempt=fails.append)
        except llm.BudgetExceeded:
            raise
        except Exception as e:
            log(f"[group] {iid} naming failed: {e}")
            with store.tx(con):
                for a in fails:
                    store.log_call(con, run_id=run_id, stage="group", role="group", model=config.MODELS["group"],
                                   effort="thinking_off", phase="initial", label_config=None,
                                   prompt_version=config.GROUP_PROMPT_VERSION, review_ids=[], tier="standard", **a)
            out[iid] = {"issue_id": iid, "name": iid, "description": "Naming call failed; key shown.",
                        "representative_ids": [], "misfit_ids": [], "evidence_key": None, "named_by": "code"}
            continue
        try:
            d = json.loads(llm.text_of(msg))
        except json.JSONDecodeError:
            d = {"name": iid, "description": "Invalid naming output.", "representative_ids": [], "misfit_ids": []}
        allowed = {e["review_id"] for e in ev}
        rejected = [x for x in d["representative_ids"] + d["misfit_ids"] if x not in allowed]
        d["representative_ids"] = [x for x in d["representative_ids"] if x in allowed][:3]
        d["misfit_ids"] = [x for x in d["misfit_ids"] if x in allowed]
        with store.tx(con):
            for a in fails:
                store.log_call(con, run_id=run_id, stage="group", role="group", model=config.MODELS["group"],
                               effort="thinking_off", phase="initial", label_config=None,
                               prompt_version=config.GROUP_PROMPT_VERSION, review_ids=[], tier="standard", **a)
            store.log_call(con, run_id=run_id, stage="group", role="group", model=config.MODELS["group"],
                           effort="thinking_off", phase="initial", outcome="succeeded", label_config=None,
                           prompt_version=config.GROUP_PROMPT_VERSION, review_ids=[e["review_id"] for e in ev],
                           input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                           cache_read_tokens=usage["cache_read_tokens"], cache_write_tokens=usage["cache_write_tokens"],
                           tier="standard", attempt=usage["attempt"], started=usage["started"], ended=time.time(),
                           error=(f"rejected unknown ids {rejected}" if rejected else None),
                           cost_usd=usage["cost_usd"], request_id=usage["request_id"])
        out[iid] = {"issue_id": iid, **d, "evidence": ev, "members": len(members), "evidence_key": key,
                    "named_by": config.MODELS["group"], "rejected_ids": rejected}
    return out
