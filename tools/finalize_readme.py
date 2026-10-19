"""Render README.md from docs/README.template.md using ONLY saved outputs (no model calls).

  .venv/bin/python tools/finalize_readme.py --run-dir runs/final_100k --selfcheck /tmp/self-check.json
"""

import argparse
import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(p, default=None):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--selfcheck")
    ap.add_argument("--out", default=str(ROOT / "README.md"))
    a = ap.parse_args()
    rd = Path(a.run_dir)
    s = load(rd / "run_summary.json")
    cov = s["coverage"]
    memo = load(rd / "memo.json")
    gold = load(ROOT / "evals" / "golden_eval.json", {}).get("summary")
    inj = load(ROOT / "evals" / "injection" / "results.json", {}).get("summary")
    ver = load(rd / "verify" / "summary.json")
    planted = load(rd / "verify" / "planted_errors.json")
    pilot_c, pilot_w = load(ROOT / "cost/pilot_cold/run_summary.json"), load(ROOT / "cost/pilot_warm/run_summary.json")
    sc = load(a.selfcheck) if a.selfcheck else None

    spend = 0.0
    for db in (ROOT / "state").glob("*.db"):
        if db.name.startswith(("fake", "controls")):
            continue
        try:
            spend += sqlite3.connect(db).execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls").fetchone()[0]
        except sqlite3.DatabaseError:
            pass

    rows = cov["source_rows"]
    f = {
        "RECOMMENDATION": memo["memo"]["recommendation"] if memo and memo.get("memo") else "_memo pending_",
        "SOURCE_ROWS": f"{rows:,}",
        "COMPLETED": f"{cov['completed']:,}",
        "COMPLETED_PCT": f"{100 * cov['completed'] / rows:.1f}%",
        "COMPLETED_PCT_NONEMPTY": f"{100 * cov['completed'] / 660609:.1f}%",
        "COMPLETED_IN_SCOPE": f"{cov['completed_in_scope']:,}",
        "REUSE": f"{cov['completed_via_exact_text_reuse']:,}",
        "QUARANTINED": f"{cov['quarantined']:,}",
        "QUARANTINE_REASONS": "; ".join(f"`{k}` {v:,}" for k, v in sorted(cov["quarantine_reasons"].items(), key=lambda x: -x[1])),
        "UNRESOLVED": f"{sum(v for k, v in cov['quarantine_reasons'].items() if k not in ('empty_review_text', 'not_in_declared_classification_scope')):,} "
                      "(model-stage quarantines + any pending in-scope rows; counted as incomplete)",
        "GOLD_LINE": (f"{gold['topic_agreement_exact']:.0%} / {gold['intent_agreement_exact']:.0%} / {gold['severity_exact']:.0%}; "
                      f"MAE {gold['severity_mae']} (accepted-label topic {gold['topic_agreement_accepted']:.0%}, n=50; student labels, see provenance)")
        if gold else "_pending_",
        "VERIFY_LINE": (f"n={ver['n']}: topic {ver['topic_agreement']:.1%}, intent {ver['intent_agreement']:.1%}, severity {ver['severity_agreement']:.1%}, "
                        f"severity MAE {ver['severity_mae']}; {ver['disagreements']} disagreements in `verify/disagreements.csv`") if ver else "_pending_",
        "INJECTION_LINE": f"{inj['resisted']}/{inj['cases']} resisted; {inj['flagged_needs_review']}/{inj['cases']} flagged needs_review" if inj else "_pending_",
        "PLANTED_LINE": (f"{planted['flagged']}/{planted['planted']} deliberately wrong topic labels flagged by the code comparison against the blind verifier "
                         "(planted only on cases where the verifier agreed with the original, so this tests the comparison path)") if planted else "_pending_",
        "PILOT_LINE": (f"${pilot_c['this_run_usage']['cost_usd']:.4f} / {pilot_c['wall_clock_seconds']:.1f}s cold → "
                       f"${pilot_w['this_run_usage']['cost_usd']:.4f} / {pilot_w['wall_clock_seconds']:.2f}s warm, "
                       f"{pilot_w['this_run_calls']['enrich']} warm enrich calls ([report](cost/report.md))") if pilot_c and pilot_w else "_pending_",
        "TOTAL_SPEND": f"${spend:.2f}",
        "FINAL_WALL": f"{s['wall_clock_seconds'] / 60:.1f} min for this invocation (Batch API jobs; see `runs/final_100k/run_log.jsonl`)",
        "FULL_ESTIMATE": "about $40 (484,189 distinct texts × $0.084 per 1K measured at the 10K batch checkpoint; see cost/report.md)",
        "LABEL_CONFIG": s["label_config"],
        "DEPS": ", ".join(l.strip() for l in open(ROOT / "requirements.txt") if l.strip()),
        "FALLBACK_QUOTES": "",
        "SELFCHECK": "",
        "TRACE": "",
        "ERROR_ANALYSIS": "",
    }
    recs = {}
    with open(rd / "records.jsonl", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r["status"] == "completed" and "cache_source_id" not in r:
                recs[r["review_id"]] = r
    f["FALLBACK_QUOTES"] = f"{sum('quote_fallback' in (r.get('review_reason') or '') for r in recs.values()):,}"

    if sc:
        c = sc["coverage"]
        f["SELFCHECK"] = (f"`check_submission.py check` on `grading/` (reference built from the full CSV): status **{sc['status']}**; "
                          f"issue counts `{json.dumps(sc['issue_counts'])}`; working coverage point candidate **{sc['working_coverage_point_candidate']}**.\n\n"
                          f"| expected | valid completed | quarantined | missing | duplicates | invalid/unresolved | valid cache reuses | labelable completion |\n|---|---|---|---|---|---|---|---|\n"
                          f"| {c['expected']:,} | {c['valid_completed']:,} | {c['quarantined']:,} | {c['missing']} | {c['duplicate_ids']} | "
                          f"{c['invalid_or_unresolved']} | {c['valid_cache_reuses']:,} | {c['labelable_completion_fraction']} |\n\n"
                          "`unfinished_classification` is expected: it counts the nonempty rows outside the declared 100K scope "
                          "(and any unresolved in-scope rows). The full report is not committed inside `grading/`, as the contract says; "
                          "regenerate it with the commands below.")

    # trace: first memo-cited review that is a verified origin record
    if memo and memo.get("memo"):
        claims = {c["claim_id"]: c for c in csv.DictReader(open(rd / "claims.csv"))}
        ranking = {r["issue_id"]: r for r in csv.DictReader(open(rd / "ranking.csv"))}
        member = {r["review_id"]: r["issue_id"] for r in csv.DictReader(open(rd / "membership.csv"))}
        vrows = {r["review_id"]: r for r in csv.DictReader(open(rd / "verify" / "comparison.csv"))} if (rd / "verify" / "comparison.csv").exists() else {}
        cited = [x for x in memo["memo"]["cited_review_ids"] if x in recs]
        pick = next((x for x in cited if x in vrows), cited[0] if cited else None)
        if pick:
            r, iid = recs[pick], member.get(pick)
            calls = [json.loads(l) for l in open(rd / "calls.jsonl")]
            call = next((c for c in calls if c["role"] == "enrich" and pick in c["review_ids"] and c["outcome"] == "succeeded"), None)
            if call is None:
                for p in sorted((ROOT / "runs").glob("*/calls.jsonl")):
                    call = next((json.loads(l) for l in open(p) if pick in l and '"enrich"' in l and '"succeeded"' in l), None)
                    if call:
                        break
            cl = [c for c in claims.values() if c["issue_id"] == iid]
            v = vrows.get(pick)
            f["TRACE"] = "\n".join([
                f"1. **Source:** `review_id` `{pick}` · `source_sha256` `{r['source_sha256'][:16]}…`",
                f"2. **Enrichment** (call `{call['request_id'] if call else 'n/a'}`, {call['tier'] if call else ''} tier, `{s['label_config']}`): "
                f"topic **{r['topic']}** / {r.get('subtopic')} · intent **{r['intent']}** · severity **{r['severity']}** · sentiment {r['sentiment']} · "
                f"quote “{r['evidence_quote'][:160]}” (an exact substring, checked by code) · entities {r['entities']}",
                f"3. **Verification:** " + (f"in the blind sample; the verifier said {v['verify_topic']}/{v['verify_intent']}/{v['verify_severity']} "
                                             f"(all_agree={v['all_agree']})" if v else "not in the seeded 1% verifier sample"),
                f"4. **Issue membership:** `{iid}` (`grading/membership.csv`)",
                f"5. **Ranking:** rank {ranking[iid]['rank']}: {int(ranking[iid]['complaint_count']):,} complaints × mean {ranking[iid]['mean_severity']} = "
                f"priority {int(ranking[iid]['priority_score']):,} (`grading/ranking.csv`)" if iid in ranking else "5. **Ranking:** n/a",
                f"6. **Memo claim:** " + ", ".join(f"`{c['claim_id']}` {c['metric']}={c['value']}" for c in cl) + " (`grading/claims.csv`); the memo cites this review ID as evidence.",
            ])
    # error analysis from golden + verifier
    ea = []
    if gold:
        ea.append(f"**Golden 50** ([full table](evals/golden_eval.md)): topic exact {gold['topic_agreement_exact']:.0%} "
                  f"({gold['topic_agreement_accepted']:.0%} counting accepted alternates), intent {gold['intent_agreement_exact']:.0%}, "
                  f"severity exact {gold['severity_exact']:.0%} (MAE {gold['severity_mae']}), sentiment MAE {gold['sentiment_mae']} "
                  f"(within ±0.3: {gold['sentiment_within_tolerance_0.3']:.0%}), quote substring {gold['quote_substring_rate']:.0%}, "
                  f"{gold['ambiguous_cases']} ambiguous cases. needs_review as a predictor of ambiguity: precision "
                  f"{gold['needs_review_vs_ambiguous']['precision']}, recall {gold['needs_review_vs_ambiguous']['recall']}.")
        cases = load(ROOT / "evals" / "golden_eval.json")["cases"]
        bad = [c for c in cases if not c["topic_exact"] or not c["intent_exact"]][:6]
        if bad:
            ea.append("\nConcrete disagreements (golden → predicted):")
            for c in bad:
                ea.append(f"- `{c['review_id'][:8]}` “{c['text'][:90]}”: {c['gold_topic']}/{c['gold_intent']}/{c['gold_severity']} → "
                          f"{c['pred_topic']}/{c['pred_intent']}/{c['pred_severity']}")
    if ver:
        ea.append(f"\n**Blind verifier** (n={ver['n']}): the most common disagreements are listed in "
                  f"[`runs/final_100k/verify/disagreements.csv`]({rd.as_posix()}/verify/disagreements.csv).")
    q = load(ROOT / "evals" / "failure_case.json")
    if q:
        ea.append(f"\n**One failed / ambiguous case and its handling:** {q['text']}")
    f["ERROR_ANALYSIS"] = "\n".join(ea) or "_pending_"

    t = (ROOT / "docs" / "README.template.md").read_text()
    for k, v in f.items():
        t = t.replace("{{" + k + "}}", str(v))
    Path(a.out).write_text(t, encoding="utf-8")
    left = [k for k in f if "{{" + k + "}}" in t]
    print(f"README written ({len(t):,} chars); unfilled: {left}")


if __name__ == "__main__":
    main()
