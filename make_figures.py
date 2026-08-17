#!/usr/bin/env python
"""
Generate figures for the GoM Shoreline Reconciliation methods document.
Pulls geometry/stats directly from PostGIS (gis_dev, schema gom_shoreline),
renders publication figures to ./figures/.

Env: conda 'gom_shoreline'.  Run: python make_figures.py
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import geopandas as gpd
from sqlalchemy import create_engine, text

ENGINE = create_engine("postgresql+psycopg2://mv57@localhost:5432/gis_dev")
OUT = os.path.join(os.path.dirname(__file__), "figures")
os.makedirs(OUT, exist_ok=True)

# Dark style to match the review work; white would also be fine for print.
plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "font.size": 11, "axes.titlesize": 13, "axes.titleweight": "bold",
    "savefig.dpi": 150, "savefig.bbox": "tight",
})

def gdf(sql):
    return gpd.read_postgis(sql, ENGINE, geom_col="geom")

def q(sql):
    with ENGINE.connect() as c:
        return c.execute(text(sql)).fetchall()

# --- Fig 1: detail comparison (vertices/km) -------------------------------
def fig_detail():
    rows = {
        "Green (NGS)": 710, "Purple (NGS)": 542, "Orange (NGS)": 493,
        "MS_TX (Notre Dame)": 111,
    }
    fig, ax = plt.subplots(figsize=(7, 4))
    names = list(rows); vals = list(rows.values())
    colors = ["#28dc5a", "#b478ff", "#ff8c00", "#888888"]
    ax.barh(names, vals, color=colors)
    for i, v in enumerate(vals):
        ax.text(v + 8, i, f"{v}", va="center", fontweight="bold")
    ax.set_xlabel("Vertices per kilometer")
    ax.set_title("Fig 1. Shoreline detail: NGS tiles vs. Notre Dame reference")
    ax.invert_yaxis()
    fig.savefig(f"{OUT}/fig1_detail_comparison.png"); plt.close(fig)
    print("fig1 done")

# --- Fig 2: fragment-length histogram (clip debris) -----------------------
def fig_fragments():
    rows = q("""
      WITH parts AS (SELECT ST_Length((ST_Dump(geom)).geom) len
                     FROM gom_shoreline.orange_prox_2m)
      SELECT width_bucket(len,0,50,25) b, count(*) n
      FROM parts WHERE len<50 GROUP BY b ORDER BY b;""")
    xs = [(r[0]-0.5)*2 for r in rows]; ys = [r[1] for r in rows]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(xs, ys, width=1.8, color="#ff8c00", edgecolor="#7a4300")
    ax.axvline(5, color="red", ls="--", lw=1.5, label="5 m debris threshold region")
    ax.set_xlabel("Fragment length (m)"); ax.set_ylabel("Count of parts")
    ax.set_title("Fig 2. Clip-debris signature: thousands of sub-5 m slivers")
    ax.legend()
    fig.savefig(f"{OUT}/fig2_fragment_histogram.png"); plt.close(fig)
    print("fig2 done")

# --- Fig 3: marsh tangle before (old) vs after (detail-rule) --------------
def fig_tangle():
    # box around the circled marsh coord (6344)
    cx, cy, r = 820640.74, 3259734.82, 90
    env = f"ST_MakeEnvelope({cx-r},{cy-r},{cx+r},{cy+r},6344)"
    green = gdf(f"SELECT geom_m AS geom FROM gom_shoreline.green_la2206_utm16 WHERE geom_m && {env}")
    orange = gdf(f"SELECT geom_m AS geom FROM gom_shoreline.orange_la2205 WHERE geom_m && {env}")
    dr = gdf(f"SELECT geom FROM gom_shoreline.line_network_dr WHERE geom && {env}")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 5))
    green.plot(ax=a1, color="#28dc5a", lw=0.6)
    orange.plot(ax=a1, color="#ff8c00", lw=0.6)
    a1.set_title("Before: green + orange overlaid\n(1,076 crossings — tangle)")
    dr.plot(ax=a2, color="#ff3ce6", lw=0.7)
    a2.set_title("After: detail-based reconciliation\n(single coherent shoreline)")
    for a in (a1, a2):
        a.set_xlim(cx-r, cx+r); a.set_ylim(cy-r, cy+r)
        a.set_xticks([]); a.set_yticks([]); a.set_aspect("equal")
    fig.suptitle("Fig 3. Marsh tangle resolved by detail-based authority", fontweight="bold")
    fig.savefig(f"{OUT}/fig3_marsh_tangle.png"); plt.close(fig)
    print("fig3 done")

# --- Fig 4: seam overlap before/after (green vs orange clip) ---------------
def fig_seam():
    cx, cy, r = 820409.79, 3259714.56, 250
    env = f"ST_MakeEnvelope({cx-r},{cy-r},{cx+r},{cy+r},6344)"
    green = gdf(f"SELECT geom_m AS geom FROM gom_shoreline.green_la2206_utm16 WHERE geom_m && {env}")
    o_orig = gdf(f"SELECT geom_m AS geom FROM gom_shoreline.orange_la2205 WHERE geom_m && {env}")
    o_clip = gdf(f"SELECT geom FROM gom_shoreline.orange_clean_full WHERE geom && {env}")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 5))
    green.plot(ax=a1, color="#28dc5a", lw=0.6); o_orig.plot(ax=a1, color="#ff8c00", lw=0.6)
    a1.set_title("Before: orange overlaps green")
    green.plot(ax=a2, color="#28dc5a", lw=0.6); o_clip.plot(ax=a2, color="#ff8c00", lw=0.6)
    a2.set_title("After: overlap removed (0.0 m residual)")
    for a in (a1, a2):
        a.set_xlim(cx-r, cx+r); a.set_ylim(cy-r, cy+r)
        a.set_xticks([]); a.set_yticks([]); a.set_aspect("equal")
    fig.suptitle("Fig 4. Buffer-difference overlap removal at a seam", fontweight="bold")
    fig.savefig(f"{OUT}/fig4_seam_before_after.png"); plt.close(fig)
    print("fig4 done")

# --- Fig 5: polygon size distribution --------------------------------------
def fig_polys():
    rows = q("""
      SELECT CASE WHEN ST_Area(geom)<100 THEN '<100' WHEN ST_Area(geom)<1000 THEN '100-1k'
                  WHEN ST_Area(geom)<10000 THEN '1k-10k' WHEN ST_Area(geom)<100000 THEN '10k-100k'
                  WHEN ST_Area(geom)<1000000 THEN '100k-1M' ELSE '>=1M' END b, count(*) n
      FROM gom_shoreline.poly_dr GROUP BY 1;""")
    order = ['<100','100-1k','1k-10k','10k-100k','100k-1M','>=1M']
    d = {r[0]: r[1] for r in rows}
    ys = [d.get(k, 0) for k in order]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(order, ys, color="#00becd", edgecolor="#006b78")
    for i, v in enumerate(ys):
        ax.text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=9)
    ax.set_xlabel("Polygon area (m²)"); ax.set_ylabel("Count")
    ax.set_title("Fig 5. Polygon product size distribution (88,807 polygons)")
    fig.savefig(f"{OUT}/fig5_polygon_distribution.png"); plt.close(fig)
    print("fig5 done")

# --- Fig 6: authority map (who won where) ----------------------------------
def fig_authority():
    cells = gdf("SELECT winner, cell AS geom FROM gom_shoreline.authority_map WHERE winner IN ('green','orange')")
    fig, ax = plt.subplots(figsize=(8, 3.5))
    cells[cells.winner=="green"].plot(ax=ax, color="#28dc5a", alpha=0.6, edgecolor="none")
    cells[cells.winner=="orange"].plot(ax=ax, color="#ff8c00", alpha=0.6, edgecolor="none")
    ax.set_title("Fig 6. Detail-based authority map (green: 289 cells, orange: 252)")
    ax.set_xticks([]); ax.set_yticks([]); ax.set_aspect("equal")
    fig.savefig(f"{OUT}/fig6_authority_map.png"); plt.close(fig)
    print("fig6 done")

if __name__ == "__main__":
    fig_detail(); fig_fragments(); fig_tangle(); fig_seam(); fig_polys(); fig_authority()
    print("ALL FIGURES DONE ->", OUT)
