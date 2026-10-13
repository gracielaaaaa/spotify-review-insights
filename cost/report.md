# 100-review cost & runtime calculator report

Generated offline from saved usage (`pilot_cold/calls.jsonl`, `pilot_warm/calls.jsonl`) and `rates.csv`. No API key or model call is needed to reproduce this file: `python cost/calculator.py`.

## Measured pilot (real calls on `cost_100.csv`, unchanged)

- Input checksum: `c884ac3b9be5066995d5063f96ad9af6e5e082975788c1684c4f6b6ea661dd0e` (matches manifest: True)
- Pilot IDs saved: 100; identical to cost_100.csv IDs: True
- Completed: 100, failed/quarantined: 0; unique texts: 100; result-cache hits (cold): 0
- label_config: `claude-haiku-4-5+enrich_v3@6bacf3e4+schema_v3`; batch size 50; workers 1; enrich output cap 2000 tokens; spend cap $9.00
- Verification sample declared: seeded random 20 of 100 (pilot); full run: 1% capped at 1,000

| Stage | Provider / model | Effort | Prompt | Tier | Requests | Attempts | Failed attempts | Reviews sent | Input | Cache write | Cache read | Output | API USD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| enrich | Anthropic / `claude-haiku-4-5` | thinking_off | enrich_v3 | standard | 3 | 3 | 0 | 101 | 3207 | 0 | 14049 | 2077 | $0.014997 |
| verify | Anthropic / `claude-haiku-4-5` | thinking_off | verify_v1 | standard | 1 | 1 | 0 | 20 | 1496 | 0 | 0 | 356 | $0.003276 |
| group | Anthropic / `claude-haiku-4-5` | thinking_off | group_v1 | standard | 18 | 18 | 0 | 50 | 10744 | 0 | 0 | 1817 | $0.019829 |
| memo | Anthropic / `claude-sonnet-5-5` | low | memo_v1 | standard | 1 | 1 | 0 | 0 | 7486 | 0 | 0 | 1374 | $0.028712 |
| **total (cold)** | | | | | | | | | | | | | **$0.066814** |

| Metric | Cold run | Warm run (saved results, same settings) |
|---|---|---|
| API spend | $0.066814 | $0.000000 |
| Model calls | 23 attempts | 0 (enrich: 0) |
| End-to-end wall clock | 84.1 s | 0.022 s |
| Enrich stage wall clock | 20.4 s | 0.002 s |
| Throughput (records / s, end-to-end) | 1.19 | 4545 |
| Cost per 1,000 input rows | $0.6681 | $0.0000 |
| Cost per completed record | $0.000668 | $0.000000 |
| Local compute (assumption $0.0/h) | $0.0000 | — |
| Unknown costs | none observed (no timeouts with unknown charge) | — |

Summed call durations (cold): enrich 19.4s, verify 4.1s, group 45.7s, memo 13.9s. These are not wall-clock time; with 1 worker the run is sequential.

## Full-run projections (estimates, not measurements)

Facts: 660,622 rows; 660,609 nonempty to classify in a full run; 13 empty-text quarantines; 484,189 distinct nonempty texts. Declared scope (instructor-approved): first 100,000 nonempty rows in the manifest's seeded order = 78,099 distinct texts; exact-text reuse then completes every other row with identical text.

Per-unit inputs measured in the pilot: 32.1 uncached input + 20.8 output tokens per text; 4683 system tokens per request; retry fraction 0.010; verify $0.000164/review; group $0.001102/issue; memo $0.028712 once. Controls: budget $9.00, output cap 2000/request, workers 1, fallback fraction 0.00%.

| Scenario | Texts sent | Enrich | Retries | Fallback reserve | Verify | Group | Memo | **Total** | Time | Budget |
|---|---|---|---|---|---|---|---|---|---|---|
| 100K scope, base (Batch API, cache hits) (declared plan) | 78,099 | $5.88 | $0.14 | $0.00 | $0.16 | $0.05 | $0.03 | **$6.27** | ~4 jobs; provider SLA <=24h each (assumed 0.5-24 h total) | within |
| 100K scope, conservative (Batch, no cache, 3x retries) | 78,099 | $9.88 | $0.43 | $0.00 | $0.16 | $0.05 | $0.03 | **$10.55** | ~4 jobs; provider SLA <=24h each (assumed 0.5-24 h total) | **EXCEEDS BUDGET** |
| 100K scope, synchronous standard tier | 78,099 | $11.51 | $0.14 | $0.00 | $0.16 | $0.05 | $0.03 | **$11.90** | 4.2 h at measured 5.2 texts/s x 1 worker(s) | **EXCEEDS BUDGET** |
| Full 660,609 with exact-text reuse (484,189), base | 484,189 | $36.48 | $0.88 | $0.00 | $0.16 | $0.05 | $0.03 | **$37.60** | ~24 jobs; provider SLA <=24h each (assumed 0.5-24 h total) | **EXCEEDS BUDGET** |
| Full 660,609 with exact-text reuse, conservative | 484,189 | $61.25 | $2.65 | $0.00 | $0.16 | $0.05 | $0.03 | **$64.15** | ~24 jobs; provider SLA <=24h each (assumed 0.5-24 h total) | **EXCEEDS BUDGET** |
| Full 660,609, NO reuse, base (no-reuse comparison) | 660,609 | $49.77 | $1.21 | $0.00 | $0.16 | $0.05 | $0.03 | **$51.22** | ~33 jobs; provider SLA <=24h each (assumed 0.5-24 h total) | **EXCEEDS BUDGET** |

Batch-tier rows apply the provider's 50% Batch API price to enrichment as an *estimate*; the pilot itself was billed at the standard tier. Prompt-cache hit rates in batch mode are best-effort and are measured at the 10K checkpoint (see `../runs/`). Group and memo are fixed overhead, counted once. Verification is 1% capped at 1,000 reviews, not every review.

## Estimate refresh from later checkpoints (measured)

| Checkpoint | Run | Tier | Enrich requests | Texts sent (attempt 1) | Retry items | Uncached in/text | Output/text | Cache read share | Enrich USD | USD per 1K texts | All-stage USD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 500 | `checkpoint_500` | standard | 12 | 479 | 19 | 29.1 | 19.7 | 92% | $0.0765 | $0.1596 | $0.1505 |
| 1K resume demo | `resume_demo` | standard | 22 | 885 | 32 | 28.5 | 19.9 | 100% | $0.1301 | $0.1470 | $0.1301 |
| 10K | `analysis_10000` | batch/standard | 189 | 8,719 | 123 | 34.6 | 20.4 | 87% | $0.7347 | $0.0843 | $0.8562 |

Re-projection after the 10K batch checkpoint: 78,099 distinct texts x $0.0843/1K = **$6.58** enrich for the whole 100K scope (before subtracting texts already done in earlier checkpoints); within the $9.00 budget. Full 660K with reuse at the same rate: $40.80.

## Rates used

| model | tier | item | USD per 1M | source | checked |
|---|---|---|---|---|---|
| claude-haiku-4-5 | standard | input | 1.000 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-07 |
| claude-haiku-4-5 | standard | cache_write | 1.250 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-07 |
| claude-haiku-4-5 | standard | cache_read | 0.100 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-07 |
| claude-haiku-4-5 | standard | output | 5.000 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-07 |
| claude-haiku-4-5 | batch | input | 0.500 | https://platform.claude.com/docs/en/build-with-claude/batch-processing#pricing | 2026-10-07 |
| claude-haiku-4-5 | batch | cache_write | 0.625 | https://platform.claude.com/docs/en/build-with-claude/batch-processing#pricing | 2026-10-07 |
| claude-haiku-4-5 | batch | cache_read | 0.050 | https://platform.claude.com/docs/en/build-with-claude/batch-processing#pricing | 2026-10-07 |
| claude-haiku-4-5 | batch | output | 2.500 | https://platform.claude.com/docs/en/build-with-claude/batch-processing#pricing | 2026-10-07 |
| claude-sonnet-5-5 | standard | input | 2.000 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-07 |
| claude-sonnet-5-5 | standard | cache_write | 2.500 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-07 |
| claude-sonnet-5-5 | standard | cache_read | 0.200 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-07 |
| claude-sonnet-5-5 | standard | output | 10.000 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-07 |
| claude-sonnet-5-5 | batch | input | 1.000 | https://platform.claude.com/docs/en/build-with-claude/batch-processing#pricing | 2026-10-07 |
| claude-sonnet-5-5 | batch | cache_write | 1.250 | https://platform.claude.com/docs/en/build-with-claude/batch-processing#pricing | 2026-10-07 |
| claude-sonnet-5-5 | batch | cache_read | 0.100 | https://platform.claude.com/docs/en/build-with-claude/batch-processing#pricing | 2026-10-07 |
| claude-sonnet-5-5 | batch | output | 5.000 | https://platform.claude.com/docs/en/build-with-claude/batch-processing#pricing | 2026-10-07 |

Edit `rates.csv` (or pass `--rates`) and rerun; with usage fixed, doubling every rate doubles API spend while measured time is unchanged.
