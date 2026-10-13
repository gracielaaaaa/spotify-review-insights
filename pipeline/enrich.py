"""Stage 2 - Enrich (role=enrich). Code decides WHAT to send (distinct uncached texts,
<=50 per request) and validates everything returned; the model only reads language.
Each request's validated results + its call-log row are committed in one transaction."""

import json
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from . import config, llm, store

def entities(text: str) -> list[str]:
    """Explicit feature terms (word-boundary, case-insensitive), returned as they appear in the source."""
    found, low = [], text.lower()
    for term in config.ENTITY_TERMS:
        m = re.search(r"(?<![a-z])" + re.escape(term) + r"(?:s|es)?(?![a-z])", low)
        if m:
            span = text[m.start():m.end()]
            if not any(term in f.lower() for f in found):
                found.append(span)
    return found[:5]


def is_short(text: str) -> bool:
    return len(text.strip()) <= config.SHORT_TEXT_CHARS


def build_user(texts: list[str]) -> str:
    lines = [f"Label these {len(texts)} reviews. Return exactly {len(texts)} lines, i = 1..{len(texts)}.", ""]
    for i, t in enumerate(texts, 1):
        body = t.replace("\r", " ").replace("\n", " ⏎ ")  # keep one review per line; quotes still validated on source
        lines.append(f"[{i}] ({'S' if is_short(t) else 'L'}) {body}")
    return "\n".join(lines)


def _norm_map(s: str):
    """Casefolded, whitespace-collapsed text plus index map back into the original."""
    out, idx = [], []
    prev_space = False
    for k, ch in enumerate(s):
        if ch.isspace() or ch == "⏎":
            if prev_space:
                continue
            out.append(" "); idx.append(k); prev_space = True
        else:
            for c in unicodedata.normalize("NFC", ch).casefold():
                out.append(c); idx.append(k)
            prev_space = False
    return "".join(out), idx


def locate_quote(quote: str, text: str):
    """Exact substring, else a whitespace/case-insensitive match mapped back to the exact
    original span (so the saved quote is always a true source substring)."""
    q = quote.strip().strip('"“”')
    if q and q in text:
        return q, None
    nt, idx = _norm_map(text)
    nq, _ = _norm_map(q)
    nq = nq.strip()
    if len(nq) >= 3:
        p = nt.find(nq)
        if p >= 0:
            start, end = idx[p], idx[p + len(nq) - 1] + 1
            span = text[start:end]
            if span.strip():
                return span, "quote_repaired_case_or_whitespace"
    return None, "quote_not_in_source"


def fallback_quote(text: str) -> str:
    """Deterministic last resort: the first sentence (<=200 chars) of the source."""
    s = text.strip()
    m = re.search(r"[.!?\n]", s[20:])
    cut = 20 + m.start() + 1 if m else len(s)
    return s[:min(cut, 200)].strip() or s[:200]


def validate(item: dict, text: str, allow_fallback: bool = False):
    """Returns (record, None) or (None, error). Contract checks + topic/subtopic consistency."""
    try:
        sub, intent = item["k"], item["n"]
        sev, sent = item["v"], item["s"]
        topic = config.SUBTOPIC_TO_TOPIC.get(sub)
        if topic is None or intent not in config.INTENTS:
            return None, "label_not_allowed"
        if type(sev) is not int or not 1 <= sev <= 5:
            return None, "severity_out_of_range"
        if type(sent) not in (int, float) or not -1 <= sent <= 1:
            return None, "sentiment_out_of_range"
        reasons = []
        ents = entities(text)
        if is_short(text):
            quote = text.strip()
        else:
            quote, why = locate_quote(item.get("q", ""), text)
            if quote is None:
                if not allow_fallback:
                    return None, why
                quote, why = fallback_quote(text), "quote_fallback_first_sentence"
            if why:
                reasons.append(why)
        needs = bool(item["r"]) or any(r.startswith("quote_fallback") for r in reasons)
        if item["r"]:
            reasons.insert(0, "model_flagged")
        return dict(topic=topic, subtopic=sub, intent=intent, sentiment=round(float(sent), 2), severity=sev,
                    entities=ents, evidence_quote=quote, needs_review=needs,
                    review_reason=";".join(reasons) or None), None
    except (KeyError, TypeError, ValueError) as e:
        return None, f"malformed_item:{type(e).__name__}"


def parse_items(raw: str, n: int):
    """schema_v3 line format 'i|k|n|s|v|r|q' -> {i: item}. Malformed lines are skipped (that item
    then counts as missing and goes to the single retry). Quote = everything after the 6th '|'."""
    got = {}
    for line in raw.strip().splitlines():
        parts = line.strip().split("|", 6)
        if len(parts) < 6:
            continue
        try:
            i = int(parts[0].strip().strip("[]"))
            item = {"i": i, "k": parts[1].strip(), "n": parts[2].strip(), "s": float(parts[3]),
                    "v": int(parts[4]), "r": parts[5].strip() in ("1", "true"),
                    "q": parts[6] if len(parts) > 6 else ""}
        except ValueError:
            continue
        if parts[5].strip() not in ("0", "1", "true", "false"):
            continue
        if 1 <= i <= n and i not in got:
            got[i] = item
    return got, (None if got else "unparseable_output")


def pending_work(con, review_ids, lc):
    """Distinct texts (in input order) with no saved result/quarantine under this label_config.
    Origin = the first input ID carrying that text; it is the ID actually sent to the model."""
    done = {r[0] for r in con.execute("SELECT text_sha FROM results WHERE label_config=?", (lc,))}
    done |= {r[0] for r in con.execute("SELECT text_sha FROM quarantine WHERE label_config=?", (lc,))}
    work, seen = [], set()
    for chunk in range(0, len(review_ids), 900):
        part = review_ids[chunk:chunk + 900]
        rows = {r["review_id"]: r for r in con.execute(
            f"SELECT review_id, text_sha, review_text FROM source WHERE review_id IN ({','.join('?' * len(part))})", part)}
        for rid in part:
            r = rows[rid]
            if r["text_sha"] in done or r["text_sha"] in seen:
                continue
            seen.add(r["text_sha"])
            work.append((rid, r["text_sha"], r["review_text"]))
    return work


def _request(chunk, system, rates, ledger, attempts_log, tier="standard"):
    texts = [t for _, _, t in chunk]
    def on_attempt(a):
        attempts_log.append(a)
    msg, usage = llm.call(role="enrich", model=config.MODELS["enrich"], system=system, user=build_user(texts),
                          max_tokens=config.ENRICH_MAX_TOKENS, ledger=ledger, rates=rates, schema=None,
                          on_attempt=on_attempt, tier=tier, cache_system=True)
    return msg, usage


def save_response(con, chunk, raw, usage, *, lc, run_id, phase, stage, attempt_no, tier, prior_failures):
    allow_fallback = attempt_no > 1  # second attempt: keep labels, use a deterministic quote, flag for review
    """Validate one response and commit results + call row atomically. Returns invalid
    (item, reason) list for the single allowed retry."""
    got, err = parse_items(raw, len(chunk))
    if usage.get("stop_reason") == "max_tokens":
        err = err or "max_tokens_truncated"
    ok, bad = [], []
    for pos, (rid, tsha, text) in enumerate(chunk, 1):
        if pos not in got:
            bad.append(((rid, tsha, text), err or "missing_item"))
            continue
        rec, why = validate(got[pos], text, allow_fallback)
        (ok.append(((rid, tsha, text), rec)) if rec else bad.append(((rid, tsha, text), why)))
    now = time.time()
    with store.tx(con):
        for a in prior_failures:  # failed transient attempts for this request
            store.log_call(con, run_id=run_id, stage=stage, role="enrich", model=config.MODELS["enrich"],
                           effort="thinking_off", phase=phase, label_config=lc,
                           prompt_version=config.ENRICH_PROMPT_VERSION,
                           review_ids=[c[0] for c in chunk], tier=tier, **a)
        store.log_call(con, run_id=run_id, stage=stage, role="enrich", model=config.MODELS["enrich"],
                       effort="thinking_off", phase=phase, outcome="succeeded", label_config=lc,
                       prompt_version=config.ENRICH_PROMPT_VERSION, review_ids=[c[0] for c in chunk],
                       input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                       cache_read_tokens=usage["cache_read_tokens"], cache_write_tokens=usage["cache_write_tokens"],
                       tier=tier, attempt=attempt_no, started=usage["started"], ended=now,
                       error=(f"{len(bad)} invalid items: " + ";".join(sorted({w for _, w in bad})) if bad else None),
                       cost_usd=usage["cost_usd"],
                       request_id=usage["request_id"])
        for (rid, tsha, _), rec in ok:
            con.execute("INSERT OR IGNORE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (tsha, lc, rid, rec["topic"], rec["subtopic"], rec["intent"], rec["sentiment"],
                         rec["severity"], json.dumps(rec["entities"], ensure_ascii=False), rec["evidence_quote"],
                         int(rec["needs_review"]), rec["review_reason"], usage["request_id"], phase, run_id, now))
    return bad


def quarantine(con, items, lc, attempts):
    with store.tx(con):
        for (rid, tsha, _), why in items:
            con.execute("INSERT OR REPLACE INTO quarantine VALUES (?,?,?,?,?,?,?)",
                        (tsha, lc, rid, why, attempts, why, time.time()))


def run(con, review_ids, *, run_id, phase, stage="enrich", max_requests=None, workers=None, log=print):
    """Sync mode (pilot / checkpoints). Returns summary dict. Raises BudgetExceeded at the cap;
    KeyboardInterrupt leaves every committed batch intact for resume."""
    lc = config.label_config()
    system = config.prompt_text(config.ENRICH_PROMPT_VERSION)
    rates = llm.load_rates()
    ledger = llm.Ledger(spent=store.spent_usd(con), cap=config.SPEND_CAP_USD)
    work = pending_work(con, review_ids, lc)
    chunks = [work[k:k + config.ENRICH_BATCH_SIZE] for k in range(0, len(work), config.ENRICH_BATCH_SIZE)]
    if max_requests is not None:
        chunks = chunks[:max_requests]
    log(f"[enrich] label_config={lc} input_ids={len(review_ids)} pending_texts={len(work)} "
        f"requests={len(chunks)} phase={phase}")
    workers = workers or config.MAX_WORKERS
    retry_items, n_req = [], 0

    def do(chunk):
        attempts = []
        try:
            msg, usage = _request(chunk, system, rates, ledger, attempts)
            return chunk, msg, usage, attempts, None
        except (llm.BudgetExceeded, llm.FatalAPIError):
            raise
        except Exception as e:  # exhausted transient retries or non-retryable error
            return chunk, None, None, attempts, e

    def handle(res, attempt_no=1):
        nonlocal n_req
        chunk, msg, usage, attempts, exc = res
        n_req += 1
        if exc is not None:
            with store.tx(con):
                for a in attempts:
                    store.log_call(con, run_id=run_id, stage=stage, role="enrich", model=config.MODELS["enrich"],
                                   effort="thinking_off", phase=phase, label_config=lc,
                                   prompt_version=config.ENRICH_PROMPT_VERSION,
                                   review_ids=[c[0] for c in chunk], tier="standard", **a)
            return [(c, f"api_error:{type(exc).__name__}") for c in chunk]
        return save_response(con, chunk, llm.text_of(msg), usage, lc=lc, run_id=run_id, phase=phase, stage=stage,
                             attempt_no=attempt_no, tier="standard", prior_failures=attempts)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for k in range(0, len(chunks), workers):
            for res in pool.map(do, chunks[k:k + workers]):
                bad = handle(res)
                retry_items.extend(bad)
            done = con.execute("SELECT COUNT(*) FROM results WHERE label_config=?", (lc,)).fetchone()[0]
            log(f"[enrich] request {min(k + workers, len(chunks))}/{len(chunks)} saved; texts_done={done} "
                f"spent=${ledger.spent:.4f} retry_queue={len(retry_items)}")

    # Single retry for invalid items, in smaller requests (10), then quarantine.
    still_bad = []
    for k in range(0, len(retry_items), 10):
        sub = [c for c, _ in retry_items[k:k + 10]]
        bad = handle(do(sub), attempt_no=2)
        still_bad.extend(bad)
    if still_bad:
        quarantine(con, still_bad, lc, attempts=2)
    return {"label_config": lc, "pending_texts": len(work), "requests": n_req, "retried_items": len(retry_items),
            "quarantined_texts": len(still_bad), "spent_total_usd": ledger.spent}


def run_batch(con, review_ids, *, run_id, phase, stage="enrich", requests_per_job=400, max_jobs=None,
              poll_seconds=30, log=print):
    """Provider Message Batches API (50% price, async). Each job holds <=requests_per_job
    requests of <=50 reviews. Job IDs + their chunk mapping are saved BEFORE waiting, so a
    restart reconciles in-flight jobs instead of resubmitting (no double charge). Each
    returned request is validated and committed atomically like sync mode; invalid items
    get the single retry in small synchronous requests."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    lc = config.label_config()
    system = config.prompt_text(config.ENRICH_PROMPT_VERSION)
    rates = llm.load_rates()
    ledger = llm.Ledger(spent=store.spent_usd(con), cap=config.SPEND_CAP_USD)
    model = config.MODELS["enrich"]
    retry_items = []

    def process(job_id, mapping, job_phase):
        """Fetch results of an ended job and save every request."""
        n_ok = n_err = 0
        seen = set()
        for res in llm.client().messages.batches.results(job_id):
            chunk = [tuple(c) for c in mapping[res.custom_id]]
            seen.add(res.custom_id)
            if res.result.type == "succeeded":
                msg = res.result.message
                u = msg.usage
                usage = dict(input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                             cache_read_tokens=u.cache_read_input_tokens or 0,
                             cache_write_tokens=u.cache_creation_input_tokens or 0)
                usage.update(cost_usd=llm.cost_of(usage, model, "batch", rates), request_id=msg.id,
                             started=job_started, stop_reason=msg.stop_reason)
                if con.execute("SELECT 1 FROM calls WHERE request_id=?", (msg.id,)).fetchone():
                    continue  # already saved before an interruption
                retry_items.extend(save_response(con, chunk, llm.text_of(msg), usage, lc=lc, run_id=run_id,
                                                 phase=job_phase, stage=stage, attempt_no=1, tier="batch",
                                                 prior_failures=[]))
                n_ok += 1
            else:
                rid = f"{job_id}:{res.custom_id}"
                if not con.execute("SELECT 1 FROM calls WHERE request_id=?", (rid,)).fetchone():
                    with store.tx(con):
                        store.log_call(con, request_id=rid, run_id=run_id, stage=stage, role="enrich", model=model,
                                       effort="thinking_off", phase=job_phase, outcome="failed", label_config=lc,
                                       prompt_version=config.ENRICH_PROMPT_VERSION,
                                       review_ids=[c[0] for c in chunk], input_tokens=0, output_tokens=0,
                                       cache_read_tokens=0, cache_write_tokens=0, tier="batch", attempt=1,
                                       started=job_started, error=f"batch_result_{res.result.type}", cost_usd=0.0)
                retry_items.extend((c, f"batch_{res.result.type}") for c in chunk)
                n_err += 1
        with store.tx(con):
            con.execute("UPDATE batch_jobs SET status='processed' WHERE batch_id=?", (job_id,))
        log(f"[enrich-batch] job {job_id} processed: ok={n_ok} errored={n_err} spent=${store.spent_usd(con):.4f}")

    def wait(job_id):
        while True:
            b = llm.client().messages.batches.retrieve(job_id)
            if b.processing_status == "ended":
                return b
            c = b.request_counts
            log(f"[enrich-batch] {job_id} {b.processing_status} processing={c.processing} "
                f"succeeded={c.succeeded} errored={c.errored}")
            time.sleep(poll_seconds)

    # 1. reconcile jobs submitted earlier (interrupted while in flight)
    for j in con.execute("SELECT * FROM batch_jobs WHERE status!='processed'").fetchall():
        job_started = j["created"]
        log(f"[enrich-batch] reconciling in-flight job {j['batch_id']} (no resubmission)")
        wait(j["batch_id"])
        process(j["batch_id"], json.loads(j["requests"]), j["phase"])

    jobs = 0
    while True:
        # texts already attempted in a batch and found invalid go ONLY to the single retry below
        retrying = {c[1] for c, _ in retry_items}
        work = [w for w in pending_work(con, review_ids, lc) if w[1] not in retrying]
        if not work or (max_jobs is not None and jobs >= max_jobs):
            break
        chunks = [work[k:k + config.ENRICH_BATCH_SIZE] for k in range(0, len(work), config.ENRICH_BATCH_SIZE)]
        chunks = chunks[:requests_per_job]
        # reserve worst case for the whole job before submitting
        worst = sum(llm.estimate_tokens(system + build_user([t for _, _, t in ch])) * rates[(model, "batch", "cache_write")]
                    + config.ENRICH_MAX_TOKENS * rates[(model, "batch", "output")] for ch in chunks)
        ledger.spent = store.spent_usd(con)
        ledger.reserve(worst)
        mapping, reqs = {}, []
        for n, ch in enumerate(chunks):
            cid = f"r{n:04d}"
            mapping[cid] = [list(c) for c in ch]
            reqs.append(Request(custom_id=cid, params=MessageCreateParamsNonStreaming(
                model=model, max_tokens=config.ENRICH_MAX_TOKENS,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": build_user([t for _, _, t in ch])}])))
        job_started = time.time()
        import anthropic
        try:
            b = llm.client().messages.batches.create(requests=reqs)
        except (anthropic.BadRequestError, anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            ledger.settle(worst, 0.0)
            raise llm.FatalAPIError(f"batch submission refused: {str(e)[:200]}") from e
        with store.tx(con):
            con.execute("INSERT INTO batch_jobs VALUES (?,?,?,?,?,?)",
                        (b.id, run_id, phase, job_started, "submitted", json.dumps(mapping)))
        log(f"[enrich-batch] submitted {b.id}: {len(reqs)} requests, {sum(len(c) for c in chunks)} texts, "
            f"reserved ${worst:.4f}")
        wait(b.id)
        ledger.settle(worst, 0.0)
        process(b.id, mapping, phase)
        jobs += 1

    # single retry for invalid/errored items (small synchronous requests), then quarantine
    still_bad = []
    for k in range(0, len(retry_items), 10):
        sub = [c for c, _ in retry_items[k:k + 10]]
        attempts = []
        try:
            msg, usage = _request(sub, system, rates, ledger, attempts)
            still_bad.extend(save_response(con, sub, llm.text_of(msg), usage, lc=lc, run_id=run_id, phase=phase,
                                           stage=stage, attempt_no=2, tier="standard", prior_failures=attempts))
        except (llm.BudgetExceeded, llm.FatalAPIError):
            raise
        except Exception as e:
            still_bad.extend((c, f"api_error:{type(e).__name__}") for c in sub)
    if still_bad:
        quarantine(con, still_bad, lc, attempts=2)
    return {"label_config": lc, "jobs": jobs, "retried_items": len(retry_items),
            "quarantined_texts": len(still_bad), "spent_total_usd": store.spent_usd(con)}
