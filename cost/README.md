# Cost and runtime calculator

| Command | Paid? | What it does |
|---|---|---|
| `.venv/bin/python cost/calculator.py` | **no** (default) | Offline replay: recomputes every cost from saved usage (`pilot_cold/`, `pilot_warm/`, `../runs/*/calls.jsonl`) × `rates.csv`, and rewrites `report.md`, `usage.csv`, `pilot_records.jsonl` and `pilot_calls.jsonl`. No API key, no network. |
| `... --rates my_rates.csv --budget 9 --max-output-tokens 2000 --workers 1 --fallback-fraction 0.02 --local-usd-per-hour 0` | no | Editable rates and controls. Scenarios over budget are marked **EXCEEDS BUDGET**. |
| `.venv/bin/python cost/calculator.py pilot --yes` | **yes** | Explicit paid pilot. Deletes the pilot DB, runs `cost_100.csv` cold (1 worker, empty result cache), then warm. Refuses to run without `--yes`. |

**Formulas.**
- Each line item: `item_cost = billed_units × usd_per_unit`, where `usd_per_unit = usd_per_1M / 1,000,000`.
- Billing items are mutually exclusive Anthropic usage fields: `input_tokens` (uncached), `cache_creation_input_tokens`, `cache_read_input_tokens` and `output_tokens`. There are no separate reasoning tokens, because thinking is off.
- The tier in `rates.csv` (standard or batch) is applied per call exactly as recorded in the call log.

**Check it yourself.**
- Doubling every `usd_per_unit` doubles API spend, and measured times are unchanged (verified: $0.066814 → $0.133628).
- Changing projected volume never changes the measured pilot section.

**Files.**
- `pilot_records.jsonl`: 100 pilot statuses.
- `pilot_calls.jsonl`: every cold and warm attempt.
- `usage.csv`: one row per call × billing item, with the rate source and its check date.
- `rates.csv`: editable rates with dated official source links.
- `report.md`: measured tables, the 500/10K/100K refresh, and full-run scenarios.

Rates were checked on 2026-10-07 against Anthropic's pricing pages for `claude-haiku-4-5` and `claude-sonnet-5-5`. Batch rates are 50% of standard.
