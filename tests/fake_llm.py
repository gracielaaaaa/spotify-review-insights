"""Offline fake Anthropic client for zero-cost pipeline tests. Labels are keyword rules,
NOT real classifications; outputs from this client are never exported as results."""
import json, re, uuid
from types import SimpleNamespace as NS

RULES = [("crash", "playback", "crash_wont_open", 4), ("ads", "usability", "ads", 2), ("premium", "billing", "premium_paywall", 3),
         ("login", "access", "login_failure", 4), ("download", "downloads", "downloads_other", 3), ("song", "catalog", "missing_content", 2)]

class Messages:
    def create(self, **kw):
        user = kw["messages"][0]["content"]
        props = kw["output_config"]["format"]["schema"]["properties"]
        if "items" in props:
            items = []
            for m in re.finditer(r"^\[(\d+)\] (?:\((S|L)\) )?(.*)$", user, re.M):
                i, text = int(m.group(1)), m.group(3)
                topic, sub, sev = "other", "general_criticism", 2
                for kwd, t, s, v in RULES:
                    if kwd in text.lower():
                        topic, sub, sev = t, s, v; break
                intent = "praise" if re.search(r"good|great|love|best|nice", text, re.I) else "complaint"
                if intent == "praise": topic, sub, sev = ("other", "general_praise", 1) if topic == "other" else (topic, sub, 1)
                it = {"i": i, "topic": topic, "intent": intent, "severity": sev}
                if "subtopic" in json.dumps(props):
                    words = text.replace(" ⏎ ", "\n").split()
                    it.update(subtopic=sub, sentiment=0.5 if intent == "praise" else -0.5, entities=[],
                              quote=" ".join(words[:6]) if m.group(2) == "L" else "", needs_review=False)
                items.append(it)
            out = {"items": items}
        elif "memo_markdown" in props:
            claims = json.loads(user.split("\n\nYour previous")[0])["claims"]
            c = claims[0]
            out = {"recommendation": "fake", "memo_markdown": f"## Recommendation\nFocus on {c['issue_id']} with {c['value']} complaints [{c['claim_id']}].",
                   "cited_claim_ids": [c["claim_id"]], "cited_review_ids": []}
        else:
            ids = re.findall(r"- ([0-9a-f-]{36}):", user)
            out = {"name": "Fake issue name", "description": "fake", "representative_ids": ids[:2], "misfit_ids": []}
        text = json.dumps(out)
        return NS(id=f"fake_{uuid.uuid4().hex[:10]}", content=[NS(type="text", text=text)], stop_reason="end_turn",
                  usage=NS(input_tokens=len(kw["system"] + user) // 4, output_tokens=len(text) // 4,
                           cache_read_input_tokens=0, cache_creation_input_tokens=0))

class FakeClient:
    messages = Messages()

def install():
    from pipeline import llm
    llm._client = FakeClient()

def install_interrupting(after_calls):
    """Fake client that raises KeyboardInterrupt (like Ctrl-C) on call number after_calls+1."""
    from pipeline import llm
    class Interrupting(Messages):
        n = 0
        def create(self, **kw):
            Interrupting.n += 1
            if Interrupting.n > after_calls:
                raise KeyboardInterrupt
            return super().create(**kw)
    c = FakeClient(); c.messages = Interrupting(); llm._client = c
