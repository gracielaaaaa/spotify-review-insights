"""Orchestrator. Code owns dispatch, state, validation, budgets and logs; models only do the
bounded language tasks inside enrich / verify / group / memo.

  python -m pipeline run --input data.csv [--scope N | --subset ids.csv] --out OUTDIR   (paid)
  python -m pipeline rank --grading grading/                                           (offline)
"""

import argparse
import csv
import hashlib
import json
import signal
import sys
import time
from pathlib import Path

from . import config, enrich, export, group, ingest, llm, memo, rank, store, verify

STAGES = ["ingest", "enrich", "verify", "group", "rank", "memo"]


class RunLog:
    def __init__(self, path: Path, run_id: str):
        self.path, self.run_id = path, run_id
        path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, **e):
        e = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "t": round(time.time(), 3), "run_id": self.run_id, **e}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        try:
            print(json.dumps(e, ensure_ascii=False)[:300], flush=True)
        except (BrokenPipeError, OSError):
            pass  # console gone (e.g. Ctrl-C killed a pipe); the log file still has the event


def completed_ids(con, ids, lc):
    done = {r[0] for r in con.execute("SELECT text_sha FROM results WHERE label_config=?", (lc,))}
    out = []
    for k in range(0, len(ids), 900):
        part = ids[k:k + 900]
        out += [r[0] for r in con.execute(
            f"SELECT review_id, text_sha FROM source WHERE review_id IN ({','.join('?' * len(part))})", part)
            if r[1] in done]
    return sorted(out)


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def cmd_run(a):
    t_start = time.time()
    inp = Path(a.input).resolve()
    db = Path(a.db) if a.db else config.STATE_DIR / f"{inp.stem}.db"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    con = store.connect(db)
    run_id = a.run_id or f"{inp.stem}-{time.strftime('%Y%m%d-%H%M%S')}"
    log = RunLog(out / "run_log.jsonl", run_id)
    stages = a.stages.split(",") if a.stages else STAGES
    lc = config.label_config()
    log.event(stage="run", status="start", input=str(inp.name), db=str(db.name), stages=stages, label_config=lc,
              models=config.MODELS, spend_cap_usd=config.SPEND_CAP_USD, max_workers=a.workers,
              batch_size=config.ENRICH_BATCH_SIZE, spent_before_usd=round(store.spent_usd(con), 6))
    timings = {}

    # 1. ingest (skip when this exact file is already loaded)
    t = time.time()
    sha = file_sha(inp)
    if store.kv_get(con, "ingested_sha") != sha or "ingest" in (a.force or ""):
        rep = ingest.ingest(inp, con, out)
        with store.tx(con):
            store.kv_set(con, "ingested_sha", sha)
        log.event(stage="ingest", status="done", records=rep["records"], empty=rep["empty_review_text"],
                  distinct_texts=rep["distinct_nonempty_texts"], seconds=round(time.time() - t, 2))
    else:
        log.event(stage="ingest", status="cached", file_sha256=sha)
    timings["ingest"] = round(time.time() - t, 3)

    subset = Path(a.subset) if a.subset else None
    scope = ingest.scope_ids(con, a.scope, subset)
    log.event(stage="scope", in_scope_nonempty=len(scope), scope_rule=(f"subset:{subset.name}" if subset else
              (f"first {a.scope} nonempty by sha256(seed:id)" if a.scope else "all nonempty")))

    # 2. enrich
    if "enrich" in stages:
        t = time.time()
        state_key = f"enrich_state:{run_id}"
        phase = "resume" if store.kv_get(con, state_key) == "interrupted" else "initial"
        try:
            if a.batch_api:
                s = enrich.run_batch(con, scope, run_id=run_id, phase=phase, requests_per_job=a.requests_per_job,
                                     max_jobs=a.max_jobs, log=lambda m: log.event(stage="enrich", msg=m))
            else:
                s = enrich.run(con, scope, run_id=run_id, phase=phase, max_requests=a.max_requests,
                               workers=a.workers, log=lambda m: log.event(stage="enrich", msg=m))
            with store.tx(con):
                store.kv_set(con, state_key, "completed")
            if phase == "resume":
                ids = completed_ids(con, scope, lc)
                (out / "checkpoint_after.json").write_text(json.dumps({"completed_ids": ids}) + "\n")
                log.event(stage="enrich", status="checkpoint_after", completed=len(ids))
            log.event(stage="enrich", status="done", phase=phase, **s, seconds=round(time.time() - t, 2))
        except (KeyboardInterrupt, llm.BudgetExceeded, llm.FatalAPIError) as e:
            ids = completed_ids(con, scope, lc)
            with store.tx(con):
                store.kv_set(con, state_key, "interrupted")
            (out / "checkpoint_before.json").write_text(json.dumps({"completed_ids": ids}) + "\n")
            log.event(stage="enrich", status="interrupted", reason=type(e).__name__, detail=str(e)[:200],
                      completed_saved=len(ids), pending=len(scope) - len(ids), phase=phase,
                      resume_with=f"python -m pipeline run --input {inp.name} --run-id {run_id} ...")
            sys.exit(130)
        timings["enrich"] = round(time.time() - t, 3)

    # final records for the whole input (exact-text reuse applies to every row)
    recs = list(export.final_records(con, lc, scope))
    by_id = {r["review_id"]: r for r in recs}
    completed = [r for r in recs if r["status"] == "completed"]
    export.write_jsonl(recs, out / "records.jsonl")
    export.write_jsonl(completed, out / "enriched.jsonl")
    export.write_jsonl([r for r in recs if r["status"] == "quarantined"], out / "quarantine.jsonl")
    scope_set = set(scope)
    (out / "scope_ids.json").write_text(json.dumps(scope) + "\n")
    coverage = {"source_rows": len(recs), "in_scope_nonempty": len(scope),
                "completed": len(completed), "completed_in_scope": sum(r["review_id"] in scope_set for r in completed),
                "completed_via_exact_text_reuse": sum("cache_source_id" in r for r in completed),
                "quarantined": len(recs) - len(completed),
                "quarantine_reasons": {}, "needs_review": sum(r["needs_review"] for r in completed)}
    for r in recs:
        if r["status"] == "quarantined":
            coverage["quarantine_reasons"][r["reason"]] = coverage["quarantine_reasons"].get(r["reason"], 0) + 1
    log.event(stage="records", **coverage)

    # 3. verify (blind, seeded sample of origin records in scope)
    vsummary = None
    if "verify" in stages:
        t = time.time()
        origins = [r["review_id"] for r in completed if "cache_source_id" not in r and r["review_id"] in scope_set]
        sids = verify.sample(origins, fraction=config.VERIFY_SAMPLE_FRACTION, minimum=min(a.verify_min, len(origins)),
                             maximum=a.verify_max)
        texts = {rid: t2 for rid, t2 in con.execute(
            f"SELECT review_id, review_text FROM source WHERE review_id IN ({','.join('?' * len(sids))})", sids)} if sids else {}
        vres, vcfg = verify.run(con, sids, texts, run_id=run_id, log=lambda m: log.event(stage="verify", msg=m))
        vsummary, vrows = verify.compare(by_id, vres)
        planted = verify.planted_errors(by_id, vres)
        vdir = out / "verify"
        vdir.mkdir(exist_ok=True)
        (vdir / "summary.json").write_text(json.dumps({"verifier_config": vcfg, "sample_rule":
            f"seeded random {config.VERIFY_SAMPLE_FRACTION:.0%} of in-scope origin records, min {a.verify_min}, max {a.verify_max}",
            **vsummary, "planted_error_copy": {k: planted[k] for k in ("planted", "flagged")}}, indent=2) + "\n")
        if vrows:
            export.write_csv(vrows, list(vrows[0]), vdir / "comparison.csv")
            export.write_csv([r for r in vrows if not r["all_agree"]], list(vrows[0]), vdir / "disagreements.csv")
        (vdir / "planted_errors.json").write_text(json.dumps(planted, indent=2) + "\n")
        log.event(stage="verify", status="done", **vsummary, seconds=round(time.time() - t, 2))
        timings["verify"] = round(time.time() - t, 3)

    # 4. group (membership by code; names by model, cached by evidence hash)
    t = time.time()
    pairs = group.membership(completed)
    export.write_csv([{"issue_id": i, "review_id": r} for i, r in pairs], ["issue_id", "review_id"],
                     out / "membership.csv")
    issues = store.kv_get(con, "issue_names", {})
    if "group" in stages:
        issues = group.name_issues(con, pairs, by_id, run_id=run_id, cache=issues,
                                   log=lambda m: log.event(stage="group", msg=m))
        with store.tx(con):
            store.kv_set(con, "issue_names", issues)
    (out / "issues.json").write_text(json.dumps(issues, indent=2, ensure_ascii=False) + "\n")
    timings["group"] = round(time.time() - t, 3)
    log.event(stage="group", status="done", issues=len({i for i, _ in pairs}), memberships=len(pairs))

    # 5. rank (code only)
    t = time.time()
    ranking = rank.compute(pairs, {r["review_id"]: r["severity"] for r in completed})
    rank.write(ranking, out / "ranking.csv")
    rollup = rank.topic_rollup(ranking)
    export.write_csv(rollup, ["topic", "issues", "complaint_count", "severity_sum", "mean_severity"],
                     out / "topic_rollup.csv")
    names = {k: v.get("name", k) for k, v in issues.items()}
    export.write_csv([{**r, "name": names.get(r["issue_id"], r["issue_id"])} for r in ranking],
                     ["rank", "issue_id", "name", "complaint_count", "severity_sum", "mean_severity", "priority_score"],
                     out / "aggregates.csv")
    timings["rank"] = round(time.time() - t, 3)
    log.event(stage="rank", status="done", top=[r["issue_id"] for r in ranking[:5]])

    # 6. memo (cached by payload hash)
    if "memo" in stages and ranking:
        t = time.time()
        cov = {k: coverage[k] for k in ("source_rows", "in_scope_nonempty", "completed", "quarantined",
                                        "completed_via_exact_text_reuse")}
        cov["completed_share_of_rows"] = round(coverage["completed"] / coverage["source_rows"], 4)
        cov["window"] = "2022-05-17 to 2023-11-15"
        key = hashlib.sha256(json.dumps([config.MODELS["memo"], config.prompt_hash(config.MEMO_PROMPT_VERSION),
                                         ranking[:12], rollup, cov, {k: issues.get(k, {}).get("evidence") for k in
                                                                     [r["issue_id"] for r in ranking[:8]]}],
                                        sort_keys=True, default=str).encode()).hexdigest()[:16]
        cached = store.kv_get(con, f"memo:{key}")
        if cached:
            m = cached
            log.event(stage="memo", status="cached", key=key)
        else:
            m = memo.run(con, ranking=ranking, rollup=rollup, issues=issues, coverage=cov, run_id=run_id,
                         log=lambda s: log.event(stage="memo", msg=s))
            with store.tx(con):
                store.kv_set(con, f"memo:{key}", m)
        export.write_csv(m["claims"], ["claim_id", "issue_id", "metric", "value"], out / "claims.csv")
        (out / "memo.json").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
        if m["memo"]:
            (out / "memo.md").write_text(
                f"# Decision memo\n\n_Run `{run_id}` · generated by `{config.MODELS['memo']}` from saved aggregates "
                f"only; numbers checked by code against `claims.csv` (accepted={m['checks']['accepted']})._\n\n"
                + m["memo"]["memo_markdown"].strip() + "\n")
        timings["memo"] = round(time.time() - t, 3)

    calls = [c for c in export.calls(con, run_ids={run_id})]
    export.write_jsonl(calls, out / "calls.jsonl")
    summary = {"run_id": run_id, "input": inp.name, "input_sha256": sha, "label_config": lc, "models": config.MODELS,
               "prompt_versions": {v: config.prompt_hash(v) for v in (config.ENRICH_PROMPT_VERSION,
                                   config.VERIFY_PROMPT_VERSION, config.GROUP_PROMPT_VERSION, config.MEMO_PROMPT_VERSION)},
               "settings": {"batch_size": config.ENRICH_BATCH_SIZE, "workers": a.workers,
                            "enrich_max_tokens": config.ENRICH_MAX_TOKENS, "spend_cap_usd": config.SPEND_CAP_USD,
                            "fallback_max_fraction": config.FALLBACK_MAX_FRACTION, "thinking": "off (Haiku 4.5 default)",
                            "memo_effort": "low",
                            "enrich_tier": "batch (Message Batches API)" if getattr(a, "batch_api", False) else "standard (synchronous)"},
               "coverage": coverage, "verify": vsummary, "stage_seconds": timings,
               "wall_clock_seconds": round(time.time() - t_start, 3),
               "this_run_calls": {role: sum(1 for c in calls if c["role"] == role) for role in
                                  ("enrich", "verify", "group", "memo")},
               "this_run_failed_attempts": sum(1 for c in calls if c["outcome"] == "failed"),
               "this_run_usage": {"input_tokens": sum(c["input_tokens"] for c in calls),
                                  "output_tokens": sum(c["output_tokens"] for c in calls),
                                  "cost_usd": round(sum(c["cost_usd"] or 0 for c in calls), 6)},
               "ledger_spent_total_usd": round(store.spent_usd(con), 6)}
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    log.event(stage="run", status="done", wall_clock_seconds=summary["wall_clock_seconds"],
              calls=summary["this_run_calls"], cost_usd=summary["this_run_usage"]["cost_usd"])


def cmd_rank(a):
    g = Path(a.grading)
    rows = rank.from_saved(g)
    target = Path(a.out) if a.out else g / "ranking.csv"
    rank.write(rows, target)
    print(f"Recomputed {len(rows)} issues from {g}/records + membership.csv -> {target} (no model calls)")


def cmd_grading(a):
    """Assemble the standardized grading/ folder from a run directory + the state DB."""
    import shutil
    sys.path.insert(0, str(config.ROOT))
    import check_submission as cs
    run_dir, g = Path(a.run_dir), Path(a.out)
    g.mkdir(parents=True, exist_ok=True)
    con = store.connect(Path(a.db))
    lc = config.label_config()
    n = sum(1 for _ in open(run_dir / "records.jsonl", encoding="utf-8"))
    (g / "run.json").write_text(json.dumps({"version": "a5-audit-v1", "analysis_count": n,
        "analysis_sha256": file_sha(Path(a.input)), "classification_input_fields": ["review_text"],
        "allow_multi_issue": False, "label_config": lc, "run_dir": str(run_dir.name),
        "scope_note": a.scope_note}, indent=2) + "\n")
    cs.write_json(g / "ingestion.json", cs.profile(Path(a.input)))
    for old in ("records.jsonl", "records.jsonl.gz", "calls.jsonl", "calls.jsonl.gz"):
        (g / old).unlink(missing_ok=True)
    if a.gzip:
        import gzip
        with open(run_dir / "records.jsonl", "rb") as src, gzip.open(g / "records.jsonl.gz", "wb") as dst:
            shutil.copyfileobj(src, dst)
    else:
        shutil.copy(run_dir / "records.jsonl", g / "records.jsonl")
    for f in ("membership.csv", "ranking.csv", "claims.csv"):
        shutil.copy(run_dir / f, g / f)
    ckpt = Path(a.checkpoints) if a.checkpoints else run_dir
    for f in ("checkpoint_before.json", "checkpoint_after.json"):
        if (ckpt / f).exists():
            shutil.copy(ckpt / f, g / f)
    rows = list(export.calls(con, label_configs_for_enrich={lc}))
    m = export.write_jsonl(rows, g / "calls.jsonl")
    print(f"grading/ written: {n} records, {m} calls ({lc})")


def main(argv=None):
    # Restore Ctrl-C even if a non-interactive parent shell started us with SIGINT ignored
    # (background jobs); otherwise an interrupt could be silently ignored.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    p = argparse.ArgumentParser(prog="pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run stages on an input CSV (makes paid model calls)")
    r.add_argument("--input", required=True)
    r.add_argument("--scope", type=int, help="classify the first N nonempty rows in seeded sample order")
    r.add_argument("--subset", help="classify exactly the IDs in this CSV (dev checkpoints)")
    r.add_argument("--db")
    r.add_argument("--out", required=True)
    r.add_argument("--run-id")
    r.add_argument("--stages", help=f"comma list from {STAGES}")
    r.add_argument("--max-requests", type=int, help="stop enrich after N requests (testing)")
    r.add_argument("--workers", type=int, default=config.MAX_WORKERS)
    r.add_argument("--batch-api", action="store_true", help="enrich via provider Message Batches API (50%% price)")
    r.add_argument("--requests-per-job", type=int, default=400)
    r.add_argument("--max-jobs", type=int)
    r.add_argument("--verify-min", type=int, default=50)
    r.add_argument("--verify-max", type=int, default=1000)
    r.add_argument("--force", default="")
    k = sub.add_parser("rank", help="offline: regenerate ranking.csv from saved records + membership")
    k.add_argument("--grading", default="grading")
    k.add_argument("--out")
    gx = sub.add_parser("grading", help="offline: assemble grading/ from a run dir + state DB")
    gx.add_argument("--run-dir", required=True)
    gx.add_argument("--db", required=True)
    gx.add_argument("--input", required=True)
    gx.add_argument("--out", default="grading")
    gx.add_argument("--checkpoints")
    gx.add_argument("--scope-note", default="")
    gx.add_argument("--gzip", action="store_true")
    pb = sub.add_parser("publish", help="load saved outputs into the Supabase DB (no model calls)")
    pb.add_argument("--run-dir", required=True)
    pb.add_argument("--db", required=True)
    a = p.parse_args(argv)
    if a.cmd == "publish":
        from . import publish
        print(publish.run(a.run_dir, a.db))
        return
    {"run": cmd_run, "rank": cmd_rank, "grading": cmd_grading}[a.cmd](a)


if __name__ == "__main__":
    main()
