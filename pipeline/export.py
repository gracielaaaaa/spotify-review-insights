"""Exports: final per-ID records (one per source row), membership, calls, grading/ folder.
Exact-text reuse: every source row whose text has a validated result under the final
label_config is completed; non-origin rows carry cache_source_id -> the origin ID sent."""

import csv
import gzip
import json
from pathlib import Path

from . import config, store


def final_records(con, lc, scope_ids=None):
    """Yield one final record per source row (source order). scope_ids: IDs that were in the
    declared classification scope (only used to choose the quarantine reason)."""
    res = {r["text_sha"]: r for r in con.execute("SELECT * FROM results WHERE label_config=?", (lc,))}
    qua = {r["text_sha"]: r for r in con.execute("SELECT * FROM quarantine WHERE label_config=?", (lc,))}
    scope = set(scope_ids) if scope_ids is not None else None
    for s in con.execute("SELECT review_id, source_sha256, text_sha, empty FROM source ORDER BY row_idx"):
        base = {"review_id": s["review_id"], "source_sha256": s["source_sha256"]}
        if s["empty"]:
            yield {**base, "status": "quarantined", "reason": "empty_review_text"}
            continue
        r = res.get(s["text_sha"])
        if r is not None:
            rec = {**base, "status": "completed", "topic": r["topic"], "intent": r["intent"],
                   "sentiment": r["sentiment"], "severity": r["severity"], "entities": json.loads(r["entities"]),
                   "evidence_quote": r["evidence_quote"], "needs_review": bool(r["needs_review"]),
                   "label_config": lc, "subtopic": r["subtopic"]}
            if r["review_reason"]:
                rec["review_reason"] = r["review_reason"]
            if r["origin_review_id"] != s["review_id"]:
                rec["cache_source_id"] = r["origin_review_id"]
            yield rec
            continue
        q = qua.get(s["text_sha"])
        if q is not None:
            yield {**base, "status": "quarantined", "reason": q["reason"], "attempts": q["attempts"]}
        elif scope is not None and s["review_id"] not in scope:
            yield {**base, "status": "quarantined", "reason": "not_in_declared_classification_scope"}
        else:
            yield {**base, "status": "quarantined", "reason": "pending_not_classified"}


def write_jsonl(rows, path: Path, gz=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = (lambda: gzip.open(str(path) + ".gz", "wt", encoding="utf-8")) if gz else \
        (lambda: open(path, "w", encoding="utf-8"))
    n = 0
    with opener() as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
            n += 1
    return n


def calls(con, roles=None, label_configs_for_enrich=None, run_ids=None):
    q = "SELECT * FROM calls ORDER BY started"
    for c in con.execute(q):
        if roles and c["role"] not in roles:
            continue
        if run_ids and c["run_id"] not in run_ids:
            continue
        if c["role"] == "enrich" and label_configs_for_enrich and c["label_config"] not in label_configs_for_enrich:
            continue
        yield {"request_id": c["request_id"], "role": c["role"], "review_ids": json.loads(c["review_ids"]),
               "model": c["model"], "phase": c["phase"], "outcome": c["outcome"], "label_config": c["label_config"],
               "input_tokens": c["input_tokens"] or 0, "output_tokens": c["output_tokens"] or 0,
               "cache_read_tokens": c["cache_read_tokens"] or 0, "cache_write_tokens": c["cache_write_tokens"] or 0,
               "run_id": c["run_id"], "stage": c["stage"], "effort": c["effort"], "tier": c["tier"],
               "prompt_version": c["prompt_version"], "attempt": c["attempt"], "started": c["started"],
               "ended": c["ended"], "error": c["error"], "cost_usd": c["cost_usd"]}


def write_csv(rows, fields, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
