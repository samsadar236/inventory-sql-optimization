"""
make_erd.py
-----------
Renders the star-schema ER diagram as a PNG. Pure matplotlib so we don't
depend on Graphviz being installed on whatever box the user clones to.
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mp
from matplotlib.patches import FancyArrowPatch
from pathlib import Path

OUT = Path("/home/claude/urban_retail_sql/diagrams/erd.png")

# Color palette — restrained, print-friendly
HEADER_FACT = "#1F3A5F"
HEADER_DIM  = "#4A6FA5"
BODY        = "#FFFFFF"
PK          = "#FFD166"
FK          = "#06AED5"
BORDER      = "#1F3A5F"
EDGE        = "#444444"

def draw_table(ax, x, y, w, title, fields, is_fact=False):
    """Renders a table card. `fields` is a list of (name, type, role)."""
    header_h = 0.42
    row_h    = 0.30
    h        = header_h + row_h * len(fields)

    # Body
    ax.add_patch(mp.FancyBboxPatch((x, y - h), w, h,
                                   boxstyle="round,pad=0.02,rounding_size=0.05",
                                   facecolor=BODY, edgecolor=BORDER, linewidth=1.4))
    # Header strip
    ax.add_patch(mp.Rectangle((x, y - header_h), w, header_h,
                              facecolor=HEADER_FACT if is_fact else HEADER_DIM,
                              edgecolor=BORDER, linewidth=1.4))
    ax.text(x + w / 2, y - header_h / 2, title,
            ha="center", va="center", color="white", fontsize=11,
            fontweight="bold", family="DejaVu Sans")

    # Rows
    for i, (name, dtype, role) in enumerate(fields):
        ry = y - header_h - row_h * (i + 1)
        # Row stripe
        if i % 2 == 1:
            ax.add_patch(mp.Rectangle((x, ry), w, row_h,
                                      facecolor="#F4F4F4", edgecolor="none"))
        # Role badge
        if role == "PK":
            ax.add_patch(mp.Rectangle((x + 0.08, ry + 0.05), 0.30, row_h - 0.10,
                                      facecolor=PK, edgecolor=BORDER, linewidth=0.7))
            ax.text(x + 0.23, ry + row_h / 2, "PK", ha="center", va="center",
                    fontsize=7, fontweight="bold", color="#222")
        elif role == "FK":
            ax.add_patch(mp.Rectangle((x + 0.08, ry + 0.05), 0.30, row_h - 0.10,
                                      facecolor=FK, edgecolor=BORDER, linewidth=0.7))
            ax.text(x + 0.23, ry + row_h / 2, "FK", ha="center", va="center",
                    fontsize=7, fontweight="bold", color="white")

        ax.text(x + 0.46, ry + row_h / 2, name,
                ha="left", va="center", fontsize=8.5, family="DejaVu Sans Mono",
                fontweight="bold" if role in ("PK", "FK") else "normal")
        ax.text(x + w - 0.10, ry + row_h / 2, dtype,
                ha="right", va="center", fontsize=7.5, color="#666",
                family="DejaVu Sans Mono", style="italic")
    return (x, y, w, h)

def connect(ax, src, dst, label=""):
    """Draw a foreign-key relationship line between two table cards."""
    (sx, sy, sw, sh) = src
    (dx, dy, dw, dh) = dst
    src_mid = (sx + sw / 2, sy - sh / 2)
    dst_mid = (dx + dw / 2, dy - dh / 2)
    # Pick connection sides
    if dst_mid[0] > src_mid[0]:
        sp = (sx + sw, src_mid[1])
        dp = (dx, dst_mid[1])
    else:
        sp = (sx, src_mid[1])
        dp = (dx + dw, dst_mid[1])
    arr = FancyArrowPatch(sp, dp,
                          arrowstyle="-|>", mutation_scale=14,
                          color=EDGE, linewidth=1.3,
                          connectionstyle="arc3,rad=0.0")
    ax.add_patch(arr)
    mx, my = (sp[0] + dp[0]) / 2, (sp[1] + dp[1]) / 2
    if label:
        ax.text(mx, my + 0.10, label, ha="center", va="bottom",
                fontsize=7.5, color="#333", style="italic",
                bbox=dict(facecolor="white", edgecolor="none", pad=1.5))


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(16, 10), dpi=160)
ax.set_xlim(0, 16)
ax.set_ylim(0, 10)
ax.set_aspect("equal")
ax.axis("off")

# Title
ax.text(8, 9.6, "Urban Retail Co. — Inventory Analytics  |  Star Schema",
        ha="center", va="center", fontsize=15, fontweight="bold", color="#1F3A5F")
ax.text(8, 9.2, "Fact: fact_inventory_daily   ·   Dimensions: dim_date, dim_product, dim_store, dim_region",
        ha="center", va="center", fontsize=9.5, color="#555", style="italic")

# Fact table — centre
fact_fields = [
    ("txn_date",          "DATE",        "PK"),
    ("store_id",          "VARCHAR(10)", "PK"),
    ("product_id",        "VARCHAR(10)", "PK"),
    ("region_id",         "INTEGER",     "FK"),
    ("inventory_level",   "INTEGER",     ""),
    ("units_sold",        "INTEGER",     ""),
    ("units_ordered",     "INTEGER",     ""),
    ("demand_forecast",   "NUMERIC",     ""),
    ("price",             "NUMERIC",     ""),
    ("discount_pct",      "INTEGER",     ""),
    ("competitor_price",  "NUMERIC",     ""),
    ("weather_condition", "VARCHAR(20)", ""),
    ("holiday_promotion", "SMALLINT",    ""),
]
fact = draw_table(ax, 6.0, 8.0, 4.0, "fact_inventory_daily", fact_fields, is_fact=True)

# dim_date — top-left
date_fields = [
    ("date_key",      "DATE",         "PK"),
    ("day",           "SMALLINT",     ""),
    ("month",         "SMALLINT",     ""),
    ("month_name",    "VARCHAR(10)",  ""),
    ("quarter",       "SMALLINT",     ""),
    ("year",          "SMALLINT",     ""),
    ("day_of_week",   "SMALLINT",     ""),
    ("day_name",      "VARCHAR(10)",  ""),
    ("is_weekend",    "BOOLEAN",      ""),
    ("season",        "VARCHAR(20)",  ""),
    ("week_of_year",  "SMALLINT",     ""),
]
date = draw_table(ax, 0.5, 8.0, 3.3, "dim_date", date_fields)

# dim_product — top-right
prod_fields = [
    ("product_id", "VARCHAR(10)", "PK"),
    ("category",   "VARCHAR(50)", ""),
]
prod = draw_table(ax, 12.2, 8.0, 3.3, "dim_product", prod_fields)

# dim_store — bottom-left
store_fields = [("store_id", "VARCHAR(10)", "PK")]
store = draw_table(ax, 1.5, 3.5, 3.0, "dim_store", store_fields)

# dim_region — bottom-right
region_fields = [
    ("region_id",   "SERIAL",      "PK"),
    ("region_name", "VARCHAR(20)", ""),
]
region = draw_table(ax, 11.5, 3.5, 3.0, "dim_region", region_fields)

# Relationships
connect(ax, fact, date,   "1:N  date_key → txn_date")
connect(ax, fact, prod,   "1:N  product_id")
connect(ax, fact, store,  "1:N  store_id")
connect(ax, fact, region, "1:N  region_id")

# Legend
ax.add_patch(mp.Rectangle((0.5, 0.5), 0.4, 0.3, facecolor=PK, edgecolor=BORDER))
ax.text(1.0, 0.65, "Primary Key",     fontsize=9, va="center")
ax.add_patch(mp.Rectangle((2.6, 0.5), 0.4, 0.3, facecolor=FK, edgecolor=BORDER))
ax.text(3.1, 0.65, "Foreign Key",     fontsize=9, va="center")
ax.add_patch(mp.Rectangle((4.8, 0.5), 0.4, 0.3, facecolor=HEADER_FACT, edgecolor=BORDER))
ax.text(5.3, 0.65, "Fact Table",      fontsize=9, va="center")
ax.add_patch(mp.Rectangle((6.7, 0.5), 0.4, 0.3, facecolor=HEADER_DIM,  edgecolor=BORDER))
ax.text(7.2, 0.65, "Dimension Table", fontsize=9, va="center")
ax.text(15.5, 0.65, "Grain: (txn_date, store_id, product_id)", fontsize=8.5,
        va="center", ha="right", style="italic", color="#555")

plt.tight_layout()
plt.savefig(OUT, dpi=160, bbox_inches="tight", facecolor="white")
plt.close()
print(f"ERD saved -> {OUT}")
