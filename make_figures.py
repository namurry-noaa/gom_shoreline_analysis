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
import contextily as cx
from sqlalchemy import create_engine, text

ENGINE = create_engine("postgresql+psycopg2://mv57@localhost:5432/gis_dev")
OUT = os.path.join(os.path.dirname(__file__), "figures")
os.makedirs(OUT, exist_ok=True)

# Dark style to match the review work; white would also be fine for print.
plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "font.size": 11, "axes.titlesize": 13, "axes.titleweight": "bold",
    "savefig.dpi": 200, "savefig.bbox": "tight", "figure.constrained_layout.use": True,
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
    fig, ax = plt.subplots(figsize=(8, 4.4))
    names = list(rows); vals = list(rows.values())
    colors = ["#28dc5a", "#b478ff", "#ff8c00", "#888888"]
    ax.barh(names, vals, color=colors)
    for i, v in enumerate(vals):
        ax.text(v + 8, i, f"{v}", va="center", fontweight="bold")
    ax.set_xlim(0, max(vals) * 1.15)
    ax.set_xlabel("Vertices per kilometer")
    ax.set_title("Shoreline detail: NGS tiles vs. Notre Dame reference")
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
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 5.5))
    green.plot(ax=a1, color="#28dc5a", lw=0.6)
    orange.plot(ax=a1, color="#ff8c00", lw=0.6)
    a1.set_title("Before: green + orange overlaid\n(1,076 crossings — tangle)")
    dr.plot(ax=a2, color="#ff3ce6", lw=0.7)
    a2.set_title("After: detail-based reconciliation\n(single coherent shoreline)")
    for a in (a1, a2):
        a.set_xlim(cx-r, cx+r); a.set_ylim(cy-r, cy+r)
        a.set_xticks([]); a.set_yticks([]); a.set_aspect("equal")
    fig.suptitle("Fig 3. Marsh tangle resolved by detail-based authority", fontweight="bold", fontsize=14)
    fig.savefig(f"{OUT}/fig3_marsh_tangle.png"); plt.close(fig)
    print("fig3 done")

# --- Fig 4: seam overlap before/after (green vs orange clip) ---------------
def fig_seam():
    cx, cy, r = 820409.79, 3259714.56, 250
    env = f"ST_MakeEnvelope({cx-r},{cy-r},{cx+r},{cy+r},6344)"
    green = gdf(f"SELECT geom_m AS geom FROM gom_shoreline.green_la2206_utm16 WHERE geom_m && {env}")
    o_orig = gdf(f"SELECT geom_m AS geom FROM gom_shoreline.orange_la2205 WHERE geom_m && {env}")
    o_clip = gdf(f"SELECT geom FROM gom_shoreline.orange_clean_full WHERE geom && {env}")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 5.5))
    green.plot(ax=a1, color="#28dc5a", lw=0.6); o_orig.plot(ax=a1, color="#ff8c00", lw=0.6)
    a1.set_title("Before: orange overlaps green")
    green.plot(ax=a2, color="#28dc5a", lw=0.6); o_clip.plot(ax=a2, color="#ff8c00", lw=0.6)
    a2.set_title("After: overlap removed (0.0 m residual)")
    for a in (a1, a2):
        a.set_xlim(cx-r, cx+r); a.set_ylim(cy-r, cy+r)
        a.set_xticks([]); a.set_yticks([]); a.set_aspect("equal")
    fig.suptitle("Fig 4. Buffer-difference overlap removal at a seam", fontweight="bold", fontsize=14)
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
    ax.set_yscale("log")
    for i, v in enumerate(ys):
        ax.text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=9)
    ax.set_xlabel("Polygon area (m²)"); ax.set_ylabel("Count (log scale)")
    ax.set_title("Fig 5. Polygon product size distribution (88,807 polygons)")
    fig.savefig(f"{OUT}/fig5_polygon_distribution.png"); plt.close(fig)
    print("fig5 done")

# --- Fig 6: authority map on ESRI World Imagery basemap --------------------
def fig_authority():
    # Pull cells + overlap-zone shoreline; reproject to Web Mercator (3857) for tiles.
    cells = gdf("SELECT winner, cell AS geom FROM gom_shoreline.authority_map WHERE winner IN ('green','orange')").set_crs(6344).to_crs(3857)
    n_g = int((cells.winner == "green").sum())
    n_o = int((cells.winner == "orange").sum())
    minx, miny, maxx, maxy = cells.total_bounds

    # Context extent: green+orange+purple (exclude red - far west, no context value here)
    ctx_zone = gdf("""SELECT geom FROM gom_shoreline.line_network_dr
                      WHERE src NOT IN ('red')""").set_crs(6344).to_crs(3857)
    cminx, cminy, cmaxx, cmaxy = ctx_zone.total_bounds

    ESRI = cx.providers.Esri.WorldImagery
    from matplotlib.patches import Patch, Rectangle

    # Overlap zone is a tall-thin strip (aspect ~0.10). Layout: LEFT = large regional
    # context (square-ish, fills most width), RIGHT = the narrow tall detail strip.
    fig = plt.figure(figsize=(10.5, 9.5))
    fig.set_layout_engine("none")
    # Context hugs the LEFT margin and enlarges; detail strip hugs the RIGHT margin;
    # ~3/8" gap between them. Figure is 10.5" wide -> 0.375"/10.5 ~= 0.036 fraction gap.
    # detail strip: right edge at ~0.985, width 0.15 -> left edge 0.835.
    # context: left edge ~0.015, right edge = 0.835 - 0.036 gap = ~0.799 -> width 0.784.
    axc = fig.add_axes([0.015, 0.11, 0.784, 0.85])   # context, hugging left
    axd = fig.add_axes([0.835, 0.11, 0.150, 0.85])   # detail strip, hugging right

    # LEFT: regional context on satellite, overlap zone boxed, + reconciled shoreline
    padc = 6000
    cxlo, cxhi = cminx - padc, cmaxx + padc
    cylo, cyhi = cminy - padc, cmaxy + padc
    ctx_zone.cx[cxlo:cxhi, cylo:cyhi].plot(ax=axc, color="#00e5ff", lw=0.3, alpha=0.85)
    axc.set_xlim(cxlo, cxhi); axc.set_ylim(cylo, cyhi)
    cx.add_basemap(axc, source=ESRI, crs=3857, attribution=False)
    # red overlap-zone box drawn LAST + high zorder so it sits on top of shoreline/basemap
    axc.add_patch(Rectangle((minx, miny), maxx - minx, maxy - miny,
                            fill=False, edgecolor="red", lw=2.4, zorder=10))
    axc.set_xticks([]); axc.set_yticks([])
    axc.set_title("Regional context (LA coast): reconciled shoreline (cyan); red box = overlap zone", fontsize=11)
    axc.legend(handles=[plt.Line2D([0],[0], color="#00e5ff", lw=1.5, label="reconciled shoreline"),
                        Patch(facecolor="none", edgecolor="red", label="overlap zone")],
               loc="lower left", framealpha=0.9, fontsize=9)

    # RIGHT: the tall overlap-zone strip fills the narrow column vertically
    padd = 600
    axd.set_xlim(minx - padd, maxx + padd); axd.set_ylim(miny - padd, maxy + padd)
    cells[cells.winner == "green"].plot(ax=axd, color="#00ff66", alpha=0.62, edgecolor="#004d1f", lw=0.5)
    cells[cells.winner == "orange"].plot(ax=axd, color="#ff9500", alpha=0.62, edgecolor="#663500", lw=0.5)
    cx.add_basemap(axd, source=ESRI, crs=3857, attribution=False)
    axd.set_xticks([]); axd.set_yticks([])
    axd.set_title("Overlap zone\ndetail (500 m)", fontsize=10)

    # green/orange legend BELOW the detail strip (above the basemap credit), under axd
    fig.legend(handles=[Patch(facecolor="#00ff66", alpha=0.75, edgecolor="#004d1f", label=f"green denser ({n_g})"),
                        Patch(facecolor="#ff9500", alpha=0.75, edgecolor="#663500", label=f"orange denser ({n_o})")],
               loc="lower center", bbox_to_anchor=(0.91, 0.045), framealpha=0.9, fontsize=9, ncol=1)

    # attribution/CRS note at very bottom, clear of both panels
    fig.text(0.5, 0.015, "Basemap: Esri World Imagery (Esri, Maxar, Earthstar Geographics). "
                         "Data transformed to Web Mercator (EPSG:3857) for basemap display.",
             ha="center", fontsize=7.5, color="#333")
    fig.savefig(f"{OUT}/fig6_authority_map.png"); plt.close(fig)
    print("fig6 done")

# --- Recovery: size distribution of large unclosed features -----------------
def fig_recovery_distribution():
    rows = q("""
      SELECT CASE WHEN closed_area_m2 < 100 THEN '<100 m2'
                  WHEN closed_area_m2 < 1000 THEN '100-1k'
                  WHEN closed_area_m2 < 10000 THEN '1k-10k'
                  WHEN closed_area_m2 < 100000 THEN '10k-100k'
                  WHEN closed_area_m2 < 1000000 THEN '0.1-1 km2'
                  ELSE '>1 km2' END b, count(*) n
      FROM gom_shoreline.dangle_clusters2 WHERE closed_area_m2 > 0 GROUP BY 1;""")
    order = ['<100 m2','100-1k','1k-10k','10k-100k','0.1-1 km2','>1 km2']
    d = {r[0]: r[1] for r in rows}
    ys = [d.get(k, 0) for k in order]
    colors = ["#bbbbbb","#bbbbbb","#bbbbbb","#bbbbbb","#ff8c00","#d62728"]
    fig, ax = plt.subplots(figsize=(8, 4.4))
    bars = ax.bar(order, ys, color=colors, edgecolor="#333")
    ax.set_yscale("log")
    for i, v in enumerate(ys):
        ax.text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=9)
    ax.set_xlabel("Feature size (area enclosed if the open feature were closed)")
    ax.set_ylabel("Count of features (log scale)")
    ax.set_title("Unclosed features by size: candidates for polygon recovery\n"
                 "(orange + red = 70 large features >= 0.1 km2)")
    fig.savefig(f"{OUT}/fig_recovery_distribution.png"); plt.close(fig)
    print("fig_recovery_distribution done")

# --- Recovery: overview map of the 70 large candidates on ESRI imagery -------
def fig_recovery_overview():
    cand = gdf("""SELECT closed_area_km2, lines_geom AS geom
                  FROM gom_shoreline.recovery_candidates""").set_crs(6344).to_crs(3857)
    areas = gdf("""SELECT closed_area_km2, area_geom AS geom
                   FROM gom_shoreline.recovery_candidates
                   WHERE area_geom IS NOT NULL AND NOT ST_IsEmpty(area_geom)""").set_crs(6344).to_crs(3857)
    # context: the reconciled shoreline (exclude red) for orientation
    ctx = gdf("SELECT geom FROM gom_shoreline.line_network_dr WHERE src NOT IN ('red')").set_crs(6344).to_crs(3857)
    minx, miny, maxx, maxy = cand.total_bounds
    padx = (maxx - minx) * 0.08 + 2000
    pady = (maxy - miny) * 0.08 + 2000

    ESRI = cx.providers.Esri.WorldImagery
    fig, ax = plt.subplots(figsize=(10.5, 8.5))
    ctx.cx[minx-padx:maxx+padx, miny-pady:maxy+pady].plot(ax=ax, color="#00e5ff", lw=0.15, alpha=0.5)
    # areas that would be recovered, colored by size
    areas.plot(ax=ax, column="closed_area_km2", cmap="autumn_r", alpha=0.75,
               edgecolor="red", lw=0.4, legend=True,
               legend_kwds={"label": "Feature size if recovered (km²)", "shrink": 0.5})
    ax.set_xlim(minx-padx, maxx+padx); ax.set_ylim(miny-pady, maxy+pady)
    cx.add_basemap(ax, source=ESRI, crs=3857, attribution=False)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title("Large unclosed features - polygon-recovery candidates\n"
                 "(70 features >= 0.1 km²; cyan = reconciled shoreline for context)", fontsize=11)
    fig.text(0.5, 0.02, "Basemap: Esri World Imagery (Esri, Maxar, Earthstar Geographics). "
                        "Data transformed to Web Mercator (EPSG:3857) for basemap display.",
             ha="center", fontsize=7.5, color="#333")
    fig.savefig(f"{OUT}/fig_recovery_overview.png"); plt.close(fig)
    print("fig_recovery_overview done")

# --- Recovered vs original polygons (RC2) -----------------------------------
def fig_recovered_vs_original():
    orig = gdf("SELECT geom FROM gom_shoreline.poly_dr_recovered WHERE src='original'").set_crs(6344).to_crs(3857)
    rec = gdf("SELECT geom FROM gom_shoreline.poly_dr_recovered WHERE src='recovered'").set_crs(6344).to_crs(3857)
    minx, miny, maxx, maxy = rec.total_bounds  # frame on where recoveries happened
    padx = (maxx - minx) * 0.06 + 2000
    pady = (maxy - miny) * 0.06 + 2000
    ESRI = cx.providers.Esri.WorldImagery
    from matplotlib.patches import Patch
    fig, ax = plt.subplots(figsize=(10.5, 8.5))
    orig.cx[minx-padx:maxx+padx, miny-pady:maxy+pady].plot(ax=ax, color="#ffd400", alpha=0.45, edgecolor="#b39600", lw=0.2)
    rec.plot(ax=ax, color="#ff0033", alpha=0.85, edgecolor="#a00020", lw=0.4)
    ax.set_xlim(minx-padx, maxx+padx); ax.set_ylim(miny-pady, maxy+pady)
    cx.add_basemap(ax, source=ESRI, crs=3857, attribution=False)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title("Polygon recovery (RC2): recovered features (red) vs. existing polygons (yellow)\n"
                 "2,664 recovered polygons (+339 km²); total 91,471", fontsize=11)
    ax.legend(handles=[Patch(facecolor="#ff0033", alpha=0.85, label="recovered (RC2)"),
                       Patch(facecolor="#ffd400", alpha=0.6, label="existing polygons")],
              loc="lower left", framealpha=0.9, fontsize=9)
    fig.text(0.5, 0.02, "Basemap: Esri World Imagery (Esri, Maxar, Earthstar Geographics). "
                        "Data transformed to Web Mercator (EPSG:3857) for basemap display.",
             ha="center", fontsize=7.5, color="#333")
    fig.savefig(f"{OUT}/fig_recovered_vs_original.png"); plt.close(fig)
    print("fig_recovered_vs_original done")

if __name__ == "__main__":
    fig_detail(); fig_fragments(); fig_tangle(); fig_seam(); fig_polys(); fig_authority()
    fig_recovery_distribution()
    print("ALL FIGURES DONE ->", OUT)
