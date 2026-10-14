"""One-page blind labeling form for the golden 50 (human labels; shows review text only, never
drafts or model output). Saves to evals/golden_50_human.csv on every change.

  .venv/bin/python tools/label_form.py      # then open http://localhost:8765
"""

import csv
import html
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Final Assignment - Spotify Reviews Dataset" / "golden_50_to_label.csv"
OUT = ROOT / "evals" / "golden_50_human.csv"
TOPICS = ["access", "usability", "playback", "downloads", "catalog", "billing", "support", "other"]
INTENTS = ["cancellation", "complaint", "request", "praise", "unclear"]
SRC_FIELDS = ["review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp"]
FIELDS = SRC_FIELDS + ["topic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review",
                       "ambiguous", "accepted_topics", "accepted_intents", "accepted_severities", "notes", "labeler", "label_source"]
ROWS = list(csv.DictReader(open(SRC, encoding="utf-8", newline="")))


def existing():
    if not OUT.exists():
        return {}
    return {r["review_id"]: r for r in csv.DictReader(open(OUT, encoding="utf-8", newline=""))}


def save(data):
    labeler = data.get("labeler", "").strip()
    by_id = data["labels"]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        n = 0
        for r in ROWS:
            l = by_id.get(r["review_id"])
            if not l or not (l.get("topic") and l.get("intent") and l.get("severity")):
                continue
            amb = bool(l.get("ambiguous"))
            w.writerow({**{k: r[k] for k in SRC_FIELDS}, "topic": l["topic"], "intent": l["intent"],
                        "sentiment": l.get("sentiment", "0"), "severity": l["severity"], "entities": "",
                        "evidence_quote": r["review_text"].strip(), "needs_review": "true" if amb else "false",
                        "ambiguous": "y" if amb else "n", "accepted_topics": l.get("alt_topic", "") if amb else "",
                        "accepted_intents": l.get("alt_intent", "") if amb else "",
                        "accepted_severities": l.get("alt_sev", "") if amb else "", "notes": l.get("notes", ""),
                        "labeler": labeler, "label_source": "human_blind_form"})
            n += 1
    return n


def page():
    prev = existing()
    opts = lambda vals, sel: "".join(f'<option value="{v}"{" selected" if v == sel else ""}>{v}</option>' for v in vals)
    cards = []
    for k, r in enumerate(ROWS, 1):
        p = prev.get(r["review_id"], {})
        cards.append(f"""<div class="card" data-id="{r['review_id']}">
<div class="num">{k}</div><div class="text">{html.escape(r['review_text'])}</div>
<div class="ctl">
<label>Topic<select name="topic"><option value=""></option>{opts(TOPICS, p.get('topic'))}</select></label>
<label>Intent<select name="intent"><option value=""></option>{opts(INTENTS, p.get('intent'))}</select></label>
<label>Severity<select name="severity"><option value=""></option>{opts(['1','2','3','4','5'], p.get('severity'))}</select></label>
<label>Sentiment <output>{p.get('sentiment', '0')}</output><input type="range" name="sentiment" min="-1" max="1" step="0.1" value="{p.get('sentiment', '0')}"></label>
<label class="amb"><input type="checkbox" name="ambiguous"{" checked" if p.get('ambiguous') == 'y' else ""}> ambiguous</label>
<span class="alts"{'' if p.get('ambiguous') == 'y' else ' hidden'}>also OK:
<select name="alt_topic"><option value=""></option>{opts(TOPICS, p.get('accepted_topics'))}</select>
<select name="alt_intent"><option value=""></option>{opts(INTENTS, p.get('accepted_intents'))}</select>
<select name="alt_sev"><option value=""></option>{opts(['1','2','3','4','5'], p.get('accepted_severities'))}</select></span>
</div></div>""")
    name = next((r.get("labeler") for r in prev.values() if r.get("labeler")), "")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Golden 50 Labeling</title><style>
:root{{--bg:#f4f3f0;--card:#fff;--ink:#111;--mut:#666;--line:#ddd;--acc:#2a78d6;--ok:#e8f3ea}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--card:#1b1b1a;--ink:#eee;--mut:#999;--line:#333;--acc:#3987e5;--ok:#16261a}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,system-ui,sans-serif}}
.top{{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:10px 16px;z-index:2;font-size:13px}}
.top b{{font-size:15px}} .key{{color:var(--mut)}} main{{max-width:980px;margin:0 auto;padding:12px 16px 60px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:8px 0;display:grid;grid-template-columns:28px 1fr;gap:4px 10px}}
.card.done{{background:var(--ok)}} .num{{color:var(--mut);font-weight:600}} .ctl{{grid-column:2;display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:13px}}
select{{font:inherit;padding:3px;margin-left:4px}} input[type=range]{{vertical-align:middle;width:120px}} #status{{float:right;color:var(--acc)}}
</style></head><body>
<div class="top"><span id="status">not saved yet</span><b>Golden 50, your labels</b> · name <input id="labeler" value="{html.escape(name)}" placeholder="your name">
· <span id="prog"></span><br><span class="key">Ignore stars. Topic: the most severe specific problem (tie → first mentioned); general praise or criticism → other; a paid-plan mention alone is not billing; premium-only controls → billing.
Intent: cancellation &gt; complaint &gt; request &gt; praise &gt; unclear (bare slogans → unclear). Severity: 1 none/praise/request · 2 annoyance · 3 degraded, still usable · 4 core task blocked · 5 serious money/privacy/data harm.</span></div>
<main>{''.join(cards)}</main>
<script>
const cards=[...document.querySelectorAll('.card')];
function collect(){{const labels={{}};cards.forEach(c=>{{const g=n=>c.querySelector(`[name=${{n}}]`);
labels[c.dataset.id]={{topic:g('topic').value,intent:g('intent').value,severity:g('severity').value,sentiment:g('sentiment').value,
ambiguous:g('ambiguous').checked,alt_topic:g('alt_topic').value,alt_intent:g('alt_intent').value,alt_sev:g('alt_sev').value}};
c.classList.toggle('done',!!(g('topic').value&&g('intent').value&&g('severity').value));}});
document.getElementById('prog').textContent=document.querySelectorAll('.card.done').length+' / 50 complete';return labels;}}
let t;function save(){{clearTimeout(t);t=setTimeout(async()=>{{const r=await fetch('/save',{{method:'POST',body:JSON.stringify({{labeler:document.getElementById('labeler').value,labels:collect()}})}});
const j=await r.json();document.getElementById('status').textContent='saved '+j.saved+' rows to evals/golden_50_human.csv';}},300);}}
document.addEventListener('input',e=>{{if(e.target.type==='range')e.target.previousElementSibling.textContent=e.target.value;
if(e.target.name==='ambiguous')e.target.closest('.ctl').querySelector('.alts').hidden=!e.target.checked;collect();save();}});
collect();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = page().encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        n = save(data)
        b = json.dumps({"saved": n}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)


if __name__ == "__main__":
    print("Open http://localhost:8765  (Ctrl-C to stop; labels save automatically)")
    HTTPServer(("127.0.0.1", 8765), H).serve_forever()
