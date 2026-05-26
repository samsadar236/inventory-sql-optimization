"""
make_dashboard.py
-----------------
Generates a single dashboard PNG that summarises the analytics produced by
the SQL queries. Reads from data/inventory.duckdb so the visualisations
inherit the exact numbers shown in the queries.

Layout: 4 KPI cards on top, then a 3x3 grid of charts beneath.
"""

from pathlib import Path
import duckdb
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd
from matplotlib.gridspec import GridSpec

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROOT = Path("/home/claude/urban_retail_sql")
OUT  = ROOT / "dashboard" / "dashboard.png"
DB   = ROOT / "data" / "inventory.duckdb"

COL = {
    "navy":   "#1F3A5F",
    "blue":   "#4A6FA5",
    "teal":   "#06AED5",
    "amber":  "#F4A261",
    "red":    "#E76F51",
    "green":  "#2A9D8F",
    "grey":   "#6C757D",
    "bg":     "#F7F8FA",
    "card":   "#FFFFFF",
}

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.titleweight": "bold",
    "axes.titlesize": 11,
    "axes.titlecolor": COL["navy"],
    "axes.labelsize": 9.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "axes.edgecolor": "#888",
})

con = duckdb.connect(str(DB), read_only=True)

# ---------------------------------------------------------------------------
# Pull the data we need
# ---------------------------------------------------------------------------
kpi = con.execute("""
    WITH w AS (
      SELECT * FROM fact_inventory_daily
      WHERE txn_date >= (SELECT MAX(txn_date) - INTERVAL 90 DAY FROM fact_inventory_daily)
    )
    SELECT
        SUM(units_sold)                                            AS units_sold,
        SUM(units_sold * price)                                    AS revenue,
        AVG(inventory_level)                                       AS avg_stock,
        100.0 * SUM(CASE WHEN inventory_level = 0 THEN 1 ELSE 0 END) / COUNT(*)
                                                                   AS stockout_pct,
        SUM(units_sold) / NULLIF(AVG(inventory_level), 0)          AS turnover
    FROM w;
""").df().iloc[0]

daily = con.execute("""
    SELECT txn_date,
           SUM(units_sold)      AS units_sold,
           SUM(demand_forecast) AS forecast,
           AVG(inventory_level) AS avg_inv
    FROM fact_inventory_daily
    GROUP BY txn_date ORDER BY txn_date
""").df()
daily["ma_7"]  = daily["units_sold"].rolling(7,  min_periods=1).mean()
daily["ma_30"] = daily["units_sold"].rolling(30, min_periods=1).mean()

abc = con.execute("""
    WITH r AS (
      SELECT f.product_id, p.category,
             SUM(f.units_sold)              AS units_sold,
             SUM(f.units_sold * f.price)    AS revenue
      FROM fact_inventory_daily f
      JOIN dim_product p USING (product_id)
      GROUP BY f.product_id, p.category
    ),
    k AS (
      SELECT *,
             100.0 * revenue / SUM(revenue) OVER ()             AS pct,
             100.0 * SUM(revenue) OVER (ORDER BY revenue DESC
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                     / SUM(revenue) OVER ()                     AS cum_pct
      FROM r
    )
    SELECT *,
           CASE WHEN cum_pct <= 70 THEN 'A' WHEN cum_pct <= 90 THEN 'B' ELSE 'C' END AS abc
    FROM k ORDER BY revenue DESC
""").df()

cat_sales = con.execute("""
    SELECT p.category, SUM(f.units_sold * f.price) AS revenue,
           SUM(f.units_sold) AS units
    FROM fact_inventory_daily f
    JOIN dim_product p USING (product_id)
    GROUP BY p.category ORDER BY revenue DESC
""").df()

heat = con.execute("""
    SELECT p.category, r.region_name,
           AVG(f.units_sold) AS avg_sales
    FROM fact_inventory_daily f
    JOIN dim_product p USING (product_id)
    JOIN dim_region  r USING (region_id)
    GROUP BY p.category, r.region_name
""").df()
heat_pivot = heat.pivot(index="category", columns="region_name", values="avg_sales")

inv_dist = con.execute("""
    SELECT inventory_level FROM fact_inventory_daily
""").df()

fcast = con.execute("""
    SELECT txn_date,
           SUM(units_sold)::DOUBLE      AS sold,
           SUM(demand_forecast)::DOUBLE AS forecast
    FROM fact_inventory_daily
    GROUP BY txn_date ORDER BY txn_date
""").df()
fcast["err_pct"] = 100 * (fcast["forecast"] - fcast["sold"]) / fcast["sold"]

monthly = con.execute("""
    SELECT EXTRACT(MONTH FROM txn_date)::INT AS month,
           strftime(txn_date, '%b')           AS m_label,
           SUM(units_sold)                    AS units
    FROM fact_inventory_daily
    GROUP BY month, m_label ORDER BY month
""").df()

action = con.execute("""
    WITH params AS (SELECT 7.0 lt, 1.65 z),
    d AS (SELECT store_id, product_id,
                 AVG(units_sold) m, STDDEV_SAMP(units_sold) s
          FROM fact_inventory_daily GROUP BY store_id, product_id),
    l AS (SELECT store_id, product_id, inventory_level FROM (
            SELECT store_id, product_id, inventory_level,
                   ROW_NUMBER() OVER (PARTITION BY store_id, product_id
                                      ORDER BY txn_date DESC) rn
            FROM fact_inventory_daily) WHERE rn = 1)
    SELECT
      CASE WHEN l.inventory_level < d.m*p.lt + p.z*d.s*SQRT(p.lt)         THEN 'INCREASE'
           WHEN l.inventory_level > d.m*p.lt*1.5 + p.z*d.s*SQRT(p.lt)     THEN 'DECREASE'
           ELSE 'HOLD' END AS action,
      COUNT(*) AS n
    FROM l JOIN d USING (store_id, product_id) CROSS JOIN params p
    GROUP BY action
""").df()

store_perf = con.execute("""
    SELECT store_id,
           SUM(units_sold)                          AS units,
           SUM(units_sold * price)                  AS revenue,
           AVG(inventory_level)                     AS avg_inv
    FROM fact_inventory_daily
    GROUP BY store_id ORDER BY store_id
""").df()

# ---------------------------------------------------------------------------
# Figure layout
# ---------------------------------------------------------------------------
fig = plt.figure(figsize=(18, 14), dpi=140, facecolor=COL["bg"])
gs  = GridSpec(5, 4, figure=fig,
               height_ratios=[0.5, 1.2, 1.7, 1.7, 1.7],
               hspace=0.55, wspace=0.30,
               left=0.04, right=0.97, top=0.95, bottom=0.04)

# ---- Title -----------------------------------------------------------------
fig.text(0.04, 0.975, "Urban Retail Co. — Inventory Analytics Dashboard",
         fontsize=20, fontweight="bold", color=COL["navy"])
fig.text(0.04, 0.953, "SQL-driven KPI report  ·  Source: inventory_forecasting.csv  ·  Period: 2022-01-01 → 2023-12-31  ·  Last-90-day KPIs",
         fontsize=10.5, color=COL["grey"], style="italic")

# ---- KPI cards -------------------------------------------------------------
def kpi_card(ax, label, value, sub, color):
    ax.set_facecolor(COL["card"])
    for s in ax.spines.values():
        s.set_visible(True)
        s.set_edgecolor("#E1E4E8")
        s.set_linewidth(1)
    # Color stripe
    ax.add_patch(plt.Rectangle((0, 0.92), 1, 0.08, transform=ax.transAxes,
                               facecolor=color, edgecolor="none"))
    ax.text(0.05, 0.65, value, transform=ax.transAxes,
            fontsize=22, fontweight="bold", color=COL["navy"])
    ax.text(0.05, 0.35, label, transform=ax.transAxes,
            fontsize=10.5, color=COL["grey"], fontweight="bold")
    ax.text(0.05, 0.12, sub, transform=ax.transAxes,
            fontsize=8.5, color="#999", style="italic")
    ax.set_xticks([]); ax.set_yticks([])

def fmt_money(v):
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.1f}M"
    if v >= 1e3: return f"${v/1e3:.1f}K"
    return f"${v:,.0f}"

def fmt_num(v):
    if v >= 1e9: return f"{v/1e9:.2f}B"
    if v >= 1e6: return f"{v/1e6:.2f}M"
    if v >= 1e3: return f"{v/1e3:.1f}K"
    return f"{v:,.0f}"

ax_k1 = fig.add_subplot(gs[1, 0]); kpi_card(ax_k1, "Total Revenue (90d)",   fmt_money(kpi["revenue"]),    "Gross, pre-discount",        COL["navy"])
ax_k2 = fig.add_subplot(gs[1, 1]); kpi_card(ax_k2, "Units Sold (90d)",      fmt_num(kpi["units_sold"]),   "Across 5 stores · 30 SKUs",  COL["teal"])
ax_k3 = fig.add_subplot(gs[1, 2]); kpi_card(ax_k3, "Avg Stock Level",       f"{kpi['avg_stock']:.0f}",    "Units per (store, product)", COL["amber"])
ax_k4 = fig.add_subplot(gs[1, 3]); kpi_card(ax_k4, "Stockout Rate",         f"{kpi['stockout_pct']:.2f}%","Zero-inventory observations", COL["green"] if kpi["stockout_pct"]==0 else COL["red"])

# ---- Chart 1: Daily sales trend with moving averages -----------------------
ax1 = fig.add_subplot(gs[2, :2])
ax1.plot(daily["txn_date"], daily["units_sold"], color=COL["teal"], linewidth=0.7, alpha=0.4, label="Daily units sold")
ax1.plot(daily["txn_date"], daily["ma_7"],       color=COL["amber"], linewidth=1.6, label="7-day MA")
ax1.plot(daily["txn_date"], daily["ma_30"],      color=COL["navy"],  linewidth=2.0, label="30-day MA")
ax1.set_title("Daily Units Sold — Trend & Moving Averages")
ax1.set_xlabel(""); ax1.set_ylabel("Units sold (all SKUs)")
ax1.legend(loc="upper left", frameon=False, fontsize=8.5)
ax1.grid(True, axis="y", alpha=0.3)
ax1.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, p: f"{int(x):,}"))

# ---- Chart 2: ABC analysis – top 15 products -------------------------------
ax2 = fig.add_subplot(gs[2, 2:])
top = abc.head(15).iloc[::-1]
colors = top["abc"].map({"A": COL["green"], "B": COL["amber"], "C": COL["red"]})
bars = ax2.barh(top["product_id"], top["revenue"]/1e6, color=colors, edgecolor="white")
ax2.set_title("Top 15 Products by Revenue — ABC Classification")
ax2.set_xlabel("Revenue ($M)")
for bar, cls in zip(bars, top["abc"]):
    w = bar.get_width()
    ax2.text(w + 0.2, bar.get_y() + bar.get_height()/2, cls,
             va="center", fontsize=8, color=COL["navy"], fontweight="bold")
ax2.grid(True, axis="x", alpha=0.3)

# Legend for ABC
from matplotlib.patches import Patch
legend_elems = [
    Patch(facecolor=COL["green"], label="A — Fast (≤70% cum.)"),
    Patch(facecolor=COL["amber"], label="B — Medium (70–90%)"),
    Patch(facecolor=COL["red"],   label="C — Slow (>90%)"),
]
ax2.legend(handles=legend_elems, loc="lower right", fontsize=7.5, frameon=False)

# ---- Chart 3: Category revenue mix -----------------------------------------
ax3 = fig.add_subplot(gs[3, 0])
palette = [COL["navy"], COL["teal"], COL["amber"], COL["green"], COL["red"]]
ax3.pie(cat_sales["revenue"], labels=cat_sales["category"], autopct="%1.1f%%",
        colors=palette[:len(cat_sales)], startangle=90,
        wedgeprops=dict(edgecolor="white", linewidth=2),
        textprops=dict(fontsize=9, color=COL["navy"]))
ax3.set_title("Revenue Mix by Category")

# ---- Chart 4: Avg sales heatmap (category × region) ------------------------
ax4 = fig.add_subplot(gs[3, 1])
im = ax4.imshow(heat_pivot.values, aspect="auto", cmap="YlGnBu")
ax4.set_xticks(range(len(heat_pivot.columns))); ax4.set_xticklabels(heat_pivot.columns, fontsize=8)
ax4.set_yticks(range(len(heat_pivot.index)));   ax4.set_yticklabels(heat_pivot.index,   fontsize=8)
ax4.set_title("Avg Daily Sales by Category × Region")
for i in range(heat_pivot.shape[0]):
    for j in range(heat_pivot.shape[1]):
        ax4.text(j, i, f"{heat_pivot.values[i,j]:.0f}",
                 ha="center", va="center", fontsize=8,
                 color="white" if heat_pivot.values[i,j] > heat_pivot.values.mean() else COL["navy"])
plt.colorbar(im, ax=ax4, shrink=0.7).ax.tick_params(labelsize=7)

# ---- Chart 5: Inventory level distribution ---------------------------------
ax5 = fig.add_subplot(gs[3, 2])
ax5.hist(inv_dist["inventory_level"], bins=40, color=COL["teal"],
         edgecolor="white", alpha=0.9)
ax5.axvline(inv_dist["inventory_level"].mean(), color=COL["red"],
            linestyle="--", linewidth=1.5,
            label=f"Mean = {inv_dist['inventory_level'].mean():.0f}")
ax5.axvline(inv_dist["inventory_level"].median(), color=COL["amber"],
            linestyle="--", linewidth=1.5,
            label=f"Median = {inv_dist['inventory_level'].median():.0f}")
ax5.set_title("Inventory Level Distribution")
ax5.set_xlabel("Units on hand"); ax5.set_ylabel("Frequency")
ax5.legend(fontsize=8, frameon=False)
ax5.grid(True, axis="y", alpha=0.3)

# ---- Chart 6: Stock adjustment actions -------------------------------------
ax6 = fig.add_subplot(gs[3, 3])
action_order = {"INCREASE": COL["red"], "HOLD": COL["green"], "DECREASE": COL["amber"]}
action = action.set_index("action").reindex(["INCREASE","HOLD","DECREASE"]).fillna(0).reset_index()
bars = ax6.bar(action["action"], action["n"],
               color=[action_order[a] for a in action["action"]], edgecolor="white", width=0.6)
ax6.set_title("Recommended Stock Actions")
ax6.set_ylabel("# of (store, product) pairs")
for bar, v in zip(bars, action["n"]):
    ax6.text(bar.get_x()+bar.get_width()/2, bar.get_height()+1,
             f"{int(v)}", ha="center", fontsize=9, fontweight="bold", color=COL["navy"])
ax6.grid(True, axis="y", alpha=0.3)

# ---- Chart 7: Forecast accuracy (sold vs forecast) -------------------------
ax7 = fig.add_subplot(gs[4, :2])
ax7.plot(fcast["txn_date"], fcast["sold"],     color=COL["teal"], linewidth=1.0, alpha=0.7, label="Actual sold")
ax7.plot(fcast["txn_date"], fcast["forecast"], color=COL["red"],  linewidth=1.0, alpha=0.7, label="Forecast")
ax7.set_title("Forecast vs Actual — Daily Aggregate")
ax7.set_xlabel(""); ax7.set_ylabel("Units")
ax7.legend(loc="upper left", frameon=False, fontsize=8.5)
ax7.grid(True, axis="y", alpha=0.3)
ax7.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, p: f"{int(x):,}"))

# ---- Chart 8: Monthly seasonality ------------------------------------------
ax8 = fig.add_subplot(gs[4, 2])
bars = ax8.bar(monthly["m_label"], monthly["units"]/1e3,
               color=COL["blue"], edgecolor="white")
ax8.set_title("Sales by Month (2-yr aggregate)")
ax8.set_ylabel("Units sold (thousands)")
plt.setp(ax8.get_xticklabels(), rotation=45, ha="right")
ax8.grid(True, axis="y", alpha=0.3)

# ---- Chart 9: Store performance --------------------------------------------
ax9 = fig.add_subplot(gs[4, 3])
xp = np.arange(len(store_perf))
ax9.bar(xp - 0.18, store_perf["revenue"]/1e6, width=0.35,
        color=COL["navy"], label="Revenue ($M)", edgecolor="white")
ax9b = ax9.twinx()
ax9b.bar(xp + 0.18, store_perf["avg_inv"], width=0.35,
         color=COL["amber"], label="Avg Inventory", edgecolor="white")
ax9.set_xticks(xp); ax9.set_xticklabels(store_perf["store_id"])
ax9.set_title("Store Performance")
ax9.set_ylabel("Revenue ($M)",  color=COL["navy"])
ax9b.set_ylabel("Avg Inventory", color=COL["amber"])
ax9.tick_params(axis="y", labelcolor=COL["navy"])
ax9b.tick_params(axis="y", labelcolor=COL["amber"])
ax9b.spines["top"].set_visible(False)
ax9.grid(True, axis="y", alpha=0.3)

# ---- Footer ----------------------------------------------------------------
fig.text(0.5, 0.005,
         "Generated by SQL queries against fact_inventory_daily  ·  Star schema  ·  Reorder logic: ROP = µ·LT + z·σ·√LT  (LT=7d, z=1.65)",
         ha="center", fontsize=8, color=COL["grey"], style="italic")

plt.savefig(OUT, dpi=140, bbox_inches="tight", facecolor=COL["bg"])
plt.close()
con.close()
print(f"Dashboard saved -> {OUT}")
