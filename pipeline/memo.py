"""Stage 6 - Recommend (role=memo). The memo model gets ONLY saved aggregates + a bounded
evidence pack. Code builds claims.csv (every issue-level number the memo may use) BEFORE
the call, then rejects the memo if it cites unknown claim/review IDs or states an issue
number in a cited sentence that does not match the claim table."""

import json
import re
import time

from . import config, llm, store

SCHEMA = {"type": "object", "properties": {
    "recommendation": {"type": "string"}, "memo_markdown": {"type": "string"},
    "cited_claim_ids": {"type": "array", "items": {"type": "string"}},
    "cited_review_ids": {"type": "array", "items": {"type": "string"}}},
    "required": ["recommendation", "memo_markdown", "cited_claim_ids", "cited_review_ids"],
    "additionalProperties": False}

METRICS = ("complaint_count", "severity_sum", "mean_severity", "priority_score")


def build_claims(ranking, top=12):
    claims, k = [], 1
    for r in ranking[:top]:
        for m in METRICS:
            claims.append({"claim_id": f"C{k:03d}", "issue_id": r["issue_id"], "metric": m, "value": str(r[m])})
            k += 1
    return claims


def evidence_pack(ranking, issues, top=8, per_issue=3):
    pack = []
    for r in ranking[:top]:
        info = issues.get(r["issue_id"], {})
        ev = info.get("evidence", [])
        reps = set(info.get("representative_ids", []))
        chosen = [e for e in ev if e["review_id"] in reps] + [e for e in ev if e["review_id"] not in reps]
        pack.append({"issue_id": r["issue_id"], "name": info.get("name", r["issue_id"]),
                     "quotes": [{"review_id": e["review_id"], "quote": e["quote"][:220]} for e in chosen[:per_issue]]})
    return pack


def check(memo, claims, pack):
    """Returns list of problems (empty = accepted)."""
    problems = []
    claim_ids = {c["claim_id"]: c for c in claims}
    review_ids = {q["review_id"] for p in pack for q in p["quotes"]}
    text = memo["memo_markdown"]
    for cid in set(re.findall(r"\[(C\d{3})\]", text)) | set(memo["cited_claim_ids"]):
        if cid not in claim_ids:
            problems.append(f"unknown claim id {cid}")
    for rid in set(memo["cited_review_ids"]):
        if rid not in review_ids:
            problems.append(f"review id not in evidence pack {rid}")
    for rid in re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text):
        if rid not in review_ids:
            problems.append(f"memo text cites unknown review id {rid}")
    # number immediately before a claim citation must equal the claim value
    for num, cid in re.findall(r"([0-9][0-9,]*\.?[0-9]*)[^0-9\[\]]{0,40}\[(C\d{3})\]", text):
        if cid in claim_ids:
            v = claim_ids[cid]["value"]
            n = num.replace(",", "").rstrip(".")
            try:
                if abs(float(n) - float(v)) > (0.005 if "." in v else 0.5):
                    problems.append(f"{cid}: memo says {num}, claim value {v}")
            except ValueError:
                problems.append(f"{cid}: unparseable number {num}")
    if not re.findall(r"\[(C\d{3})\]", text):
        problems.append("memo cites no claim IDs")
    return problems


def run(con, *, ranking, rollup, issues, coverage, run_id, log=print, max_attempts=2):
    claims = build_claims(ranking)
    pack = evidence_pack(ranking, issues)
    names = {r["issue_id"]: issues.get(r["issue_id"], {}).get("name", r["issue_id"]) for r in ranking[:12]}
    payload = {"coverage": coverage, "topic_rollup": rollup,
               "ranking_top12": [{**r, "name": names[r["issue_id"]]} for r in ranking[:12]],
               "claims": claims, "evidence_pack": pack}
    user = json.dumps(payload, ensure_ascii=False, indent=1)
    rates = llm.load_rates()
    ledger = llm.Ledger(spent=store.spent_usd(con), cap=config.SPEND_CAP_USD)
    system = config.prompt_text(config.MEMO_PROMPT_VERSION)
    feedback = ""
    for attempt in range(1, max_attempts + 1):
        fails = []
        msg, usage = llm.call(role="memo", model=config.MODELS["memo"], system=system, user=user + feedback,
                              max_tokens=4000, ledger=ledger, rates=rates, schema=SCHEMA, on_attempt=fails.append,
                              extra={"output_config": {"effort": "low", "format": {"type": "json_schema",
                                                                                     "schema": SCHEMA}}})
        try:
            memo = json.loads(llm.text_of(msg))
            problems = check(memo, claims, pack)
        except json.JSONDecodeError:
            memo, problems = None, ["invalid_json"]
        with store.tx(con):
            for a in fails:
                store.log_call(con, run_id=run_id, stage="memo", role="memo", model=config.MODELS["memo"],
                               effort="low", phase="initial", label_config=None,
                               prompt_version=config.MEMO_PROMPT_VERSION, review_ids=[], tier="standard", **a)
            store.log_call(con, run_id=run_id, stage="memo", role="memo", model=config.MODELS["memo"], effort="low",
                           phase="initial", outcome="succeeded", label_config=None,
                           prompt_version=config.MEMO_PROMPT_VERSION, review_ids=[],
                           input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                           cache_read_tokens=usage["cache_read_tokens"], cache_write_tokens=usage["cache_write_tokens"],
                           tier="standard", attempt=attempt, started=usage["started"], ended=time.time(),
                           error=("; ".join(problems)[:500] if problems else None), cost_usd=usage["cost_usd"],
                           request_id=usage["request_id"])
        log(f"[memo] attempt {attempt}: {len(problems)} problems {problems[:3]}")
        if not problems:
            return {"memo": memo, "claims": claims, "evidence_pack": pack, "payload": payload,
                    "checks": {"accepted": True, "attempts": attempt}}
        feedback = "\n\nYour previous memo was rejected by the code checker for: " + "; ".join(problems[:10]) + \
                   ". Fix these and return the full JSON again."
    return {"memo": memo, "claims": claims, "evidence_pack": pack, "payload": payload,
            "checks": {"accepted": False, "attempts": max_attempts, "problems": problems}}
