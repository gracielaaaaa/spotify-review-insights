"""Printable PDF: labeling instructions + the 50 golden review texts with blank answer boxes.
Shows review text only (no drafts, no model output).
  .venv/bin/pip install reportlab && .venv/bin/python tools/make_label_sheet.py"""

import csv
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Final Assignment - Spotify Reviews Dataset" / "golden_50_to_label.csv"
OUT = ROOT / "evals" / "golden_labeling_sheet.pdf"

pdfmetrics.registerFont(TTFont("AU", "/Library/Fonts/Arial Unicode.ttf"))
ss = getSampleStyleSheet()
body = ParagraphStyle("b", parent=ss["Normal"], fontName="AU", fontSize=9.5, leading=13)
small = ParagraphStyle("s", parent=body, fontSize=8.5, leading=11, textColor=colors.HexColor("#444444"))
h1 = ParagraphStyle("h1", parent=ss["Title"], fontName="AU", fontSize=18, leading=22, spaceAfter=6)
h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="AU", fontSize=12.5, leading=16, spaceBefore=6, spaceAfter=3)
rev = ParagraphStyle("r", parent=body, fontSize=10, leading=13.5)

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")


def clean(t):
    t = EMOJI.sub(" [emoji] ", t)
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")).strip()


def table(rows, widths, header=True):
    t = Table(rows, colWidths=widths)
    st = [("FONTNAME", (0, 0), (-1, -1), "AU"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
          ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
          ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee"))]
    t.setStyle(TableStyle(st))
    return t


P = lambda s, st=body: Paragraph(s, st)
story = [P("Golden 50: labeling guide and worksheet", h1),
         P("Spotify review pipeline, final assignment. Label each review <b>from its text only</b>. "
           "This sheet shows no model labels or drafts, so your answers are independent human labels.", body),
         P("How to submit your labels", h2),
         P("1. Label on this sheet (print it or annotate it on a tablet), or go straight to the form.<br/>"
           "2. Enter the answers in the one-page form: run <font face='Courier'>.venv/bin/python tools/label_form.py</font> "
           "in the project folder and open <b>http://localhost:8765</b>. Pick from the dropdowns. It saves automatically to "
           "<font face='Courier'>evals/golden_50_human.csv</font>.<br/>"
           "3. Put your name in the box at the top of the form. Finish when the counter reads <b>50 / 50 complete</b>.", body),
         KeepTogether([P("Also: real API spend (for the cost report)", h2),
         P("console.anthropic.com → the organization with the key ending <b>…LgAA</b> → <b>Usage</b> / <b>Cost</b> → "
           "date range <b>Oct 6 – Oct 14, 2026</b> → write down the total in USD (and the split by model if it is shown): "
           "<b>$ __________</b>", body)]),
         P("Topic: pick one, the most severe specific problem (tie: the first one mentioned)", h2),
         table([["#", "Topic", "Use for"],
                ["1", "access", "login, signup, password, account access, hacked or locked account"],
                ["2", "usability", "navigation, controls, layout/redesign, queue/playlist management, shuffle/repeat, ad interruptions"],
                ["3", "playback", "crashes, won't open, lag, songs stopping or skipping, connection, audio quality, battery/data, car/Bluetooth"],
                ["4", "downloads", "downloading, offline listening, saved music disappearing"],
                ["5", "catalog", "missing songs/artists, search, recommendations, lyrics availability, podcast content"],
                ["6", "billing", "price, charges, subscriptions, refunds, paywalls; controls locked behind Premium"],
                ["7", "support", "contacting customer support and its response"],
                ["8", "other", "general praise or criticism with no specific feature; unrelated or meaningless text"]],
               [0.3 * inch, 0.9 * inch, 5.8 * inch]),
         Spacer(1, 4),
         P("• Positive review: choose the first <i>specific</i> praised feature. \"Great app\" is <b>other</b>.<br/>"
           "• Mentioning a paid plan alone is <b>not</b> billing. A subscription failing to activate <b>is</b> billing. "
           "A crash for a paying user is <b>playback</b>.<br/>"
           "• \"Bad update\" or \"worst app\" with no feature named is <b>other</b>.", small),
         P("Intent: the first one that applies wins", h2),
         table([["#", "Intent", "Meaning"],
                ["1", "cancellation", "explicitly leaving, uninstalling, cancelling, switching, or threatening to"],
                ["2", "complaint", "any negative experience, including mixed praise and criticism"],
                ["3", "request", "asks for a change or feature without reporting a failure"],
                ["4", "praise", "positive, with no complaint or request"],
                ["5", "unclear", "meaningless, unrelated, or bare boycott/political slogans with no product complaint"]],
               [0.3 * inch, 1.0 * inch, 5.7 * inch]),
         P("Severity (1–5) and sentiment (−1 to +1)", h2),
         table([["Sev.", "Meaning", "Example"],
                ["1", "no problem: praise, unclear text, or a pure feature request", "\"Good\"; \"Please add a sleep timer\""],
                ["2", "dislike, generic criticism, minor annoyance", "\"Worst update\"; \"ads every two songs\""],
                ["3", "degraded or restricted function, some use left", "\"Need premium to repeat a song\"; \"crashes sometimes\""],
                ["4", "core task clearly blocked", "\"Can't log in\"; \"app won't open\""],
                ["5", "explicit serious money, privacy or data harm", "\"Charged twice, no refund\"; \"account hacked\""]],
               [0.4 * inch, 3.1 * inch, 3.5 * inch]),
         Spacer(1, 4),
         P("<b>Ignore the star rating.</b> Angry words, a crash alone, or cancellation intent do not raise severity by themselves. "
           "Sentiment: −1 very negative · 0 neutral or mixed · +1 very positive. "
           "If two answers both seem right, mark <b>ambiguous</b> and write the other acceptable label.", small),
         PageBreak(),
         P("Worksheet: 50 reviews", h1),
         P("Codes: Topic 1–8 · Intent 1–5 · Severity 1–5 · Sentiment −1..+1 · Amb = ambiguous (✓ plus the other acceptable label). "
           "[emoji] marks where the original review had an emoji.", small), Spacer(1, 6)]

rows = list(csv.DictReader(open(SRC, encoding="utf-8", newline="")))
for k, r in enumerate(rows, 1):
    boxes = table([["Topic", "Intent", "Severity", "Sentiment", "Amb / also OK", "Note"], ["", "", "", "", "", ""]],
                  [0.7 * inch, 0.7 * inch, 0.75 * inch, 0.85 * inch, 1.5 * inch, 2.5 * inch])
    boxes.setStyle(TableStyle([("ROWHEIGHT", (0, 1), (-1, 1), 18)]))
    story.append(KeepTogether([P(f"<b>{k}.</b>  {clean(r['review_text'])}", rev),
                               P(f"id {r['review_id'][:8]}…", small), Spacer(1, 2), boxes, Spacer(1, 9)]))

def footer(c, d):
    c.setFont("AU", 8)
    c.setFillColor(colors.HexColor("#777777"))
    c.drawRightString(letter[0] - 0.6 * inch, 0.45 * inch, f"Golden 50 labeling · page {d.page}")

SimpleDocTemplate(str(OUT), pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.6 * inch,
                  bottomMargin=0.7 * inch, title="Golden 50 labeling guide and worksheet").build(story, onFirstPage=footer, onLaterPages=footer)
print(OUT)
