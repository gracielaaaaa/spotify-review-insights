"""Golden-50 evaluation (offline, no model calls).

  .venv/bin/python evals/golden_eval.py --records runs/final_100k/records.jsonl

Compares saved pipeline predictions with the reviewed golden labels (evals/golden_50_labeled.csv;
AI-drafted by claude-opus-5-5, reviewed by the student, see label_source/human_changed columns).
Golden labels were never model inputs; the enrich prompt was frozen (enrich_v3) before drafting.
Outputs: evals/golden_eval.json, evals/golden_eval.md, evals/golden_cases.csv, evals/gold_benchmark.json
(the checker's --gold format)."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOPICS = ["access", "usability", "playback", "downloads", "catalog", "billing", "support", "other"]
INTENTS = ["cancellation", "complaint", "request", "praise", "unclear"]
SENT_TOL = 0.3  # predeclared sentiment tolerance


def split(s):
    return [x.strip() for x in (s or "").replace(",", ";").split(";") if x.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    a = ap.parse_args()
    human = HERE / "golden_50_human.csv"
    hrows = list(csv.DictReader(open(human, encoding="utf-8"))) if human.exists() else []
    complete = len(hrows) == 50 and all(r.get("topic") and r.get("intent") and r.get("severity") for r in hrows)
    src = human if complete else HERE / "golden_50_labeled.csv"
    gold = list(csv.DictReader(open(src, encoding="utf-8")))
    drafts = {d["review_id"]: d for d in csv.DictReader(open(HERE / "golden_50_draft.csv", encoding="utf-8"))}
    ids = {g["review_id"] for g in gold}
    pred = {}
    for line in open(a.records, encoding="utf-8"):
        r = json.loads(line)
        if r["review_id"] in ids:
            pred[r["review_id"]] = r
    cases, conf = [], defaultdict(Counter)
    tot = Counter()
    for g in gold:
        p = pred.get(g["review_id"], {})
        ok = p.get("status") == "completed"
        acc_t = [g["topic"]] + [t for t in split(g["accepted_topics"]) if t in TOPICS]
        acc_i = [g["intent"]] + [i for i in split(g["accepted_intents"]) if i in INTENTS]
        acc_s = [int(g["severity"])] + [int(s) for s in split(g["accepted_severities"]) if s.isdigit()]
        c = {"review_id": g["review_id"], "text": g["review_text"][:160], "valid_prediction": ok,
             "gold_topic": g["topic"], "pred_topic": p.get("topic"), "topic_exact": ok and p["topic"] == g["topic"],
             "topic_accepted": ok and p["topic"] in acc_t,
             "gold_intent": g["intent"], "pred_intent": p.get("intent"), "intent_exact": ok and p["intent"] == g["intent"],
             "intent_accepted": ok and p["intent"] in acc_i,
             "gold_severity": int(g["severity"]), "pred_severity": p.get("severity"),
             "severity_exact": ok and p["severity"] == int(g["severity"]),
             "severity_abs_err": abs(p["severity"] - int(g["severity"])) if ok else None,
             "severity_accepted_err": min(abs(p["severity"] - s) for s in acc_s) if ok else None,
             "gold_sentiment": float(g["sentiment"]), "pred_sentiment": p.get("sentiment"),
             "sentiment_abs_err": round(abs(p["sentiment"] - float(g["sentiment"])), 3) if ok else None,
             "quote_is_substring": ok and p["evidence_quote"] in g["review_text"],
             "pred_needs_review": p.get("needs_review"), "gold_ambiguous": g["ambiguous"] == "y",
             "pred_entities": p.get("entities"), "cache_source_id": p.get("cache_source_id"),
             "label_source": g.get("label_source"), "human_changed": g.get("human_changed")}
        c["sentiment_within_tol"] = ok and c["sentiment_abs_err"] <= SENT_TOL
        cases.append(c)
        conf[g["topic"]][p.get("topic", "<missing>")] += 1
        for k in ("topic_exact", "topic_accepted", "intent_exact", "intent_accepted", "severity_exact",
                  "sentiment_within_tol", "quote_is_substring", "valid_prediction"):
            tot[k] += bool(c[k])
    n = len(cases)
    valid = [c for c in cases if c["valid_prediction"]]
    nr_tp = sum(c["pred_needs_review"] and c["gold_ambiguous"] for c in valid)
    nr_p = sum(bool(c["pred_needs_review"]) for c in valid)
    nr_a = sum(c["gold_ambiguous"] for c in valid)
    per_topic = {t: {"gold": sum(c["gold_topic"] == t for c in cases), "pred": sum(c["pred_topic"] == t for c in cases),
                     "correct": sum(c["gold_topic"] == t and c["topic_exact"] for c in cases)} for t in TOPICS}
    f1s = []
    for t, v in per_topic.items():
        if v["gold"]:
            f1s.append(2 * v["correct"] / (v["gold"] + v["pred"]) if v["gold"] + v["pred"] else 0)
    summary = {
        "n": n, "valid_predictions": tot["valid_prediction"], "ambiguous_cases": sum(c["gold_ambiguous"] for c in cases),
        "topic_agreement_exact": round(tot["topic_exact"] / n, 4), "topic_agreement_accepted": round(tot["topic_accepted"] / n, 4),
        "intent_agreement_exact": round(tot["intent_exact"] / n, 4), "intent_agreement_accepted": round(tot["intent_accepted"] / n, 4),
        "severity_exact": round(tot["severity_exact"] / n, 4),
        "severity_mae": round(sum(c["severity_abs_err"] for c in valid) / max(1, len(valid)), 4),
        "severity_mae_vs_accepted": round(sum(c["severity_accepted_err"] for c in valid) / max(1, len(valid)), 4),
        "sentiment_mae": round(sum(c["sentiment_abs_err"] for c in valid) / max(1, len(valid)), 4),
        "sentiment_within_tolerance_0.3": round(tot["sentiment_within_tol"] / n, 4),
        "quote_substring_rate": round(tot["quote_is_substring"] / n, 4),
        "topic_macro_f1_exact": round(sum(f1s) / len(f1s), 4) if f1s else None,
        "needs_review_vs_ambiguous": {"predicted_flags": nr_p, "gold_ambiguous": nr_a, "true_positive": nr_tp,
                                      "precision": round(nr_tp / nr_p, 4) if nr_p else None,
                                      "recall": round(nr_tp / nr_a, 4) if nr_a else None},
        "per_topic": per_topic,
        "confusion_gold_to_pred": {g: dict(v) for g, v in conf.items()},
        "golden_source_file": src.name,
        "label_provenance": dict(Counter(c["label_source"] for c in cases)),
        "human_vs_assistant_draft": ({f: round(sum(g[f] == drafts[g["review_id"]]["draft_" + f] for g in gold) / len(gold), 4)
                                      for f in ("topic", "intent", "severity")} if complete else None),
        "caveat": ("50 cases is a small diagnostic sample. Golden labels were assigned blind by the student "
                   "(tools/label_golden.py shows only the review text; no drafts or model output). The student had seen an "
                   "earlier AI-drafted version (evals/golden_50_draft.csv) during a previous review pass; agreement with those drafts is reported separately."
                   if complete else
                   "50 cases is a small diagnostic sample. Golden labels were AI-drafted (claude-opus-5-5) and human-reviewed; "
                   "the reviewer accepted all drafts unchanged, so agreement partly measures model-vs-model consistency."),
    }
    (HERE / "golden_eval.json").write_text(json.dumps({"summary": summary, "cases": cases}, indent=2, ensure_ascii=False) + "\n")
    with open(HERE / "golden_cases.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(cases[0]), lineterminator="\n")
        w.writeheader(); w.writerows(cases)
    bench = {"version": "student-golden-50-v1", "cases": [
        {"review_id": g["review_id"], "status": "approved", "reviewer": g.get("labeler") or "student",
         "accepted_topic": sorted(set([g["topic"]] + [t for t in split(g["accepted_topics"]) if t in TOPICS])),
         "accepted_intent": sorted(set([g["intent"]] + [i for i in split(g["accepted_intents"]) if i in INTENTS])),
         "accepted_severity": sorted(set([int(g["severity"])] + [int(s) for s in split(g["accepted_severities"]) if s.isdigit()]))}
        for g in gold]}
    (HERE / "gold_benchmark.json").write_text(json.dumps(bench, indent=2) + "\n")
    L = ["# Golden-50 evaluation\n", f"Predictions: `{a.records}`. {summary['caveat']}\n",
         "| Metric | Value |", "|---|---|"]
    for k in ("valid_predictions", "ambiguous_cases", "topic_agreement_exact", "topic_agreement_accepted", "intent_agreement_exact",
              "intent_agreement_accepted", "severity_exact", "severity_mae", "severity_mae_vs_accepted", "sentiment_mae",
              "sentiment_within_tolerance_0.3", "quote_substring_rate", "topic_macro_f1_exact"):
        L.append(f"| {k} | {summary[k]} |")
    nr = summary["needs_review_vs_ambiguous"]
    L.append(f"| needs_review as predictor of ambiguity | precision {nr['precision']}, recall {nr['recall']} ({nr['true_positive']}/{nr['gold_ambiguous']}) |")
    L.append("\n## Topic confusion (rows = golden, columns = predicted)\n")
    L.append("| gold \\ pred | " + " | ".join(TOPICS) + " |")
    L.append("|---" * (len(TOPICS) + 1) + "|")
    for g in TOPICS:
        if per_topic[g]["gold"]:
            L.append(f"| {g} | " + " | ".join(str(conf[g].get(p, "")) for p in TOPICS) + " |")
    L.append("\n## Disagreements (topic, intent or severity differ from the main golden label)\n")
    L.append("| review_id | text | gold t/i/s | pred t/i/s | accepted? |")
    L.append("|---|---|---|---|---|")
    for c in cases:
        if not (c["topic_exact"] and c["intent_exact"] and c["severity_exact"]):
            acc = "yes" if c["topic_accepted"] and c["intent_accepted"] and c["severity_accepted_err"] == 0 else "no"
            L.append(f"| {c['review_id'][:8]} | {c['text'][:70].replace('|', '/')} | {c['gold_topic']}/{c['gold_intent']}/{c['gold_severity']} | "
                     f"{c['pred_topic']}/{c['pred_intent']}/{c['pred_severity']} | {acc} |")
    (HERE / "golden_eval.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("per_topic", "confusion_gold_to_pred")}, indent=1))


if __name__ == "__main__":
    main()
