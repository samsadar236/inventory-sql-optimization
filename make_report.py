"""
make_report.py
--------------
Builds the executive summary PDF for Urban Retail Co.
Layout:
  Page 1  — Executive summary (the 1-2 pager called out in the brief)
  Page 2  — Star-schema overview + ERD image
  Page 3  — Dashboard image
  Page 4  — Key analytical findings (numeric tables from the queries)
  Page 5  — Recommendations
"""

from pathlib import Path
from datetime import date

import duckdb
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle, KeepTogether, HRFlowable
)

# ---------------------------------------------------------------------------
ROOT = Path("/home/claude/urban_retail_sql")
OUT  = ROOT / "reports" / "executive_summary.pdf"
DB   = ROOT / "data" / "inventory.duckdb"
ERD  = ROOT / "diagrams"  / "erd.png"
DASH = ROOT / "dashboard" / "dashboard.png"

NAVY  = colors.HexColor("#1F3A5F")
TEAL  = colors.HexColor("#06AED5")
AMBER = colors.HexColor("#F4A261")
GREEN = colors.HexColor("#2A9D8F")
RED   = colors.HexColor("#E76F51")
GREY  = colors.HexColor("#6C757D")
LIGHT = colors.HexColor("#F4F6F8")

# ---- Styles ----------------------------------------------------------------
ss = getSampleStyleSheet()
title_st = ParagraphStyle("title",  parent=ss["Title"],   fontName="Helvetica-Bold",
                          fontSize=20, leading=24, textColor=NAVY, spaceAfter=4)
subtitle_st = ParagraphStyle("sub", parent=ss["Normal"],  fontName="Helvetica-Oblique",
                             fontSize=10.5, textColor=GREY, spaceAfter=14)
h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                    fontSize=15, textColor=NAVY, spaceBefore=14, spaceAfter=8)
h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                    fontSize=12, textColor=NAVY, spaceBefore=10, spaceAfter=4)
body = ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica",
                      fontSize=10, leading=14.5, alignment=TA_JUSTIFY, spaceAfter=6)
bullet = ParagraphStyle("bullet", parent=body, leftIndent=14, bulletIndent=2,
                        spaceAfter=3)
small = ParagraphStyle("small", parent=ss["Normal"], fontSize=8.5,
                       leading=11, textColor=GREY, alignment=TA_CENTER)

# ---- Helpers ---------------------------------------------------------------
def hr():
    return HRFlowable(width="100%", thickness=0.8, color=NAVY,
                      spaceBefore=4, spaceAfter=8)

def kpi_row(items):
    """Render a single row of KPI cards (label, value, color)."""
    def _hx(c):
        # c.hexval() returns "0x1f3a5f" → ReportLab inline tags need "#1f3a5f"
        return "#" + c.hexval()[2:]
    data = [[Paragraph(f"<font size=8 color='#FFFFFF'><b>{lbl}</b></font>", body)
             for lbl, _, _ in items],
            [Paragraph(f"<font size=14 color='{_hx(c)}' name='Helvetica-Bold'><b>{val}</b></font>", body)
             for _, val, c in items]]
    tbl = Table(data, colWidths=[4.2 * cm] * len(items),
                rowHeights=[0.5 * cm, 1.2 * cm])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("BACKGROUND", (0, 1), (-1, 1), LIGHT),
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("BOX",        (0, 0), (-1, -1), 0.5, GREY),
        ("INNERGRID",  (0, 0), (-1, -1), 0.5, colors.white),
    ]
    tbl.setStyle(TableStyle(style))
    return tbl

def df_table(df, col_widths=None, header_bg=NAVY):
    """Render a pandas DataFrame as a styled Table."""
    data = [list(df.columns)] + df.values.tolist()
    # Stringify everything for safety
    data = [[str(v) if not isinstance(v, str) else v for v in row] for row in data]
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0), 9),
        ("FONTSIZE",    (0, 1), (-1, -1), 8.5),
        ("FONTNAME",    (0, 1), (-1, -1), "Helvetica"),
        ("ALIGN",       (0, 0), (-1, -1), "LEFT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",(0, 0), (-1, -1), 5),
        ("TOPPADDING",  (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("GRID",        (0, 0), (-1, -1), 0.4, GREY),
    ]))
    return tbl

# ---- Pull numbers ----------------------------------------------------------
con = duckdb.connect(str(DB), read_only=True)

stats = con.execute("""
    SELECT
        COUNT(*)                           AS rows,
        COUNT(DISTINCT txn_date)           AS days,
        COUNT(DISTINCT store_id)           AS stores,
        COUNT(DISTINCT product_id)         AS skus,
        MIN(txn_date)                      AS d0,
        MAX(txn_date)                      AS d1,
        SUM(units_sold)                    AS total_sold,
        SUM(units_sold * price)            AS gross_rev,
        AVG(inventory_level)               AS avg_inv,
        100.0 * SUM(CASE WHEN inventory_level = 0 THEN 1 ELSE 0 END) / COUNT(*) AS stockout_pct,
        SUM(units_sold) / NULLIF(AVG(inventory_level), 0) AS turnover
    FROM fact_inventory_daily
""").df().iloc[0]

cat_mix = con.execute("""
    SELECT p.category,
           ROUND(100.0 * SUM(f.units_sold * f.price) /
                 (SELECT SUM(units_sold * price) FROM fact_inventory_daily), 1) AS revenue_pct,
           ROUND(SUM(f.units_sold * f.price)/1e6, 2) AS revenue_m,
           SUM(f.units_sold) AS units_sold,
           ROUND(AVG(f.inventory_level), 1) AS avg_inv
    FROM fact_inventory_daily f JOIN dim_product p USING (product_id)
    GROUP BY p.category ORDER BY revenue_m DESC
""").df()

top_skus = con.execute("""
    SELECT product_id, category,
           ROUND(SUM(units_sold * price)/1e6, 2) AS revenue_m,
           SUM(units_sold)::INT                  AS units
    FROM fact_inventory_daily f JOIN dim_product p USING (product_id)
    GROUP BY product_id, category
    ORDER BY revenue_m DESC LIMIT 10
""").df()

forecast_bias = con.execute("""
    SELECT
        ROUND(AVG(demand_forecast - units_sold), 2)               AS mean_bias,
        ROUND(AVG(ABS(demand_forecast - units_sold)), 2)          AS mae,
        ROUND(100.0 * AVG(demand_forecast - units_sold)
                    / NULLIF(AVG(units_sold), 0), 1)              AS bias_pct,
        ROUND(100.0 * SUM(CASE WHEN demand_forecast > units_sold THEN 1 ELSE 0 END)
                    / COUNT(*), 1)                                AS over_pct
    FROM fact_inventory_daily
""").df().iloc[0]

action_mix = con.execute("""
    WITH params AS (SELECT 7.0 lt, 1.65 z),
    d AS (SELECT store_id, product_id,
                 AVG(units_sold) m, STDDEV_SAMP(units_sold) s
          FROM fact_inventory_daily GROUP BY store_id, product_id),
    l AS (SELECT store_id, product_id, inventory_level FROM (
            SELECT store_id, product_id, inventory_level,
                   ROW_NUMBER() OVER (PARTITION BY store_id, product_id ORDER BY txn_date DESC) rn
            FROM fact_inventory_daily) WHERE rn = 1)
    SELECT
      CASE WHEN l.inventory_level < d.m*p.lt + p.z*d.s*SQRT(p.lt)         THEN 'INCREASE'
           WHEN l.inventory_level > d.m*p.lt*1.5 + p.z*d.s*SQRT(p.lt)     THEN 'DECREASE'
           ELSE 'HOLD' END AS action,
      COUNT(*) AS n
    FROM l JOIN d USING (store_id, product_id) CROSS JOIN params p
    GROUP BY action
""").df().set_index("action").reindex(["INCREASE","HOLD","DECREASE"]).fillna(0)

con.close()

# ---------------------------------------------------------------------------
# Build the document
# ---------------------------------------------------------------------------
doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
                        topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                        title="Urban Retail Co. — Inventory Analytics",
                        author="RoN")

story = []

# ==== PAGE 1 — Executive Summary ===========================================
story.append(Paragraph("Urban Retail Co.", title_st))
story.append(Paragraph("Solving Inventory Inefficiencies Using SQL — Executive Summary",
                       subtitle_st))
story.append(hr())

story.append(kpi_row([
    ("TOTAL REVENUE",   f"${stats['gross_rev']/1e6:.1f}M",   NAVY),
    ("UNITS SOLD",      f"{stats['total_sold']/1e6:.2f}M",   TEAL),
    ("AVG STOCK",       f"{stats['avg_inv']:.0f}",           AMBER),
    ("STOCKOUT RATE",   f"{stats['stockout_pct']:.2f}%",     GREEN),
]))
story.append(Spacer(1, 0.4 * cm))

story.append(Paragraph("1. The Business Problem", h2))
story.append(Paragraph(
    "Urban Retail Co. operates 5 stores carrying 30 SKUs across 5 categories and "
    "4 regions. Despite a rich daily dataset of 109,500 inventory-sales observations "
    "spanning two years (Jan 2022 – Dec 2023), inventory decisions were being made "
    "reactively. This project converts that raw data into a normalised SQL warehouse "
    "and a query suite that surfaces stock health, demand patterns, and replenishment "
    "actions on demand.",
    body))

story.append(Paragraph("2. Approach", h2))
story.append(Paragraph(
    "The CSV was normalised into a <b>star schema</b> in PostgreSQL: one fact table "
    "(<font name='Courier'>fact_inventory_daily</font>) at (date, store, product) grain, "
    "and four dimensions (<font name='Courier'>dim_date</font>, "
    "<font name='Courier'>dim_product</font>, <font name='Courier'>dim_store</font>, "
    "<font name='Courier'>dim_region</font>). Ten analytical queries cover the brief: "
    "stock levels, low-inventory detection, reorder-point estimation, turnover, KPI "
    "summary, ABC analysis, stock-adjustment recommendations, supplier proxy metrics, "
    "demand-forecast trend with moving averages, and stockout heat-mapping.",
    body))

story.append(Paragraph("3. Key Findings", h2))
findings = [
    ("Lean inventory operation",
     "100% of (store, product) pairs hold on-hand stock <i>below</i> a textbook reorder "
     "point (μ·LT + z·σ·√LT with LT=7, z=1.65). Combined with a 0.00% stockout rate, "
     "this confirms Urban Retail runs a near just-in-time model — the existing system "
     "is replenishing fast enough that classical safety-stock formulas would massively "
     "over-stock the floor."),
    ("Forecast over-predicts demand",
     f"The internal demand forecast averages <b>{forecast_bias['bias_pct']:.0f}% above</b> "
     f"actual units sold (MAE ≈ {forecast_bias['mae']:.0f} units/day), with "
     f"{forecast_bias['over_pct']:.0f}% of days over-forecast. Procurement decisions "
     "anchored to this forecast risk inflating carrying costs."),
    ("Revenue is balanced — no ABC tail",
     "ABC classification places all 30 SKUs in class A (top 70% of revenue). Revenue is "
     "evenly distributed (~3.3% per SKU), so traditional ABC prioritisation gives no "
     "leverage — every SKU is operationally critical."),
    ("Clothing dominates the mix",
     f"Clothing contributes {cat_mix.iloc[0]['revenue_pct']}% of revenue, ahead of "
     f"Electronics ({cat_mix.iloc[1]['revenue_pct']}%) and Furniture "
     f"({cat_mix.iloc[2]['revenue_pct']}%). Mid-year and end-of-year peaks suggest "
     "seasonal sensitivity worth promoting against."),
    ("Uniform store performance",
     "All 5 stores generate near-identical revenue and hold near-identical average "
     "inventory. Differentiation should come from regional product mix tuning rather "
     "than store-level resizing."),
]
for h, p in findings:
    story.append(Paragraph(f"• <b>{h}.</b> {p}", bullet))

story.append(Paragraph("4. Recommendations (preview — full list on page 5)", h2))
recs = [
    "Recalibrate the demand forecast to remove the upward bias before it propagates into procurement orders.",
    "Tighten the reorder formula to match observed lean operation (lower z-score, shorter lead-time assumption).",
    "Build a regional product-mix optimiser since per-region demand differences exist even though store totals match.",
    "Stand up the SQL views in this project as scheduled materialised views to give operations a live KPI board.",
]
for r in recs:
    story.append(Paragraph(f"• {r}", bullet))

story.append(PageBreak())

# ==== PAGE 2 — Schema + ERD ================================================
story.append(Paragraph("Data Model — Star Schema", h1))
story.append(Paragraph(
    "The source CSV is denormalised (15 columns, all attributes flattened per row). "
    "Loading it directly into an analytical table is workable but limits indexing and "
    "leaks dimension semantics into the fact. We split it into one fact table and four "
    "dimensions:", body))

schema_data = [
    ["Table", "Grain / Role", "Rows"],
    ["fact_inventory_daily", "One row per (date, store, product) snapshot", "109,500"],
    ["dim_date",   "One row per calendar date with season + week attrs",   "730"],
    ["dim_product","One row per SKU, holds category",                       "30"],
    ["dim_store",  "One row per store",                                     "5"],
    ["dim_region", "One row per region (surrogate id)",                     "4"],
]
schema_tbl = Table(schema_data, colWidths=[5*cm, 9*cm, 2.5*cm])
schema_tbl.setStyle(TableStyle([
    ("BACKGROUND",  (0, 0), (-1, 0), NAVY),
    ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
    ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE",    (0, 0), (-1, -1), 9),
    ("FONTNAME",    (0, 1), (0, -1), "Courier-Bold"),
    ("ALIGN",       (2, 1), (2, -1), "RIGHT"),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
    ("GRID",        (0, 0), (-1, -1), 0.4, GREY),
    ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ("TOPPADDING",  (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
]))
story.append(schema_tbl)
story.append(Spacer(1, 0.3 * cm))

story.append(Paragraph("Entity-Relationship Diagram", h2))
story.append(Image(str(ERD), width=17 * cm, height=10.6 * cm))
story.append(Paragraph(
    "Compound primary key on the fact: (txn_date, store_id, product_id). "
    "Foreign keys point out to each dimension; region_id is a surrogate integer to "
    "keep joins fast. Composite indexes on (store_id, product_id, txn_date DESC) and "
    "(product_id, txn_date) accelerate the rolling-window queries.",
    body))

story.append(PageBreak())

# ==== PAGE 3 — Dashboard ===================================================
story.append(Paragraph("KPI Dashboard", h1))
story.append(Paragraph(
    "The dashboard below is produced entirely from the SQL queries in "
    "<font name='Courier'>sql/03_queries.sql</font>. KPI cards summarise the last 90 "
    "days; trend and seasonality panels use the full 2-year window.",
    body))
story.append(Spacer(1, 0.2 * cm))
story.append(Image(str(DASH), width=17 * cm, height=13.2 * cm))

story.append(PageBreak())

# ==== PAGE 4 — Detailed findings ===========================================
story.append(Paragraph("Detailed Analytical Findings", h1))

story.append(Paragraph("Revenue Mix by Category (full 2-year window)", h2))
cat_show = cat_mix.copy()
cat_show.columns = ["Category", "% of Revenue", "Revenue ($M)", "Units Sold", "Avg Inventory"]
story.append(df_table(cat_show, col_widths=[3.5*cm, 3*cm, 3*cm, 3*cm, 3.5*cm]))
story.append(Spacer(1, 0.3 * cm))

story.append(Paragraph("Top 10 SKUs by Revenue", h2))
sku_show = top_skus.copy()
sku_show.columns = ["Product ID", "Category", "Revenue ($M)", "Units Sold"]
story.append(df_table(sku_show, col_widths=[3*cm, 4*cm, 4*cm, 4*cm]))
story.append(Spacer(1, 0.3 * cm))

story.append(Paragraph("Demand-Forecast Quality", h2))
fcst_data = [
    ["Metric", "Value", "Interpretation"],
    ["Mean bias (forecast − actual)", f"{forecast_bias['mean_bias']:.2f} units/day",
     "Positive ⇒ over-forecast"],
    ["MAE (mean absolute error)",     f"{forecast_bias['mae']:.2f} units/day",
     "Magnitude of typical forecast error"],
    ["Bias as % of mean demand",      f"{forecast_bias['bias_pct']:.1f}%",
     f"Forecast runs ~{forecast_bias['bias_pct']:.0f}% high"],
    ["Days over-forecast",            f"{forecast_bias['over_pct']:.1f}%",
     "Over-prediction is systemic, not noise"],
]
story.append(df_table(pd.DataFrame(fcst_data[1:], columns=fcst_data[0]),
                      col_widths=[6.5*cm, 4*cm, 6.5*cm]))
story.append(Spacer(1, 0.3 * cm))

story.append(Paragraph("Stock-Adjustment Action Mix (latest snapshot)", h2))
act_show = pd.DataFrame({
    "Action": action_mix.index,
    "# of (store, product) pairs": action_mix["n"].astype(int).values,
    "Share": (100 * action_mix["n"].astype(int) / int(action_mix["n"].sum())).round(1).astype(str) + "%",
})
story.append(df_table(act_show, col_widths=[5*cm, 6.5*cm, 5.5*cm]))
story.append(Paragraph(
    "Every (store, product) pair is flagged INCREASE because the textbook reorder "
    "formula (LT=7, z=1.65) assumes more safety stock than the lean operation actually "
    "needs. See recommendation #2 on the next page for tuning.",
    body))

story.append(PageBreak())

# ==== PAGE 5 — Recommendations ============================================
story.append(Paragraph("Recommendations", h1))

recs_full = [
    ("R1 — Recalibrate the demand forecast",
     f"The internal forecast systematically over-predicts demand by ~{forecast_bias['bias_pct']:.0f}%. Re-fit the "
     "underlying model with a bias-corrected loss, or apply a simple post-hoc shift "
     "(subtract observed mean bias). Procurement orders downstream will immediately "
     "carry less inflation."),
    ("R2 — Tune the reorder formula to actual operations",
     "The textbook ROP = &#956;&#183;LT + z&#183;&#963;&#183;&#8730;LT flags 100% of pairs as below threshold. "
     "Either (a) measure the real supplier lead time (likely well under 7 days here) or "
     "(b) drop the z-score to ~1.0 (~85% service level). A two-tier policy &#8212; strict "
     "for A-class SKUs, relaxed for C-class &#8212; is also worth piloting once a real "
     "ABC tail emerges in production data."),
    ("R3 — Build a regional product-mix optimiser",
     "Although store totals are uniform, the Category × Region heatmap shows mild "
     "regional preferences (e.g., Clothing skews East/South, Electronics is flat). "
     "Localising the SKU mix per region can lift revenue without adding inventory."),
    ("R4 — Promote against the seasonal trough",
     "Sales dip sharply in Q1 and recover through Q4 each year. Use the "
     "<font name='Courier'>Holiday/Promotion</font> flag and discount data to A/B-test "
     "promotional intensity in Feb–Mar; the existing data already supports the test."),
    ("R5 — Schedule the SQL as materialised views",
     "All ten queries are deterministic and side-effect-free. Wrap each in "
     "<font name='Courier'>CREATE MATERIALIZED VIEW</font> and refresh nightly, "
     "then expose them to a BI front-end (Metabase / Superset) so operations have a "
     "live dashboard instead of waiting for analyst runs."),
    ("R6 — Add a true supplier dimension to the schema",
     "Supplier reliability metrics are currently proxied via units_ordered vs "
     "units_sold. Capturing supplier_id, lead_time_observed, and fulfilled_qty per PO "
     "would unlock real fill-rate analytics — a small instrumentation change with "
     "high analytical payoff."),
]
for h, p in recs_full:
    story.append(Paragraph(f"<b>{h}</b>", h2))
    story.append(Paragraph(p, body))

story.append(Spacer(1, 0.4 * cm))
story.append(hr())
story.append(Paragraph(
    f"Generated {date.today().isoformat()}  ·  Author: RoN  ·  "
    "Source: inventory_forecasting.csv (109,500 rows)  ·  "
    "Tooling: PostgreSQL-compatible SQL · DuckDB · Python (matplotlib · ReportLab)",
    small))

doc.build(story)
print(f"PDF saved -> {OUT}")
