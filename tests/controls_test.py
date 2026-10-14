"""Zero-cost demonstration of the run controls with a fake model client (no API key, no spend).
Synthetic: results go to evals/controls/ and separate state DBs, never into business outputs.

  .venv/bin/python tests/controls_test.py
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests import fake_llm  # noqa: E402
from pipeline import config, enrich, llm, store  # noqa: E402
import anthropic  # noqa: E402
import httpx2 as httpx  # noqa: E402

D = ROOT / "Final Assignment - Spotify Reviews Dataset"
OUT = ROOT / "evals" / "controls"
OUT.mkdir(parents=True, exist_ok=True)
SUBSET = ROOT / "evals" / "resume" / "resume_demo_1000.csv"   # committed copy of 1,000 source rows


def fresh_db(name):
    p = ROOT / "state" / f"controls_{name}.db"
    for q in (p, Path(str(p) + "-wal"), Path(str(p) + "-shm")):
        q.unlink(missing_ok=True)
    con = store.connect(p)
    from pipeline import ingest
    ingest.ingest(SUBSET, con, ROOT / "state" / f"controls_{name}_ingest")
    return con, [r[0] for r in con.execute("SELECT review_id FROM source WHERE empty=0 ORDER BY row_idx")]


def calls(con):
    return [dict(r) for r in con.execute("SELECT request_id, outcome, attempt, error, cost_usd FROM calls ORDER BY started")]


results = {}

# 1. Spending cap: reservation of worst-case cost blocks admission before the cap is crossed.
fake_llm.install()
con, ids = fresh_db("spend")
config.SPEND_CAP_USD = 0.02        # tiny cap for the demo (normal: $9.00)
try:
    enrich.run(con, ids, run_id="controls-spend", phase="initial", log=lambda m: None)
    stopped = None
except llm.BudgetExceeded as e:
    stopped = str(e)
done = con.execute("SELECT COUNT(*) FROM results").fetchone()[0]
results["spend_cap"] = {"cap_usd": 0.02, "stopped_with": stopped, "requests_admitted": len(calls(con)),
                        "texts_saved_before_stop": done, "ledger_spent_usd": round(store.spent_usd(con), 6),
                        "ledger_never_exceeds_cap": store.spent_usd(con) <= 0.02,
                        "note": "Each request reserves worst-case cost (input estimate + max_tokens output) before dispatch; "
                                "admission stops when spent + reserved + next > cap. Saved batches remain for resume."}
config.SPEND_CAP_USD = 9.00

# 2. Transient errors: two 429s then success -> bounded exponential backoff, every failed attempt logged.
class Flaky(fake_llm.Messages):
    n = 0
    def create(self, **kw):
        Flaky.n += 1
        if Flaky.n <= 2:
            req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.RateLimitError("rate limited (synthetic)", response=httpx.Response(429, request=req, headers={"retry-after": "0"}), body=None)
        return super().create(**kw)
c = fake_llm.FakeClient(); c.messages = Flaky(); llm._client = c
llm.time.sleep = lambda s: None      # skip real waiting in the demo; delays are still computed
con, ids = fresh_db("retry")
enrich.run(con, ids[:50], run_id="controls-retry", phase="initial", log=lambda m: None)
cl = calls(con)
results["transient_retry"] = {"attempts": [{k: x[k] for k in ("outcome", "attempt", "error")} for x in cl],
                              "texts_saved": con.execute("SELECT COUNT(*) FROM results").fetchone()[0],
                              "max_transient_retries": config.TRANSIENT_RETRIES}

# 3. Invalid output: one item always returns a disallowed label -> exactly one retry, then quarantine.
class Bad(fake_llm.Messages):
    def create(self, **kw):
        m = super().create(**kw)
        lines = m.content[0].text.splitlines()
        lines[0] = lines[0].split("|", 1)[0] + "|not_a_real_subtopic|complaint|-0.5|2|0|"
        m.content[0].text = "\n".join(lines)
        return m
c = fake_llm.FakeClient(); c.messages = Bad(); llm._client = c
con, ids = fresh_db("invalid")
s = enrich.run(con, ids[:50], run_id="controls-invalid", phase="initial", log=lambda m: None)
q = [dict(r) for r in con.execute("SELECT origin_review_id, reason, attempts FROM quarantine")]
results["invalid_output"] = {"summary": s, "calls": [{k: x[k] for k in ("outcome", "attempt", "error")} for x in calls(con)],
                             "quarantined": q}

results["synthetic"] = True
(OUT / "controls_test.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
print(json.dumps({k: (v if k != "transient_retry" else v["attempts"]) for k, v in results.items() if k != "invalid_output"}, indent=1, default=str)[:1500])
print("invalid_output:", results["invalid_output"]["summary"], results["invalid_output"]["quarantined"][:2])
