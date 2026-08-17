# gom_shoreline_analysis

A reproducible **PostGIS** workflow for reconciling multiple overlapping,
high-resolution shoreline datasets into a single clean **line** layer and a
companion closed-**polygon** layer suitable for hydrodynamic mesh generation
(SMS / ADCIRC), such as the shoreline basis for VDatum tidal-datum grids.

## The problem this solves

When adjacent regions are covered by separately-digitized shoreline datasets,
their overlap strips disagree: the *same* physical feature (island, spit, marsh)
is traced twice and the two tracings do not coincide. Overlapping, non-coincident
linework breaks mesh generators. This workflow removes that redundant overlap,
preserves the finest available detail from each source, and derives closed
polygons — **rule-based geoprocessing, no smoothing, thinning, or fabrication of
shoreline.**

## Approach (in brief)

1. **Detail assessment** — measure vertex density per source.
2. **Detail-based authority** — in each grid cell, the *denser* dataset wins.
3. **Overlap removal** — buffer-difference removes duplicate co-tracing linework.
4. **Fragment cleanup** — remove clip debris (short *and* near the reference).
5. **Polygonize** — node + polygonize the reconciled network; remove artifact
   slivers and features below a minimum size.

The method was reached by ruling out a concave-hull approach on measured evidence
(it always leaves residual overlap). See the methods document for full rationale,
figures, validation, and contingencies.

## Contents

| File | Description |
|---|---|
| `GoM_Shoreline_Analysis_Methods.md` | Full methods & results report (with figures) |
| `gom_shoreline_pipeline.sql` | Reproducible PostGIS pipeline: functions + driver templates |
| `make_figures.py` | Regenerates the report figures directly from the PostGIS database |
| `figures/` | Figures used in the methods document |

## Requirements

- PostgreSQL + PostGIS (developed on PostgreSQL 17 / PostGIS 3.6)
- GDAL/OGR (`ogr2ogr`, `shp2pgsql`) for load/export
- Python (for figures): `geopandas`, `matplotlib`, `psycopg2`, `sqlalchemy`

## Usage

Load your shoreline shapefiles into a PostGIS schema, add a metric working
geometry column, then run the pipeline. Parameters (overlap buffer, cell size,
minimum feature size, CRS) are configurable — see the CONFIG and DRIVER sections
in `gom_shoreline_pipeline.sql` and the methods document for guidance.

## Notes

- **Code and methodology only** — input shoreline datasets and large output
  GeoPackages are not included here (data provenance/licensing is separate from
  this workflow).
- Distances are handled in a **metric CRS** so all parameters (e.g. "5 m buffer")
  are literal meters; deliverables are reprojected to the target CRS on export.
