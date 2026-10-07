"""Human review of the assistant-drafted golden labels (evals/golden_50_draft.csv).

  .venv/bin/python tools/review_golden.py

For each review: press Enter to ACCEPT the draft, or type your own
"topic intent severity sentiment" (e.g. "8 2 2 -0.8", add " a" if ambiguous) to CORRECT it.
'b' = back, 'q' = quit (progress saved after every review; rerun to resume).
Output: evals/golden_50_labeled.csv with human_changed y/n per row (disclosed in README)."""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Final Assignment - Spotify Reviews Dataset" / "golden_50_to_label.csv"
DRAFT = ROOT / "evals" / "golden_50_draft.csv"
OUT = ROOT / "evals" / "golden_50_labeled.csv"
TOPICS = ["access", "usability", "playback", "downloads", "catalog", "billing", "support", "other"]
INTENTS = ["cancellation", "complaint", "request", "praise", "unclear"]
FIELDS = ["review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp",
          "topic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review",
          "ambiguous", "accepted_topics", "accepted_intents", "accepted_severities", "notes",
          "labeler", "human_changed", "label_source",
          "draft_topic", "draft_intent", "draft_severity", "draft_sentiment"]

KEY = ("TOPIC 1 access 2 usability 3 playback 4 downloads 5 catalog 6 billing 7 support 8 other\n"
       "INTENT 1 cancellation 2 complaint 3 request 4 praise 5 unclear\n"
       "SEVERITY 1 none/praise/request 2 annoyance 3 degraded 4 blocked 5 serious money/privacy/data harm")


def save(rows, done):
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            if r["review_id"] in done:
                w.writerow(done[r["review_id"]])


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8", newline="")))
    drafts = {d["review_id"]: d for d in csv.DictReader(open(DRAFT, encoding="utf-8", newline=""))}
    done = {}
    if OUT.exists():
        done = {r["review_id"]: r for r in csv.DictReader(open(OUT, encoding="utf-8", newline=""))
                if r.get("label_source")}
    name = next((r["labeler"] for r in done.values() if r.get("labeler")), "") or input("Your name: ").strip()
    print("\n" + KEY + "\nEnter = accept draft | type 4 numbers to correct | b back | q quit\n")
    i = next((k for k, r in enumerate(rows) if r["review_id"] not in done), len(rows))
    while i < len(rows):
        src, d = rows[i], drafts[rows[i]["review_id"]]
        print(f"[{i + 1}/50]  {src['review_text']}")
        alt = "; ".join(x for x in (d["draft_accepted_topics"], d["draft_accepted_intents"],
                                    d["draft_accepted_severities"]) if x)
        print(f"   draft: {d['draft_topic']} / {d['draft_intent']} / severity {d['draft_severity']} / "
              f"sentiment {d['draft_sentiment']}" + (f"  (ambiguous; also ok: {alt})" if d["draft_ambiguous"] == "y" else "")
              + (f"\n   why: {d['draft_note']}" if d["draft_note"] else ""))
        v = input("   > ").strip()
        if v == "q":
            break
        if v == "b":
            i = max(0, i - 1)
            done.pop(rows[i]["review_id"], None)
            continue
        rec = {k: src[k] for k in FIELDS[:6]}
        rec.update(entities="", evidence_quote=src["review_text"].strip(), labeler=name,
                   draft_topic=d["draft_topic"], draft_intent=d["draft_intent"],
                   draft_severity=d["draft_severity"], draft_sentiment=d["draft_sentiment"])
        if not v:
            rec.update(topic=d["draft_topic"], intent=d["draft_intent"], severity=d["draft_severity"],
                       sentiment=d["draft_sentiment"], ambiguous=d["draft_ambiguous"],
                       needs_review="true" if d["draft_ambiguous"] == "y" else "false",
                       accepted_topics=d["draft_accepted_topics"], accepted_intents=d["draft_accepted_intents"],
                       accepted_severities=d["draft_accepted_severities"], notes=d["draft_note"],
                       human_changed="n", label_source="assistant_draft_human_accepted")
        else:
            parts = v.split()
            amb = parts[-1] == "a"
            parts = parts[:-1] if amb else parts
            try:
                t, n, sv, se = int(parts[0]), int(parts[1]), int(parts[2]), float(parts[3])
                assert 1 <= t <= 8 and 1 <= n <= 5 and 1 <= sv <= 5 and -1 <= se <= 1
            except (AssertionError, ValueError, IndexError):
                print("   ! need: topic(1-8) intent(1-5) severity(1-5) sentiment(-1..1), e.g. 8 2 2 -0.8\n")
                continue
            rec.update(topic=TOPICS[t - 1], intent=INTENTS[n - 1], severity=str(sv), sentiment=str(se),
                       ambiguous="y" if amb else "n", needs_review="true" if amb else "false",
                       accepted_topics=input("   other ok topics (optional): ").strip() if amb else "",
                       accepted_intents=input("   other ok intents (optional): ").strip() if amb else "",
                       accepted_severities=input("   other ok severities (optional): ").strip() if amb else "",
                       notes=input("   why (optional): ").strip(),
                       human_changed="y", label_source="human_corrected")
        done[src["review_id"]] = rec
        save(rows, done)
        print()
        i += 1
    save(rows, done)
    changed = sum(r["human_changed"] == "y" for r in done.values())
    print(f"\nSaved {len(done)}/50 to {OUT} ({changed} corrected by you).")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nProgress saved; rerun to resume.")
        sys.exit(0)
