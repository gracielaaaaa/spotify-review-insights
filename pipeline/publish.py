"""Publish saved pipeline outputs (no model calls) to the Supabase Postgres database that
backs the dashboard API. Writes go through PostgREST with the project's publishable key,
which only works while the temporary 'loader write' policies in db/loader_open.sql are
applied; db/loader_close.sql removes them again (tables are otherwise read-only).

  python -m pipeline publish --run-dir runs/final_100k --db state/pipeline.db
"""

import csv
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from . import config, store

TABLES = ["reviews", "issues", "topic_rollup", "claims", "memo", "metrics", "runs", "monthly"]


def _req(method, url, key, body=None, prefer=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("apikey", key)
    r.add_header("Authorization", f"Bearer {key}")
    r.add_header("Content-Type", "application/json")
    if prefer:
        r.add_header("Prefer", prefer)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(r, timeout=120) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 4:
                raise RuntimeError(f"{method} {url} -> {e.code}: {e.read()[:300]!r}")
        except urllib.error.URLError:
            if attempt == 4:
                raise
        time.sleep(2 ** attempt)


def upsert(base, key, table, rows, chunk=2000, log=print):
    for k in range(0, len(rows), chunk):
        _req("POST", f"{base}/rest/v1/{table}", key, rows[k:k + chunk],
             prefer="resolution=merge-duplicates,return=minimal")
    log(f"[publish] {table}: {len(rows)} rows")


def build(run_dir: Path, con):
    recs = [json.loads(l) for l in open(run_dir / "records.jsonl", encoding="utf-8")]
    summary = json.loads((run_dir / "run_summary.json").read_text())
    issues = json.loads((run_dir / "issues.json").read_text()) if (run_dir / "issues.json").exists() else {}
    memo = json.loads((run_dir / "memo.json").read_text()) if (run_dir / "memo.json").exists() else None
    member = {r["review_id"]: r["issue_id"] for r in csv.DictReader(open(run_dir / "membership.csv", encoding="utf-8"))}
    ranking = list(csv.DictReader(open(run_dir / "ranking.csv", encoding="utf-8")))
    meta = {r[0]: (r[1], r[2][:7]) for r in con.execute("SELECT review_id, review_rating, review_timestamp FROM source")}
    scope = set(json.loads((run_dir / "scope_ids.json").read_text())) if (run_dir / "scope_ids.json").exists() else set()

    reviews, monthly_c, monthly_n = [], Counter(), Counter()
    for r in recs:
        if r["status"] != "completed":
            continue
        rating, month = meta[r["review_id"]]
        reviews.append({"review_id": r["review_id"], "topic": r["topic"], "subtopic": r.get("subtopic"),
                        "intent": r["intent"], "sentiment": r["sentiment"], "severity": r["severity"],
                        "evidence_quote": r["evidence_quote"][:600], "needs_review": r["needs_review"],
                        "cache_source_id": r.get("cache_source_id"), "issue_id": member.get(r["review_id"]),
                        "review_rating": int(rating) if rating.isdigit() else None, "review_month": month,
                        "in_declared_scope": r["review_id"] in scope})
        monthly_n[month] += 1
        if r["intent"] in ("complaint", "cancellation"):
            monthly_c[(month, r["topic"])] += 1
    monthly = [{"month": m, "topic": t, "complaints": c, "completed": monthly_n[m]} for (m, t), c in monthly_c.items()]

    issue_rows = []
    for r in ranking:
        info = issues.get(r["issue_id"], {})
        t, s = r["issue_id"].split(".", 1)
        issue_rows.append({"issue_id": r["issue_id"], "rank": int(r["rank"]), "topic": t, "subtopic": s,
                           "name": info.get("name", r["issue_id"]), "description": info.get("description"),
                           "complaint_count": int(r["complaint_count"]), "severity_sum": int(r["severity_sum"]),
                           "mean_severity": r["mean_severity"], "priority_score": int(r["priority_score"]),
                           "representative_ids": info.get("representative_ids", []),
                           "evidence": info.get("evidence", [])[:8]})
    rollup = [{**r, "issues": int(r["issues"]), "complaint_count": int(r["complaint_count"]),
               "severity_sum": int(r["severity_sum"])}
              for r in csv.DictReader(open(run_dir / "topic_rollup.csv", encoding="utf-8"))]
    claims = list(csv.DictReader(open(run_dir / "claims.csv", encoding="utf-8"))) if (run_dir / "claims.csv").exists() else []
    cov = summary["coverage"]
    intents = Counter(r["intent"] for r in reviews)
    topics = Counter(r["topic"] for r in reviews)
    verify = json.loads((run_dir / "verify" / "summary.json").read_text()) if (run_dir / "verify" / "summary.json").exists() else None
    metrics = [{"key": k, "value": v} for k, v in {
        "coverage": cov, "intents": dict(intents), "topics": dict(topics),
        "complaint_records": intents["complaint"] + intents["cancellation"],
        "mean_severity_complaints": round(sum(r["severity"] for r in reviews if r["intent"] in ("complaint", "cancellation"))
                                          / max(1, intents["complaint"] + intents["cancellation"]), 6),
        "verify": verify, "usage": summary.get("this_run_usage"), "ledger_spent_total_usd": summary.get("ledger_spent_total_usd"),
        "label_config": summary["label_config"], "models": summary["models"],
    }.items()]
    memo_rows = [{"id": 1, "run_id": summary["run_id"], "model": config.MODELS["memo"],
                  "recommendation": memo["memo"]["recommendation"], "memo_markdown": memo["memo"]["memo_markdown"],
                  "checks": memo["checks"], "cited_claim_ids": memo["memo"]["cited_claim_ids"],
                  "cited_review_ids": memo["memo"]["cited_review_ids"]}] if memo and memo.get("memo") else []
    runs = [{"run_id": summary["run_id"], "label_config": summary["label_config"], "summary": summary}]
    return {"reviews": reviews, "issues": issue_rows, "topic_rollup": rollup, "claims": claims, "memo": memo_rows,
            "metrics": metrics, "runs": runs, "monthly": monthly}


def run(run_dir, db, log=print):
    from dotenv import load_dotenv
    load_dotenv(config.ROOT / ".env")
    base, key = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_PUBLISHABLE_KEY"]
    con = store.connect(Path(db))
    data = build(Path(run_dir), con)
    for t in TABLES:
        upsert(base, key, t, data[t], log=log)
    return {t: len(v) for t, v in data.items()}
