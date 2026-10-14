"""100-review cost & runtime calculator.

OFFLINE (default; no API key, no network, no model calls):
    python cost/calculator.py                       # replay saved pilot usage with cost/rates.csv
    python cost/calculator.py --rates my_rates.csv  # edit/copy rates.csv and recompute
    python cost/calculator.py --budget 9 --max-output-tokens 2000 --workers 1 --fallback-fraction 0.02

PAID (explicit, separate command; makes real API calls on cost_100.csv, cold then warm):
    python cost/calculator.py pilot --yes

Formulas: item_cost = billed_units x usd_per_unit (per-1M prices divided by 1,000,000);
input, cache_write, cache_read and output are mutually exclusive Anthropic usage fields
(input_tokens excludes cached tokens; Haiku 4.5 ran with thinking off, so no reasoning tokens).
"""

import argparse
import csv
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "Final Assignment - Spotify Reviews Dataset"
ITEMS = (("input", "input_tokens"), ("cache_write", "cache_write_tokens"),
         ("cache_read", "cache_read_tokens"), ("output", "output_tokens"))

# Full-corpus facts (from ingestion_report.json; recomputed by pipeline ingest)
FULL_ROWS, FULL_NONEMPTY, FULL_EMPTY, FULL_DISTINCT = 660_622, 660_609, 13, 484_189
SCOPE_ROWS, SCOPE_DISTINCT = 100_000, 78_099   # declared 100K classification scope


def load_rates(path):
    rates, meta = {}, {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = (r["model"], r["tier"], r["item"])
            rates[k] = float(r["usd_per_unit"]) / float(r["unit_size"])
            meta[k] = r
    return rates, meta


def load_calls(run_dir):
    p = run_dir / "calls.jsonl"
    return [json.loads(l) for l in open(p, encoding="utf-8")] if p.exists() else []


def cost(call, rates):
    tier = call.get("tier") or "standard"
    return sum((call.get(field) or 0) * rates[(call["model"], tier, item)] for item, field in ITEMS)


def stage_table(calls, rates):
    t = defaultdict(lambda: defaultdict(float))
    for c in calls:
        s = t[c["role"]]
        s["requests"] += c["outcome"] == "succeeded"
        s["attempts"] += 1
        s["failed_attempts"] += c["outcome"] == "failed"
        s["reviews_sent"] += len(c["review_ids"])
        for _, field in ITEMS:
            s[field] += c.get(field) or 0
        s["usd"] += cost(c, rates)
        s["call_seconds"] += max(0.0, (c.get("ended") or 0) - (c.get("started") or 0))
        s["model"] = c["model"]
        s["effort"] = c.get("effort")
        s["prompt"] = c.get("prompt_version")
        s["tier"] = c.get("tier")
    return t


def project(enrich, verify, group, memo, rates, *, texts, verify_n, issues, tier, cache_hit, retry_mult,
            fallback_fraction, out_cap):
    """Per-stage extrapolation from measured per-unit usage. Fixed overhead (group, memo) added once."""
    m = "claude-haiku-4-5"
    n_texts = max(1, enrich["texts"])
    req = texts / 50
    per_text_in = enrich["input_tokens"] / n_texts
    per_text_out = min(enrich["output_tokens"] / n_texts, out_cap / 50)
    sys_tokens = (enrich["cache_read_tokens"] + enrich["cache_write_tokens"]) / max(1, enrich["requests"])
    retry = enrich["retry_fraction"] * retry_mult
    e = (texts * per_text_in * rates[(m, tier, "input")]
         + req * sys_tokens * (cache_hit * rates[(m, tier, "cache_read")] + (1 - cache_hit) * rates[(m, tier, "cache_write")])
         + texts * per_text_out * rates[(m, tier, "output")])
    # retries re-send items in small synchronous requests (standard tier, system prompt cached)
    e_retry = texts * retry * (per_text_in * rates[(m, "standard", "input")] + per_text_out * rates[(m, "standard", "output")]
                               + sys_tokens / 10 * rates[(m, "standard", "cache_read")])
    # capped stronger-model fallback (one review per call, no cache): declared budget item even if unused
    s = "claude-sonnet-5-5"
    e_fb = texts * fallback_fraction * ((sys_tokens + per_text_in * 2) * rates[(s, "standard", "input")]
                                       + 60 * rates[(s, "standard", "output")])
    v = verify["usd_per_review"] * verify_n
    g = group["usd_per_call"] * issues
    mm = memo["usd"]
    return {"enrich": e, "enrich_retries": e_retry, "fallback_reserve": e_fb, "verify": v, "group": g, "memo": mm,
            "total": e + e_retry + e_fb + v + g + mm}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", default="replay", choices=["replay", "pilot"])
    ap.add_argument("--yes", action="store_true", help="required with 'pilot' (paid)")
    ap.add_argument("--rates", default=str(HERE / "rates.csv"))
    ap.add_argument("--cold", default=str(HERE / "pilot_cold"))
    ap.add_argument("--warm", default=str(HERE / "pilot_warm"))
    ap.add_argument("--budget", type=float, default=9.00)
    ap.add_argument("--max-output-tokens", type=int, default=2000, help="enrich output cap per 50-review request")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--fallback-fraction", type=float, default=0.0, help="share of texts sent to Sonnet fallback")
    ap.add_argument("--local-usd-per-hour", type=float, default=0.0, help="laptop/compute cost assumption")
    ap.add_argument("--out", default=str(HERE / "report.md"))
    a = ap.parse_args()

    if a.mode == "pilot":
        if not a.yes:
            sys.exit("Refusing to make paid calls without --yes.")
        py = sys.executable
        inp = str(DATA / "cost_100.csv")
        db = ROOT / "state" / "pilot.db"
        for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
            p.unlink(missing_ok=True)
        for d in (a.cold, a.warm):
            shutil.rmtree(d, ignore_errors=True)
        base = [py, "-m", "pipeline", "run", "--input", inp, "--db", str(db), "--workers", "1", "--verify-min", "20"]
        subprocess.run(base + ["--out", a.cold, "--run-id", "pilot-cold"], check=True, cwd=ROOT)
        subprocess.run(base + ["--out", a.warm, "--run-id", "pilot-warm"], check=True, cwd=ROOT)
        print("Pilot finished; rerun without arguments for the offline report.")

    rates, meta = load_rates(a.rates)
    cold, warm = Path(a.cold), Path(a.warm)
    cc, wc = load_calls(cold), load_calls(warm)
    cs = json.loads((cold / "run_summary.json").read_text())
    ws = json.loads((warm / "run_summary.json").read_text()) if (warm / "run_summary.json").exists() else None
    recs = [json.loads(l) for l in open(cold / "records.jsonl", encoding="utf-8")]
    ids_ok = [r["review_id"] for r in recs]
    src100 = DATA / "cost_100.csv"
    pilot_ids = ([r["review_id"] for r in csv.DictReader(open(src100, encoding="utf-8", newline=""))]
                 if src100.exists() else None)  # dataset not committed; checksum check below still applies

    # ---- evidence files (structured, recomputable) ----
    with open(HERE / "pilot_records.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(HERE / "pilot_calls.jsonl", "w", encoding="utf-8") as f:
        for run, calls in (("cold", cc), ("warm", wc)):
            for c in calls:
                f.write(json.dumps({**c, "pilot_run": run}, ensure_ascii=False) + "\n")
    with open(HERE / "usage.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["pilot_run", "request_id", "role", "model", "tier", "outcome", "item", "billed_units",
                    "usd_per_unit", "item_cost_usd", "rate_source", "rate_checked"])
        for run, calls in (("cold", cc), ("warm", wc)):
            for c in calls:
                tier = c.get("tier") or "standard"
                for item, field in ITEMS:
                    u = c.get(field) or 0
                    k = (c["model"], tier, item)
                    w.writerow([run, c["request_id"], c["role"], c["model"], tier, c["outcome"], item, u,
                                f"{rates[k]:.12f}", f"{u * rates[k]:.8f}", meta[k]["source"], meta[k]["checked"]])

    st = stage_table(cc, rates)
    api_cold = sum(s["usd"] for s in st.values())
    api_warm = sum(cost(c, rates) for c in wc)
    wall_cold = cs["wall_clock_seconds"]
    wall_warm = ws["wall_clock_seconds"] if ws else None
    completed = sum(r["status"] == "completed" for r in recs)
    failed = len(recs) - completed
    e = st["enrich"]
    enrich_texts = len({rid for c in cc if c["role"] == "enrich" and c["outcome"] == "succeeded" and c.get("attempt", 1) == 1
                        for rid in c["review_ids"]})
    retried = sum(len(c["review_ids"]) for c in cc if c["role"] == "enrich" and c.get("attempt", 1) == 2)
    enrich_m = {"texts": enrich_texts, "requests": e["requests"], "input_tokens": e["input_tokens"],
                "output_tokens": e["output_tokens"], "cache_read_tokens": e["cache_read_tokens"],
                "cache_write_tokens": e["cache_write_tokens"], "retry_fraction": retried / max(1, enrich_texts)}
    ver = st["verify"]
    verify_m = {"usd_per_review": ver["usd"] / max(1, ver["reviews_sent"])}
    group_m = {"usd_per_call": st["group"]["usd"] / max(1, st["group"]["requests"])}
    memo_m = {"usd": st["memo"]["usd"]}
    enrich_rate = enrich_texts / max(1e-9, e["call_seconds"])  # texts per second, 1 worker

    scen = []
    for name, texts, vn, issues, tier, hit, rmult, label in (
            ("100K scope, base (Batch API, cache hits)", SCOPE_DISTINCT, 1000, 45, "batch", 0.95, 1, "declared plan"),
            ("100K scope, conservative (Batch, no cache, 3x retries)", SCOPE_DISTINCT, 1000, 45, "batch", 0.0, 3, ""),
            ("100K scope, synchronous standard tier", SCOPE_DISTINCT, 1000, 45, "standard", 0.98, 1, ""),
            ("Full 660,609 with exact-text reuse (484,189), base", FULL_DISTINCT, 1000, 45, "batch", 0.95, 1, ""),
            ("Full 660,609 with exact-text reuse, conservative", FULL_DISTINCT, 1000, 45, "batch", 0.0, 3, ""),
            ("Full 660,609, NO reuse, base", FULL_NONEMPTY, 1000, 45, "batch", 0.95, 1, "no-reuse comparison")):
        p = project(enrich_m, verify_m, group_m, memo_m, rates, texts=texts, verify_n=vn, issues=issues, tier=tier,
                    cache_hit=hit, retry_mult=rmult, fallback_fraction=a.fallback_fraction,
                    out_cap=a.max_output_tokens)
        if tier == "batch":
            hours = f"~{texts / 50 / 400:.0f} jobs; provider SLA <=24h each (assumed 0.5-24 h total)"
        else:
            hours = f"{texts / enrich_rate / max(1, a.workers) / 3600:.1f} h at measured {enrich_rate:.1f} texts/s x {a.workers} worker(s)"
        scen.append((name, texts, p, hours, label))

    L = []
    L.append("# 100-review cost & runtime calculator report\n")
    L.append(f"Generated offline from saved usage (`{cold.name}/calls.jsonl`, `{warm.name}/calls.jsonl`) and `{Path(a.rates).name}`. "
             "No API key or model call is needed to reproduce this file: `python cost/calculator.py`.\n")
    L.append("## Measured pilot (real calls on `cost_100.csv`, unchanged)\n")
    L.append(f"- Input checksum: `{cs['input_sha256']}` (matches manifest: "
             f"{cs['input_sha256'] == 'c884ac3b9be5066995d5063f96ad9af6e5e082975788c1684c4f6b6ea661dd0e'})")
    L.append(f"- Pilot IDs saved: {len(ids_ok)}; identical to cost_100.csv IDs: {ids_ok == pilot_ids if pilot_ids is not None else 'cost_100.csv not present (place the course data to check)'}")
    L.append(f"- Completed: {completed}, failed/quarantined: {failed}; unique texts: {cs['coverage']['completed'] - cs['coverage']['completed_via_exact_text_reuse']}; "
             f"result-cache hits (cold): {cs['coverage']['completed_via_exact_text_reuse']}")
    L.append(f"- label_config: `{cs['label_config']}`; batch size {cs['settings']['batch_size']}; workers {cs['settings']['workers']}; "
             f"enrich output cap {cs['settings']['enrich_max_tokens']} tokens; spend cap ${cs['settings']['spend_cap_usd']:.2f}")
    L.append(f"- Verification sample declared: seeded random 20 of 100 (pilot); full run: 1% capped at 1,000\n")
    L.append("| Stage | Provider / model | Effort | Prompt | Tier | Requests | Attempts | Failed attempts | Reviews sent | Input | Cache write | Cache read | Output | API USD |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for role in ("enrich", "verify", "group", "memo"):
        s = st.get(role)
        if not s:
            continue
        L.append(f"| {role} | Anthropic / `{s['model']}` | {s['effort']} | {s['prompt']} | {s['tier']} | {int(s['requests'])} | "
                 f"{int(s['attempts'])} | {int(s['failed_attempts'])} | {int(s['reviews_sent'])} | {int(s['input_tokens'])} | "
                 f"{int(s['cache_write_tokens'])} | {int(s['cache_read_tokens'])} | {int(s['output_tokens'])} | ${s['usd']:.6f} |")
    L.append(f"| **total (cold)** | | | | | | | | | | | | | **${api_cold:.6f}** |\n")
    local = a.local_usd_per_hour * wall_cold / 3600
    L.append("| Metric | Cold run | Warm run (saved results, same settings) |")
    L.append("|---|---|---|")
    L.append(f"| API spend | ${api_cold:.6f} | ${api_warm:.6f} |")
    L.append(f"| Model calls | {len(cc)} attempts | {len(wc)} (enrich: {sum(c['role'] == 'enrich' for c in wc)}) |")
    L.append(f"| End-to-end wall clock | {wall_cold:.1f} s | {wall_warm if wall_warm is not None else 'n/a'} s |")
    L.append(f"| Enrich stage wall clock | {cs['stage_seconds'].get('enrich', 0):.1f} s | {ws['stage_seconds'].get('enrich', 0) if ws else 'n/a'} s |")
    L.append(f"| Throughput (records / s, end-to-end) | {len(recs) / wall_cold:.2f} | {len(recs) / wall_warm if wall_warm else 0:.0f} |")
    L.append(f"| Cost per 1,000 input rows | ${api_cold / len(recs) * 1000:.4f} | ${api_warm / len(recs) * 1000:.4f} |")
    L.append(f"| Cost per completed record | ${api_cold / max(1, completed):.6f} | ${api_warm / max(1, completed):.6f} |")
    L.append(f"| Local compute (assumption ${a.local_usd_per_hour}/h) | ${local:.4f} | — |")
    L.append("| Unknown costs | none observed (no timeouts with unknown charge) | — |\n")
    L.append("Summed call durations (cold): " + ", ".join(f"{k} {v['call_seconds']:.1f}s" for k, v in st.items())
             + ". These are not wall-clock time; with 1 worker the run is sequential.\n")
    L.append("## Full-run projections (estimates, not measurements)\n")
    L.append(f"Facts: {FULL_ROWS:,} rows; {FULL_NONEMPTY:,} nonempty to classify in a full run; {FULL_EMPTY} empty-text quarantines; "
             f"{FULL_DISTINCT:,} distinct nonempty texts. Declared scope (instructor-approved): first {SCOPE_ROWS:,} nonempty rows "
             f"in the manifest's seeded order = {SCOPE_DISTINCT:,} distinct texts; exact-text reuse then completes every other row with identical text.\n")
    L.append(f"Per-unit inputs measured in the pilot: {enrich_m['input_tokens'] / max(1, enrich_texts):.1f} uncached input + "
             f"{enrich_m['output_tokens'] / max(1, enrich_texts):.1f} output tokens per text; "
             f"{(enrich_m['cache_read_tokens'] + enrich_m['cache_write_tokens']) / max(1, enrich_m['requests']):.0f} system tokens per request; "
             f"retry fraction {enrich_m['retry_fraction']:.3f}; verify ${verify_m['usd_per_review']:.6f}/review; "
             f"group ${group_m['usd_per_call']:.6f}/issue; memo ${memo_m['usd']:.6f} once. "
             f"Controls: budget ${a.budget:.2f}, output cap {a.max_output_tokens}/request, workers {a.workers}, "
             f"fallback fraction {a.fallback_fraction:.2%}.\n")
    L.append("| Scenario | Texts sent | Enrich | Retries | Fallback reserve | Verify | Group | Memo | **Total** | Time | Budget |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, texts, p, hours, label in scen:
        flag = "**EXCEEDS BUDGET**" if p["total"] > a.budget else "within"
        L.append(f"| {name}{' (' + label + ')' if label else ''} | {texts:,} | ${p['enrich']:.2f} | ${p['enrich_retries']:.2f} | "
                 f"${p['fallback_reserve']:.2f} | ${p['verify']:.2f} | ${p['group']:.2f} | ${p['memo']:.2f} | **${p['total']:.2f}** | {hours} | {flag} |")
    L.append("\nBatch-tier rows apply the provider's 50% Batch API price to enrichment as an *estimate*; the pilot itself was billed at the standard tier. "
             "Prompt-cache hit rates in batch mode are best-effort and are measured at the 10K checkpoint (see `../runs/`). "
             "Group and memo are fixed overhead, counted once. Verification is 1% capped at 1,000 reviews, not every review.\n")
    # ---- refresh at 500 / 10K / final (measured, from saved run logs; offline) ----
    L.append("## Estimate refresh from later checkpoints (measured)\n")
    L.append("| Checkpoint | Run | Tier | Enrich requests | Texts sent (attempt 1) | Retry items | Uncached in/text | Output/text | Cache read share | Enrich USD | USD per 1K texts | All-stage USD |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    refresh = {}
    for label, d in (("500", "checkpoint_500"), ("1K resume demo", "resume_demo"), ("10K", "analysis_10000"), ("100K final", "final_100k")):
        calls = load_calls(ROOT / "runs" / d)
        en = [c for c in calls if c["role"] == "enrich" and c["outcome"] == "succeeded"]
        if not en:
            continue
        first = [c for c in en if c.get("attempt", 1) == 1]
        texts = sum(len(c["review_ids"]) for c in first)
        retry = sum(len(c["review_ids"]) for c in en if c.get("attempt", 1) > 1)
        usd = sum(cost(c, rates) for c in en)
        allusd = sum(cost(c, rates) for c in calls)
        cr = sum(c["cache_read_tokens"] for c in en); cw = sum(c["cache_write_tokens"] for c in en)
        tiers = "/".join(sorted({c.get("tier") or "standard" for c in en}))
        refresh[label] = usd / max(1, texts)
        L.append(f"| {label} | `{d}` | {tiers} | {len(en)} | {texts:,} | {retry:,} | {sum(c['input_tokens'] for c in first) / max(1, texts):.1f} | "
                 f"{sum(c['output_tokens'] for c in first) / max(1, texts):.1f} | {cr / max(1, cr + cw):.0%} | ${usd:.4f} | ${1000 * usd / max(1, texts):.4f} | ${allusd:.4f} |")
    if "10K" in refresh:
        rem = SCOPE_DISTINCT * refresh["10K"]
        L.append(f"\nRe-projection after the 10K batch checkpoint: {SCOPE_DISTINCT:,} distinct texts x ${refresh['10K'] * 1000:.4f}/1K = **${rem:.2f}** enrich "
                 f"for the whole 100K scope (before subtracting texts already done in earlier checkpoints); "
                 f"{'within' if rem < a.budget else 'EXCEEDS'} the ${a.budget:.2f} budget. Full 660K with reuse at the same rate: ${FULL_DISTINCT * refresh['10K']:.2f}.\n")
    L.append("## Rates used\n")
    L.append("| model | tier | item | USD per 1M | source | checked |")
    L.append("|---|---|---|---|---|---|")
    for k, r in meta.items():
        L.append(f"| {k[0]} | {k[1]} | {k[2]} | {float(r['usd_per_unit']):.3f} | {r['source']} | {r['checked']} |")
    L.append("\nEdit `rates.csv` (or pass `--rates`) and rerun; with usage fixed, doubling every rate doubles API spend while measured time is unchanged.")
    Path(a.out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:40]))
    print(f"\n... full report: {a.out}")


if __name__ == "__main__":
    main()
