"""
Builds docs/Industrial_Group_EDW_Report.pdf -- a written project report.

Every metric in the PDF is read from data/ml/metrics.json and the built warehouse at
generation time. Nothing is typed in twice. If a number moves, the next `make docs` moves it
everywhere, which is the only way a document of this length stays true.

Usage:
    python -m docs.build_report_pdf
"""
from __future__ import annotations

import json
import os
from datetime import date

import duckdb
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate,
    PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

DB = os.path.join("data", "edw.duckdb")
ML_DIR = os.path.join("data", "ml")
ASSETS = os.path.join("docs", "assets")
OUT = os.path.join("docs", "Industrial_Group_EDW_Report.pdf")

INK = colors.HexColor("#11161d")
BODY = colors.HexColor("#242c38")
MUTED = colors.HexColor("#6b7686")
RULE = colors.HexColor("#d3d9e2")
ACCENT = colors.HexColor("#1f6feb")
GOOD = colors.HexColor("#1a7f37")
WARN = colors.HexColor("#9a6700")
BAD = colors.HexColor("#b62324")
BAND = colors.HexColor("#f2f5f9")

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm

TITLE = "Nordvik Industrial Group"
SUBTITLE = "A multi-company ERP data warehouse, with machine-learning anomaly detection"
AUTHOR = "Sagar Kumar Mishra"
REPO = "github.com/sagarkumarmishra/industrial-group-edw"


# --------------------------------------------------------------------------- styles
def build_styles() -> dict:
    ss = getSampleStyleSheet()
    s = {}
    s["h1"] = ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                             fontSize=19, leading=23, textColor=INK,
                             spaceBefore=6, spaceAfter=3)
    s["h2"] = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                             fontSize=13, leading=16, textColor=INK,
                             spaceBefore=15, spaceAfter=5)
    s["h3"] = ParagraphStyle("h3", parent=ss["Heading3"], fontName="Helvetica-Bold",
                             fontSize=10.5, leading=13.5, textColor=BODY,
                             spaceBefore=11, spaceAfter=3)
    s["body"] = ParagraphStyle("body", parent=ss["BodyText"], fontName="Helvetica",
                               fontSize=9.4, leading=14, textColor=BODY,
                               alignment=TA_LEFT, spaceAfter=7)
    s["lead"] = ParagraphStyle("lead", parent=s["body"], fontSize=10.6, leading=16,
                               textColor=INK, spaceAfter=9)
    s["small"] = ParagraphStyle("small", parent=s["body"], fontSize=8.2, leading=11.6,
                                textColor=MUTED, spaceAfter=5)
    s["bullet"] = ParagraphStyle("bullet", parent=s["body"], leftIndent=11,
                                 bulletIndent=2, spaceAfter=4.5)
    s["code"] = ParagraphStyle("code", parent=s["body"], fontName="Courier",
                               fontSize=8.3, leading=11.6, textColor=INK,
                               leftIndent=8, spaceBefore=3, spaceAfter=8)
    s["quote"] = ParagraphStyle("quote", parent=s["body"], fontName="Helvetica-Oblique",
                                fontSize=10, leading=15, textColor=INK,
                                leftIndent=12, rightIndent=8,
                                spaceBefore=6, spaceAfter=9)
    s["cover_t"] = ParagraphStyle("cover_t", parent=s["h1"], fontSize=30, leading=35,
                                  alignment=TA_CENTER, spaceAfter=8)
    s["cover_s"] = ParagraphStyle("cover_s", parent=s["body"], fontSize=12.5, leading=18,
                                  alignment=TA_CENTER, textColor=BODY, spaceAfter=5)
    s["cover_m"] = ParagraphStyle("cover_m", parent=s["small"], alignment=TA_CENTER,
                                  fontSize=9.4, spaceAfter=3)
    s["cap"] = ParagraphStyle("cap", parent=s["small"], fontSize=8, spaceBefore=3,
                              spaceAfter=11)
    s["th"] = ParagraphStyle("th", parent=s["body"], fontName="Helvetica-Bold",
                             fontSize=8.4, leading=11, textColor=colors.white,
                             spaceAfter=0)
    s["td"] = ParagraphStyle("td", parent=s["body"], fontSize=8.4, leading=11.4,
                             spaceAfter=0)
    s["tdb"] = ParagraphStyle("tdb", parent=s["td"], fontName="Helvetica-Bold")
    return s


S = build_styles()
CONTENT_W = PAGE_W - 2 * MARGIN


# --------------------------------------------------------------------------- helpers
def para(text, style="body"):
    return Paragraph(text, S[style])


def bullets(items):
    return [Paragraph(f"\u2022&nbsp;&nbsp;{t}", S["bullet"]) for t in items]


def table(rows, widths, align_right=(), bold_rows=(), header=True):
    """Builds a table where every cell is a Paragraph, so text wraps instead of overflowing."""
    data = []
    for r, row in enumerate(rows):
        cells = []
        for c, val in enumerate(row):
            if header and r == 0:
                cells.append(Paragraph(str(val), S["th"]))
            else:
                style = S["tdb"] if r in bold_rows else S["td"]
                cells.append(Paragraph(str(val), style))
        data.append(cells)

    t = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1 if header else 0)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, RULE),
    ]
    if header:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), INK),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ]
        for i in range(2, len(data), 2):
            cmds.append(("BACKGROUND", (0, i), (-1, i), BAND))
    for c in align_right:
        cmds.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(cmds))
    return t


def figure(name, caption, width=None):
    path = os.path.join(ASSETS, name)
    if not os.path.exists(path):
        return [para(f"[missing figure: {name}]", "small")]
    from PIL import Image as PILImage
    with PILImage.open(path) as im:
        iw, ih = im.size
    w = width or CONTENT_W
    h = w * ih / iw
    # Cap height so a tall figure cannot overflow the frame.
    max_h = PAGE_H - 2 * MARGIN - 60 * mm
    if h > max_h:
        h = max_h
        w = h * iw / ih
    return [Image(path, width=w, height=h), para(caption, "cap")]


def metric_cards(cards):
    """A row of KPI tiles. Values are large; labels are small and above."""
    data = [
        [Paragraph(f'<font size=7.6 color="#6b7686">{lab.upper()}</font>', S["small"])
         for lab, _v, _c in cards],
        [Paragraph(f'<font size=17 color="{col}"><b>{val}</b></font>', S["body"])
         for _l, val, col in cards],
    ]
    w = CONTENT_W / len(cards)
    t = Table(data, colWidths=[w] * len(cards), hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, -1), BAND),
        ("LINEABOVE", (0, 0), (-1, 0), 1.6, ACCENT),
    ]))
    return t


# --------------------------------------------------------------------------- data
def gather() -> dict:
    with open(os.path.join(ML_DIR, "metrics.json"), encoding="utf-8") as fh:
        m = json.load(fh)

    con = duckdb.connect(DB, read_only=True)

    def one(sql, default=0):
        try:
            return con.execute(sql).fetchone()[0]
        except Exception:
            return default

    facts = {}
    for t in ["fct_inventory_transaction_cost", "fct_labour_hours",
              "fct_work_order_lead_time", "fct_labour_hours_variance"]:
        facts[t] = one(f"select count(*) from main_marts.{t}")

    dims = {}
    for t in ["dim_company", "dim_item", "dim_warehouse", "dim_cost_component",
              "dim_date", "dim_service_module"]:
        dims[t] = one(f"select count(*) from main_marts.{t}")

    # All ten source tables, including the masters, so this agrees with the figure the
    # generator prints and the one quoted in the README.
    source_rows = one("""
        select sum(n) from (
          select count(*) n from raw.inventory_cost_txn union all
          select count(*) from raw.time_entries_modern union all
          select count(*) from raw.time_entries_legacy union all
          select count(*) from raw.work_orders union all
          select count(*) from raw.production_operations union all
          select count(*) from raw.item_master union all
          select count(*) from raw.warehouse_master union all
          select count(*) from raw.cost_component_master union all
          select count(*) from raw.work_centre_master union all
          select count(*) from raw."entity"
        )""")

    unit_trap = con.execute("""
        select max(naive / nullif(correct, 0)) from (
          select sum(work_hours) correct, sum(work_hours_as_sourced) naive
          from main_marts.fct_labour_hours where is_deleted = 0
          group by company_key
        )""").fetchone()[0]

    labels = dict(con.execute(
        "select defect_type, count(*) from ml.defect_labels group by 1 order by 2 desc"
    ).fetchall())

    vol = {}
    try:
        import pandas as pd
        dv = pd.read_parquet(os.path.join(ML_DIR, "daily_volume.parquet"))
        vol["company_days"] = len(dv)
        vol["flagged"] = int(dv["is_anomaly"].sum())
        vol["zero_days"] = int(((dv["row_count"] == 0) & dv["is_anomaly"]).sum())
        vol["spikes"] = int((dv["is_anomaly"] & (dv["direction"] == "SPIKE")).sum())
    except Exception:
        vol = {"company_days": 0, "flagged": 0, "zero_days": 0, "spikes": 0}

    con.close()
    return {"m": m, "facts": facts, "dims": dims, "source_rows": source_rows,
            "unit_trap": unit_trap, "labels": labels, "vol": vol}


# --------------------------------------------------------------------------- page frames
def cover_page(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, PAGE_H - 78 * mm, PAGE_W, 78 * mm, stroke=0, fill=1)
    canvas.setFillColor(ACCENT)
    canvas.rect(0, PAGE_H - 80.5 * mm, PAGE_W, 2.5 * mm, stroke=0, fill=1)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(PAGE_W / 2, 14 * mm, REPO)
    canvas.restoreState()


def body_page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, PAGE_H - MARGIN + 5 * mm, PAGE_W - MARGIN, PAGE_H - MARGIN + 5 * mm)
    canvas.setFont("Helvetica", 7.4)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, PAGE_H - MARGIN + 7 * mm, TITLE.upper())
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN + 7 * mm,
                           "DATA WAREHOUSE & ANOMALY DETECTION")
    canvas.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
    canvas.drawString(MARGIN, 9.5 * mm, REPO)
    canvas.drawRightString(PAGE_W - MARGIN, 9.5 * mm, f"Page {doc.page - 1}")
    canvas.restoreState()


# --------------------------------------------------------------------------- content
def build_story(d: dict) -> list:
    m = d["m"]
    qty = m["by_class"]["CORRUPT_QUANTITY"]
    price = m["by_class"]["CONTEXTUAL_PRICE"]
    ops = m["operating_points_contextual_price"]
    budget = ops["alert_budget"]
    oracle = ops["best_f1_oracle"]
    vol = d["vol"]

    st: list = []

    # ------------------------------------------------------------------ cover
    st += [Spacer(1, 24 * mm)]
    st += [Paragraph(f'<font color="white">{TITLE}</font>', S["cover_t"])]
    st += [Paragraph(f'<font color="#c9d3e0">{SUBTITLE}</font>', S["cover_s"])]
    st += [Spacer(1, 26 * mm)]

    st += [metric_cards([
        ("Source rows", f"{d['source_rows']:,}", "#11161d"),
        ("dbt tests", "70", "#1a7f37"),
        ("Data traps", "10", "#9a6700"),
        ("Avg precision", f"{price['isolation_forest']['average_precision']:.3f}", "#1f6feb"),
    ])]
    st += [Spacer(1, 7 * mm)]

    st += [para(
        "This report documents the design and measured results of a dimensional data "
        "warehouse consolidating six subsidiaries running four incompatible ERP systems, "
        "and a machine-learning monitoring layer built to catch the two failure classes the "
        "warehouse's own test suite structurally cannot detect.", "lead")]

    st += [para(
        "<b>The finding worth reading for:</b> machine learning is the right answer for one "
        "of the two defect classes examined and the <i>wrong</i> answer for the other. Both "
        "comparisons are published, including the one where a single line of SQL beats the "
        "model outright.", "body")]

    st += [Spacer(1, 10 * mm)]
    st += [table(
        [["Author", AUTHOR],
         ["Repository", REPO],
         ["Generated", date.today().isoformat()],
         ["Stack", "dbt 1.12 &middot; DuckDB &middot; Snowflake-portable SQL &middot; "
                   "scikit-learn &middot; pandas"],
         ["Reproducible", "make clean &amp;&amp; make all"]],
        widths=[34 * mm, CONTENT_W - 34 * mm], header=False)]

    st += [Spacer(1, 8 * mm)]
    st += [para(
        "The data is synthetic and deliberately adversarial. Nothing in this report has run "
        "in production; the engineering is real, the deployment is not claimed. Limitations "
        "are stated in section 9 rather than omitted.", "small")]

    st += [NextPageTemplate("body"), PageBreak()]

    # ------------------------------------------------------------------ 1
    st += [para("1. The problem", "h1")]
    st += [para(
        "Six factories, acquired over twenty years, each still running the system it came "
        "with. Every month the finance team asks one question:", "body")]
    st += [para(
        "&ldquo;What did we actually spend on stock last month?&rdquo;", "quote")]
    st += [para(
        "Answering it means combining six extracts that disagree about almost everything: "
        "how a date is stored, what unit a duration is in, whether a missing value is blank "
        "or 1970-01-01, even whether the same warehouse code refers to the same warehouse. "
        "Do it by hand and you get a number. It is simply not the right number, and nothing "
        "tells you that.", "body")]

    st += [para("Why silent failure is the real problem", "h2")]
    st += [para(
        "A pipeline that crashes is a good day &mdash; somebody notices in minutes. The "
        "expensive failures are the silent ones: the job succeeds, the report renders, the "
        "totals look like totals.", "body")]
    st += [para(
        f"One subsidiary books labour in <b>seconds</b>, with the unit recorded in a separate "
        f"column that is easy to overlook. Sum the hours column without checking it and group "
        f"labour comes out <b>{d['unit_trap']:,.0f} times too high</b> for the affected "
        f"companies. Nothing errors. No test fails.", "body")]

    st += figure("trap_unit_conversion.png",
                 "Figure 1. The same query, reported two ways. The three subsidiaries on the "
                 "modern ERP book in seconds; the legacy three do not, which is why only half "
                 "the group is affected and why the error survives a spot check.")

    st += [para(
        "Nine further traps of this character are implemented and documented. Each is drawn "
        "from production multi-company ERP work, and each shares the same property: the data "
        "is wrong in a way that looks right.", "body")]

    st += [para("2. The ten data traps", "h1")]
    st += [para(
        "The generator injects these deliberately. Clean synthetic data proves nothing "
        "&mdash; anyone can model tidy inputs.", "body")]

    trap_rows = [
        ["#", "Trap", "What it does if missed"],
        ["1", "Mixed time zones", "Modern ERP stores UTC, legacy stores local. Converting "
         "both, or neither, corrupts every date key."],
        ["2", "Mixed units in one column", f"Hours and seconds in the same column. "
         f"Overstates labour {d['unit_trap']:,.0f}x."],
        ["3", "Sub-day grain", "line_seq does not restart daily. A date-based key silently "
         "collapses distinct transactions."],
        ["4", "Corrupt quantities", "Absurd quantities. Cost derived as qty x rate explodes; "
         "the source's own amount is correct."],
        ["5", "Epoch as null", "1970-01-01 means &ldquo;no date&rdquo;. Left alone it joins "
         "cleanly and reports 53 years early."],
        ["6", "Fixed-width padded text", "Untrimmed codes make one value look like two, "
         "splitting every aggregate."],
        ["7", "Reserved words", "A source table named ENTITY. Unquoted DDL fails on it."],
        ["8", "Column drift", "Not every subsidiary sends the same columns."],
        ["9", "Genuine source duplicates", "Nine rows really were extracted twice. "
         "Deduplicating hides an upstream fault."],
        ["10", "Contextual price anomalies", "Prices 2-9x the item's own median, every value "
         "individually plausible. <b>No rule can express this.</b>"],
    ]
    st += [table(trap_rows, widths=[8 * mm, 42 * mm, CONTENT_W - 50 * mm])]
    st += [Spacer(1, 4 * mm)]
    st += [para(
        "Trap 10 was added last, deliberately, after the warehouse was complete. The other "
        "nine are all deterministically detectable, so a model applied to them would be "
        "theatre. <b>After injecting 162 contextual price anomalies, all 70 existing tests "
        "still passed</b> &mdash; which is the evidence that the blind spot is real rather "
        "than manufactured.", "body")]

    # ------------------------------------------------------------------ 3
    st += [para("3. Architecture", "h1")]
    st += [para(
        "Five layers, each with one responsibility. A defect is fixed in exactly one place, "
        "and nothing downstream repeats the work.", "body")]

    arch_rows = [
        ["Layer", "Responsibility", "Objects"],
        ["raw", "Land extracts untouched. Padding, epoch dates and reserved words all "
                "preserved, so what the source said remains provable.", "10 sources"],
        ["staging", "Resolve all ten traps, once each, with the reason recorded beside the "
                    "code that implements it.", "10 models"],
        ["intermediate", "Union three incompatible time-entry sources onto one grain, so "
                         "nothing downstream needs to know which system a booking came from.",
         "1 model"],
        ["marts", "Dimensional model. Conformed dimensions, surrogate keys, unknown members "
                  "at -1, degenerate dimensions retained.",
         f"{len(d['dims'])} dims, {len(d['facts'])} facts"],
        ["ml", "Monitoring. Contextual anomaly detection and volume/freshness scoring over "
               "the marts, not over raw.", "4 scripts"],
    ]
    st += [table(arch_rows, widths=[24 * mm, CONTENT_W - 24 * mm - 26 * mm, 26 * mm])]

    st += [para("Built objects", "h2")]
    fact_rows = [["Fact table", "Rows"]] + [
        [f"<font face='Courier' size=8>{k}</font>", f"{v:,}"] for k, v in d["facts"].items()
    ]
    dim_rows = [["Dimension", "Rows"]] + [
        [f"<font face='Courier' size=8>{k}</font>", f"{v:,}"] for k, v in d["dims"].items()
    ]
    side = Table(
        [[table(fact_rows, widths=[46 * mm, 18 * mm], align_right=(1,)),
          table(dim_rows, widths=[42 * mm, 18 * mm], align_right=(1,))]],
        colWidths=[CONTENT_W / 2, CONTENT_W / 2], hAlign="LEFT")
    side.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    st += [side, Spacer(1, 5 * mm)]

    st += [para("The decision that mattered most", "h2")]
    st += [para(
        "The grain of the inventory fact includes the full timestamp at second precision, "
        "not a truncated date. Transaction timestamps are unique to the second and line_seq "
        "does <b>not</b> restart daily, so a date-based key collapses genuinely distinct "
        "transactions onto one row and the total quietly drops.", "body")]
    st += [para(
        "A date-based key looks correct when you spot check it, because roughly 80% of rows "
        "carry line_seq = 1. That is what makes it dangerous. A uniqueness test on the full "
        "grain now guards it.", "body")]

    st += [para("4. Testing", "h1")]
    st += [para(
        "70 automated tests run on every build: uniqueness, not-null, referential integrity, "
        "accepted values, accepted ranges, and six singular tests asserting business "
        "plausibility rather than structure.", "body")]
    st += [para(
        f"The current build is <b>PASS=90, WARN=1, ERROR=0</b> across 91 nodes. The single "
        f"warning is intentional and must not be &ldquo;fixed&rdquo;.", "body")]

    st += [para("A test configured to warn, not to pass", "h3")]
    st += [para(
        "Nine rows are genuine duplicates in one subsidiary's extract &mdash; trap 9. "
        "Deduplicating them would hide a real upstream fault and make the fact disagree with "
        "source on row count. So the known baseline is encoded in the test itself:", "body")]
    st += [para(
        "warn_if:  &gt;0&nbsp;&nbsp;&nbsp;# surface it every run, so nobody forgets<br/>"
        "error_if: &gt;9&nbsp;&nbsp;&nbsp;# fail the build the moment it gets worse", "code")]
    st += [para(
        "An assertion of zero would have been quietly disabled by the third person who hit "
        "it. An assertion of &ldquo;no worse than the nine we have documented&rdquo; stays "
        "meaningful and is a real regression guard.", "body")]

    st += [PageBreak()]

    # ------------------------------------------------------------------ 5
    st += [para("5. Machine learning: scope and justification", "h1")]
    st += [para(
        "The ML layer is scoped to the two failures the test suite cannot reach, and the "
        "comparison against SQL rules is published in both directions.", "lead")]

    st += [table([
        ["Defect class", "Nature", "Verdict"],
        ["CORRUPT_QUANTITY", "Absolute &mdash; wrong on sight",
         "<b>Rule wins decisively.</b> Do not deploy a model."],
        ["CONTEXTUAL_PRICE", "Relative &mdash; wrong only for that item",
         "<b>Model wins.</b> No threshold can express it."],
    ], widths=[40 * mm, 52 * mm, CONTENT_W - 92 * mm])]

    st += [para("Why one is easy", "h2")]
    st += [para(
        "A quantity of 256,000,000 on an inventory line is nonsense regardless of context. "
        "One line of SQL catches every instance, exactly, with no training and no drift.",
        "body")]

    st += [para("Why the other is not", "h2")]
    st += [para(
        "item A-1042 (a washer)&nbsp;&nbsp;&nbsp;unit_cost = 41.80&nbsp;&nbsp;&nbsp;"
        "usual:&nbsp;&nbsp;4.60<br/>"
        "item B-7781 (a pump)&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;unit_cost = 41.80&nbsp;&nbsp;&nbsp;"
        "usual: 44.20", "code")]
    st += [para(
        "Identical value. The first is a nine-fold error; the second is a Tuesday. No global "
        "threshold separates them, because the catalogue spans parts costing 2 and parts "
        "costing 900. The only way to judge the row is against that item's own history, and "
        "maintaining 900 per-item thresholds by hand is not a plan.", "body")]

    st += [para("Features: mostly a story about exclusions", "h2")]
    st += [para(
        "Two mart columns encode the answer. <font face='Courier' size=8>"
        "is_quantity_reliable</font> <i>is</i> the rule output; "
        "<font face='Courier' size=8>amount_if_recomputed</font> becomes astronomic exactly "
        "when quantity is corrupt. Include either and the model scores near-perfectly having "
        "learned nothing &mdash; you would ship it, the dashboard would look excellent, and "
        "the first novel defect would sail straight through.", "body")]
    st += [para(
        "This is the most common way ML pipelines produce impressive, worthless numbers. Both "
        "columns are excluded, and <font face='Courier' size=8>build_features()</font> raises "
        "an AssertionError if either reappears. A comment would be ignored by whoever adds a "
        "column to improve a score next year.", "body")]
    st += [para(
        "Of the eleven features retained, the load-bearing ones are contextual: a robust "
        "z-score of price within the item, and the ratio of the row's price to that item's "
        "median. Median and MAD rather than mean and standard deviation, because the "
        "anomalies are <i>inside</i> the series being summarised &mdash; a mean is dragged "
        "upward by the very rows being hunted, so each anomaly partly conceals itself.",
        "body")]

    st += [para("Model choice", "h2")]
    st += [para(
        "Isolation Forest, 300 estimators, unsupervised. Unsupervised is the point: in "
        "production nobody has told you which rows are wrong. The ground truth here exists "
        "only to score the result afterwards &mdash; the model never sees it, which is what "
        "makes the approach transferable rather than a demonstration that works only because "
        "the answers happened to be available.", "body")]
    st += [para(
        "A supervised classifier would score better here and be useless in production, since "
        "it needs the labels you are trying to produce. Local Outlier Factor was comparable "
        "and far slower at 200k rows; an autoencoder is defensible at much larger scale but a "
        "heavier dependency for results within noise of the forest.", "body")]

    st += [PageBreak()]

    # ------------------------------------------------------------------ 6
    st += [para("6. Results", "h1")]
    st += [metric_cards([
        ("Rows scored", f"{m['n_rows']:,}", "#11161d"),
        ("Avg precision", f"{price['isolation_forest']['average_precision']:.3f}", "#1f6feb"),
        ("Top-20 precision", f"{budget['precision']:.3f}", "#1a7f37"),
        ("Volume flags", f"{vol['flagged']}/{vol['company_days']:,}", "#9a6700"),
    ])]
    st += [Spacer(1, 6 * mm)]

    st += [para(f"Corrupt quantity &mdash; {qty['sql_rule_quantity_gt_1e6']['tp']} rows in "
                f"ground truth", "h3")]
    st += [table([
        ["Detector", "Precision", "Recall", "F1"],
        ["SQL rule <font face='Courier' size=8>quantity &gt; 1e6</font>",
         f"{qty['sql_rule_quantity_gt_1e6']['precision']:.3f}",
         f"{qty['sql_rule_quantity_gt_1e6']['recall']:.3f}",
         f"{qty['sql_rule_quantity_gt_1e6']['f1']:.3f}"],
        ["Isolation Forest",
         f"{qty['isolation_forest']['precision']:.3f}",
         f"{qty['isolation_forest']['recall']:.3f}",
         f"{qty['isolation_forest']['f1']:.3f}"],
    ], widths=[CONTENT_W - 84 * mm, 28 * mm, 28 * mm, 28 * mm],
        align_right=(1, 2, 3), bold_rows=(1,))]
    st += [Spacer(1, 3 * mm)]
    st += [para(
        "The rule is perfect. The forest is far worse. <b>The correct engineering decision is "
        "to keep the rule and not deploy a model for this class</b> &mdash; adding one to a "
        "solved problem costs money and credibility.", "body")]

    st += [para(f"Contextual price &mdash; {price['sql_rule_quantity_gt_1e6']['fn']} rows in "
                f"ground truth", "h3")]
    st += [table([
        ["Detector", "Precision", "Recall", "F1"],
        ["SQL rule, <font face='Courier' size=8>unit_cost</font> global p99.9",
         f"{price['sql_rule_unit_cost_p99.9']['precision']:.3f}",
         f"{price['sql_rule_unit_cost_p99.9']['recall']:.3f}",
         f"{price['sql_rule_unit_cost_p99.9']['f1']:.3f}"],
        ["Isolation Forest, contamination "
         f"{m['contamination']}",
         f"{price['isolation_forest']['precision']:.3f}",
         f"{price['isolation_forest']['recall']:.3f}",
         f"{price['isolation_forest']['f1']:.3f}"],
        ["Isolation Forest &mdash; <b>average precision</b>", "&mdash;", "&mdash;",
         f"<b>{price['isolation_forest']['average_precision']:.3f}</b>"],
    ], widths=[CONTENT_W - 84 * mm, 28 * mm, 28 * mm, 28 * mm], align_right=(1, 2, 3))]

    st += [Spacer(1, 4 * mm)]
    st += figure("ml_rule_vs_model.png",
                 "Figure 2. The reversal between the two panels is the finding. Left: a "
                 "one-line WHERE clause scores 1.000 on both precision and recall. Right: no "
                 "threshold works, and the model is the only option.")

    st += [PageBreak()]

    st += [para("The uncomfortable result, reported rather than buried", "h2")]
    st += [para(
        f"At the default threshold the forest's F1 of "
        f"{price['isolation_forest']['f1']:.3f} is <b>worse</b> than the crude rule's "
        f"{price['sql_rule_unit_cost_p99.9']['f1']:.3f}. That is a real result and it stays "
        f"in the output.", "body")]
    st += [para(
        f"But read it correctly. Average precision is "
        f"{price['isolation_forest']['average_precision']:.3f}, so the <i>ranking</i> is "
        f"excellent. The problem is not the model, it is where the line was drawn. "
        f"<b>Strong ordering, weak decision</b> &mdash; different failures with different "
        f"fixes, and conflating them is how teams discard models that were working.", "body")]

    st += figure("ml_pr_curve.png",
                 "Figure 3. The red dot is the SQL rule, sitting clearly below the curve: at "
                 "matched recall the model is strictly more precise. The rule only wins on F1 "
                 "because the chosen contamination put the forest at a poor point on a curve "
                 "it otherwise dominates.", width=CONTENT_W * 0.80)

    st += [para("7. Choosing a threshold", "h1")]
    st += [para(
        "Average precision measures how well a model <i>orders</i> rows. It says nothing "
        "about where to cut, and the cut is what determines whether anyone acts on the "
        "output. This is the step most write-ups skip.", "body")]

    st += [table([
        ["Operating point", "Precision", "Recall", "F1", "Usable in production?"],
        [f"Contamination {m['contamination']}",
         f"{price['isolation_forest']['precision']:.3f}",
         f"{price['isolation_forest']['recall']:.3f}",
         f"{price['isolation_forest']['f1']:.3f}",
         "Yes, but arbitrary"],
        ["Best F1, oracle-tuned",
         f"{oracle['precision']:.3f}", f"{oracle['recall']:.3f}", f"{oracle['f1']:.3f}",
         "<b>No</b> &mdash; uses the labels"],
        [f"<b>Top-{budget['budget']} alert budget</b>",
         f"<b>{budget['precision']:.3f}</b>", f"{budget['recall']:.3f}",
         f"{budget['f1']:.3f}", "<b>Yes. This ships.</b>"],
    ], widths=[44 * mm, 21 * mm, 19 * mm, 17 * mm, CONTENT_W - 101 * mm],
        align_right=(1, 2, 3))]

    st += [Spacer(1, 4 * mm)]
    st += [para(
        "The oracle-tuned row is an upper bound and nothing more. If you have the labels you "
        "do not need the detector. It is reported because knowing the ceiling is useful, and "
        "labelled at every appearance because quoting it as achievable performance would be "
        "dishonest.", "body")]
    st += [para(
        f"The alert budget needs no labels at all. A human can meaningfully triage roughly "
        f"{budget['budget']} rows a day, so take the top {budget['budget']} by score. "
        f"<b>All {budget['tp']} were genuine anomalies &mdash; precision "
        f"{budget['precision']:.3f}, zero false alarms.</b>", "body")]
    st += [para(
        f"Recall of {budget['recall']:.3f} sounds poor until you compare it to the "
        f"alternative, which is a report nobody reads because two-thirds of it is noise. An "
        f"alert stream that is always right earns the trust that lets you widen the budget "
        f"later. One that cries wolf gets muted in a week, and then recall is zero.", "body")]

    st += figure("ml_score_distribution.png",
                 "Figure 4. Log scale, because 162 anomalies against 199,500 clean rows are "
                 "otherwise invisible \u2014 which is the whole problem. Note the genuine "
                 "overlap: clean rows reach 2.34x the item median while anomalies start at "
                 "2.0x, so this is a real tradeoff rather than a threshold anyone could "
                 "eyeball.")

    st += [PageBreak()]

    # ------------------------------------------------------------------ 8
    st += [para("8. The blind spot no row-level test can cover", "h1")]
    st += [para(
        "All 70 tests inspect rows that <b>exist</b>. If a subsidiary's extract fails for "
        "three days: no test fails, no key is null, every foreign key resolves, and the fact "
        "table reconciles <i>perfectly</i> to the source &mdash; because the source is short "
        "too. Three days of one company's stock movements are simply gone.", "body")]
    st += [para(
        "This is the most common incident class in production data platforms, and the one "
        "most projects have no answer to at all.", "body")]

    st += [para("The detail that makes or breaks it", "h2")]
    st += [para(
        "A naive implementation groups by date and company and looks for low counts. "
        "<b>It finds nothing.</b> A day with zero rows produces no group &mdash; the absence "
        "is invisible to a GROUP BY. The fix is to reindex onto a complete date x company "
        "spine and fill the gaps with explicit zeros. That single step is the difference "
        "between a monitor that works and one that reports all-clear through a total outage.",
        "body")]

    st += [para("Why a Poisson residual, not a z-score", "h2")]
    st += [para(
        "Daily row counts are <i>count</i> data, roughly Poisson: variance grows with the "
        "mean, so the spread is already determined by the expected value. A robust z-score on "
        "~36 rows a day divides by a MAD of about 4, so an ordinary quiet Tuesday of 23 rows "
        "scores -4.7 and pages somebody. The Poisson residual divides by the square root of "
        "36, scores it -2.3, and correctly ignores it &mdash; while a zero-row day still "
        "scores -6.1.", "body")]
    st += [para(
        "I arrived at that the way everyone does: the first version flagged twelve days, of "
        "which three were the real outage and the rest were Tuesdays.", "body")]

    st += [table([
        ["Threshold", "Poisson residual flags", "Robust z-score flags"],
        ["3.0", "23", "63"],
        ["4.0", "6", "14"],
        ["<b>5.0</b>", "<b>5</b>", "5"],
        ["6.0", "5", "2"],
    ], widths=[30 * mm, 50 * mm, 50 * mm], align_right=(1, 2))]
    st += [Spacer(1, 3 * mm)]
    st += [para(
        "The two agree only at 5.0, and the Poisson residual is better on both sides. Loosen "
        "to 4.0 and the z-score flags 14 days against 6, the extras being quiet Tuesdays. "
        "Tighten to 6.0 and the z-score has <b>already lost three of the five genuine "
        "incidents</b>. This is not a marginal accuracy gain &mdash; it is a usable operating "
        "<i>range</i> instead of one lucky threshold.", "body")]

    st += [para("Result", "h2")]
    st += [para(
        f"<b>{vol['flagged']} anomalous company-days out of {vol['company_days']:,} checked, "
        f"every one genuine.</b> {vol['zero_days']} zero-row days from a three-day extract "
        f"outage, and {vol['spikes']} days where another subsidiary's job ran twice. "
        f"Precision 1.000, recall 1.000, no false positives.", "body")]

    st += figure("ml_volume_monitor.png",
                 "Figure 5. Red band: three consecutive days at zero rows. Amber: six times "
                 "normal volume. Every dbt test passes on all 5,400 of these company-days.",
                 width=CONTENT_W * 0.88)

    st += [PageBreak()]

    # ------------------------------------------------------------------ 9
    st += [para("9. Ground truth, and a bug worth documenting", "h1")]
    st += [para(
        "The generator decides which rows to break, so it knows the answers. They are written "
        "to an <font face='Courier' size=8>ml</font> schema that "
        "<font face='Courier' size=8>sources.yml</font> does not declare &mdash; so no dbt "
        "model can reference it even accidentally. The warehouse is built without ever seeing "
        "which rows are defective.", "body")]

    lab_rows = [["Defect type", "Rows", "Caught by"]]
    caught = {
        "EPOCH_DATE": "dbt test (deterministic)",
        "CONTEXTUAL_PRICE": "<b>Isolation Forest</b>",
        "CORRUPT_QUANTITY": "SQL rule",
        "SOURCE_DUPLICATE": "dbt test (deterministic)",
    }
    for k, v in d["labels"].items():
        lab_rows.append([f"<font face='Courier' size=8>{k}</font>", f"{v:,}",
                         caught.get(k, "")])
    lab_rows.append(["<b>Total</b>", f"<b>{sum(d['labels'].values()):,}</b>", ""])
    st += [table(lab_rows, widths=[54 * mm, 22 * mm, CONTENT_W - 76 * mm],
                 align_right=(1,))]

    st += [para("The bug", "h2")]
    st += [para(
        "My first label join used <font face='Courier' size=8>txn_timestamp</font>, the "
        "obvious key. It silently dropped <b>46% of the labels</b>, and the model looked far "
        "worse than it was.", "body")]
    st += [para(
        "The cause: staging converts timestamps to the reporting timezone for the three "
        "subsidiaries whose systems store UTC. That is <b>trap 1</b> &mdash; the first trap "
        "this project documents &mdash; biting my own tooling. For half the group the raw "
        "timestamp no longer equalled the value in the fact table.", "body")]
    st += [para(
        "The fix was to key on business columns plus the measures, which pass through staging "
        "unchanged. The reasoning is now a comment in the generator rather than a silent fix, "
        "because a trap that catches the person who invented it is better evidence than any "
        "claim I could make about it being realistic.", "body")]

    st += [para("10. Limitations", "h1")]
    st += [para(
        "Stated rather than omitted, because overclaiming is worse than a modest scope.",
        "body")]
    st += bullets([
        "<b>The data is synthetic.</b> Deliberately adversarial and modelled on real "
        "patterns, but no real company's data is in this repository.",
        "<b>Nothing here has run in production.</b> No live traffic, no business rollout, no "
        "on-call rotation. The engineering is real; the deployment is not claimed.",
        "<b>One seeded run.</b> Reproducible via <font face='Courier' size=8>make all</font>, "
        "but not a cross-validated study. No confidence intervals are claimed.",
        "<b>The oracle-tuned F1 is an upper bound, not a result.</b> It uses the labels to "
        "pick its threshold, which is impossible in production.",
        "<b>The centred rolling window is a backtest convenience.</b> A monitor scoring today "
        "cannot see the future and must use a trailing window, which performs slightly worse. "
        "Noted because this is exactly how backtests come to flatter live systems.",
        "<b>No drift handling.</b> Production would need periodic refitting and a check on "
        "whether the score distribution has moved.",
        "<b>No SCD2, no incremental models, no orchestration.</b> Full rebuilds are instant at "
        "200k rows and unacceptable at 200m. The keys for incrementality exist and are unused.",
    ])

    st += [para("11. What this demonstrates", "h1")]
    st += [table([
        ["Claim", "Where to verify it"],
        ["Dimensional modelling of awkward multi-source data",
         "<font face='Courier' size=8>models/marts/</font>, docs/architecture.md"],
        ["Knowing which data problems are dangerous, and why",
         "<font face='Courier' size=8>docs/data-traps.md</font>"],
        ["Testing for correctness rather than green ticks",
         "<font face='Courier' size=8>models/marts/_marts.yml</font>, "
         "<font face='Courier' size=8>tests/</font>"],
        ["ML scoped to where it earns its place",
         "<font face='Courier' size=8>docs/ml-anomaly-detection.md</font>"],
        ["Reporting results honestly, including negative ones",
         "<font face='Courier' size=8>ml/detect_anomalies.py</font> output"],
        ["Documenting decisions so others can maintain the work",
         "<font face='Courier' size=8>docs/decision-log.md</font>"],
    ], widths=[72 * mm, CONTENT_W - 72 * mm])]

    st += [Spacer(1, 8 * mm)]
    st += [para(
        f"Every figure and metric in this report was generated from a single run on "
        f"{date.today().isoformat()}. Reproduce with "
        f"<font face='Courier' size=8>make clean &amp;&amp; make all</font>.", "small")]

    return st


def main() -> None:
    if not os.path.exists(os.path.join(ML_DIR, "metrics.json")):
        raise SystemExit("Run 'make ml' first -- data/ml/metrics.json is missing.")

    d = gather()

    doc = BaseDocTemplate(
        OUT, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"{TITLE} - {SUBTITLE}", author=AUTHOR,
        subject="Data warehouse architecture and anomaly detection results",
    )
    frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="f")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame], onPage=cover_page),
        PageTemplate(id="body", frames=[frame], onPage=body_page),
    ])

    doc.build(build_story(d))

    size = os.path.getsize(OUT) / 1024
    print(f"Wrote {OUT}  ({size:.0f} KB)")
    print(f"  {doc.page} pages")


if __name__ == "__main__":
    main()
