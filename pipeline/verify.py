"""Stage 3 - Verify (role=verify). A separate prompt re-labels a declared, seeded random
sample WITHOUT seeing the enricher's answer. Code compares; disagreements are reported.
Planted-error test: a separate copy of the enriched labels with deliberately wrong
labels is compared against the same blind verifier output (synthetic; never exported
to business aggregates)."""

import hashlib
import json
import random
import time

from . import config, llm, store

ITEM = {"type": "object", "properties": {
    "i": {"type": "integer"}, "topic": {"type": "string", "enum": list(config.TOPICS)},
    "intent": {"type": "string", "enum": list(config.INTENTS)},
    "severity": {"type": "integer", "enum": [1, 2, 3, 4, 5]}},
    "required": ["i", "topic", "intent", "severity"], "additionalProperties": False}
SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": ITEM}},
          "required": ["items"], "additionalProperties": False}
BATCH = 25


def sample(origin_records, fraction=config.VERIFY_SAMPLE_FRACTION, minimum=50, maximum=1000, seed="verify-v1"):
    """Seeded random sample of ORIGIN records (ones with their own model call)."""
    ids = sorted(origin_records)
    n = max(minimum, min(maximum, round(len(ids) * fraction)))
    rng = random.Random(seed)
    return sorted(rng.sample(ids, min(n, len(ids))))


def run(con, sample_ids, texts, *, run_id, log=print, cache_key="verify"):
    """texts: {review_id: review_text}. Returns {review_id: {topic,intent,severity}}.
    Saved per batch in kv so a rerun reuses completed verifier results."""
    rates = llm.load_rates()
    ledger = llm.Ledger(spent=store.spent_usd(con), cap=config.SPEND_CAP_USD)
    system = config.prompt_text(config.VERIFY_PROMPT_VERSION)
    vcfg = f"{config.MODELS['verify']}+{config.VERIFY_PROMPT_VERSION}@{config.prompt_hash(config.VERIFY_PROMPT_VERSION)}"
    saved = store.kv_get(con, f"{cache_key}:{vcfg}", {})
    todo = [rid for rid in sample_ids if rid not in saved]
    log(f"[verify] sample={len(sample_ids)} cached={len(sample_ids) - len(todo)} todo={len(todo)} cfg={vcfg}")
    for k in range(0, len(todo), BATCH):
        chunk = todo[k:k + BATCH]
        user = f"Label these {len(chunk)} reviews (i = 1..{len(chunk)}).\n\n" + "\n".join(
            f"[{i}] {texts[rid].replace(chr(10), ' ⏎ ')}" for i, rid in enumerate(chunk, 1))
        fails = []
        try:
            msg, usage = llm.call(role="verify", model=config.MODELS["verify"], system=system, user=user,
                                  max_tokens=config.VERIFY_MAX_TOKENS, ledger=ledger, rates=rates, schema=SCHEMA,
                                  on_attempt=fails.append)
            items = {it["i"]: it for it in json.loads(llm.text_of(msg)).get("items", [])
                     if isinstance(it, dict) and type(it.get("i")) is int}
            outcome, err = "succeeded", None
        except llm.BudgetExceeded:
            raise
        except Exception as e:
            usage, items, outcome, err = None, {}, "failed", f"{type(e).__name__}: {e}"[:200]
        with store.tx(con):
            for a in fails:
                store.log_call(con, run_id=run_id, stage="verify", role="verify", model=config.MODELS["verify"],
                               effort="thinking_off", phase="initial", label_config=vcfg,
                               prompt_version=config.VERIFY_PROMPT_VERSION, review_ids=chunk, tier="standard", **a)
            if usage:
                store.log_call(con, run_id=run_id, stage="verify", role="verify", model=config.MODELS["verify"],
                               effort="thinking_off", phase="initial", outcome=outcome, label_config=vcfg,
                               prompt_version=config.VERIFY_PROMPT_VERSION, review_ids=chunk,
                               input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                               cache_read_tokens=usage["cache_read_tokens"],
                               cache_write_tokens=usage["cache_write_tokens"], tier="standard",
                               attempt=usage["attempt"], started=usage["started"], ended=time.time(), error=err,
                               cost_usd=usage["cost_usd"], request_id=usage["request_id"])
            for i, rid in enumerate(chunk, 1):
                it = items.get(i)
                if it and it["topic"] in config.TOPICS and it["intent"] in config.INTENTS and it["severity"] in (1, 2, 3, 4, 5):
                    saved[rid] = {"topic": it["topic"], "intent": it["intent"], "severity": it["severity"]}
            store.kv_set(con, f"{cache_key}:{vcfg}", saved)
        log(f"[verify] {min(k + BATCH, len(todo))}/{len(todo)} spent=${ledger.spent:.4f}")
    return {rid: saved[rid] for rid in sample_ids if rid in saved}, vcfg


def compare(enriched, verified):
    """Code comparison. Returns summary + per-case rows."""
    rows, agree = [], {"topic": 0, "intent": 0, "severity": 0}
    sev_abs = 0
    for rid in sorted(verified):
        e, v = enriched[rid], verified[rid]
        row = {"review_id": rid}
        for f in ("topic", "intent", "severity"):
            row[f"enrich_{f}"], row[f"verify_{f}"] = e[f], v[f]
            row[f"{f}_agree"] = e[f] == v[f]
            agree[f] += e[f] == v[f]
        sev_abs += abs(e["severity"] - v["severity"])
        row["all_agree"] = all(row[f"{f}_agree"] for f in ("topic", "intent", "severity"))
        rows.append(row)
    n = len(rows) or 1
    summary = {"n": len(rows), **{f"{f}_agreement": round(agree[f] / n, 4) for f in agree},
               "severity_mae": round(sev_abs / n, 4),
               "full_agreement": round(sum(r["all_agree"] for r in rows) / n, 4),
               "disagreements": sum(not r["all_agree"] for r in rows)}
    return summary, rows


def planted_errors(enriched, verified, n=20, seed="planted-v1"):
    """Separate TEST COPY: corrupt n enriched labels on purpose (topic -> a different topic,
    intent praise<->complaint) and check the code comparison flags each one."""
    rng = random.Random(seed)
    ids = sorted(rid for rid in verified if enriched[rid]["topic"] == verified[rid]["topic"])
    chosen = sorted(rng.sample(ids, min(n, len(ids))))
    cases = []
    for rid in chosen:
        orig = enriched[rid]
        wrong_topic = sorted(t for t in config.TOPICS if t != orig["topic"])[
            int(hashlib.sha256(rid.encode()).hexdigest(), 16) % 7]
        planted = {**orig, "topic": wrong_topic}
        flagged = planted["topic"] != verified[rid]["topic"]
        cases.append({"review_id": rid, "synthetic": True, "original_topic": orig["topic"],
                      "planted_topic": wrong_topic, "verifier_topic": verified[rid]["topic"], "flagged": flagged})
    return {"planted": len(cases), "flagged": sum(c["flagged"] for c in cases), "cases": cases}
