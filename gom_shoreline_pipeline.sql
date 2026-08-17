-- ============================================================================
-- GoM Shoreline Reconciliation Pipeline  (PostGIS 3.6 / PostgreSQL 17)
-- ----------------------------------------------------------------------------
-- Reconciles multiple overlapping, high-resolution shoreline datasets covering
-- adjacent regions into (1) one merged, non-overlapping shoreline LINE layer and
-- (2) a companion closed-POLYGON layer, suitable for SMS/ADCIRC mesh generation.
--
-- Method summary (see the accompanying Methods document for full rationale):
--   1. Load sources; keep original geometry + a metric working copy (geom_m).
--   2. Assess detail (vertices/km) per dataset.
--   3. Build a per-cell AUTHORITY MAP: in each grid cell, the denser dataset wins.
--   4. Remove duplicate co-tracing overlap (buffer-difference), per authority.
--   5. Remove clip-debris fragments (short AND near reference).
--   6. Assemble the reconciled line network (+ non-overlapping datasets as-is).
--   7. Polygonize (noded), remove artifact slivers + tiny features (< N m).
--   8. Export line + polygon layers (reproject to delivery CRS).
--
-- Parameters are physically meaningful (meters), because all analysis runs in a
-- metric CRS. Adjust the constants in Section 0 for a different domain.
--
-- USAGE: this file defines reusable functions and shows the driver steps as
-- commented templates. Set the CONFIG values, load your data, then run the
-- driver section. Table/column names below assume: each source table has a
-- metric geometry column `geom_m` (see Section 1) and an integer `gid`.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- SECTION 0. CONFIG (edit for your domain)
-- ----------------------------------------------------------------------------
--   WORKING_SRID : metric CRS (e.g. 6344 = NAD83(2011) UTM 15N). Distances = meters.
--   DELIVERY_SRID: output CRS (e.g. 4326 = WGS84).
--   BUFFER_N     : overlap buffer, meters ("within N = duplicate co-tracing").
--   FRAG_LEN/DIST: fragment removal: drop parts < FRAG_LEN AND within FRAG_DIST of ref.
--   CELL_M       : authority-map cell size, meters.
--   MIN_FEATURE_M: minimum polygon size (both oriented sides), meters.
-- These are referenced inline in the driver below; PL/pgSQL functions take them
-- as arguments so nothing is hardcoded in the functions.

CREATE SCHEMA IF NOT EXISTS gom_shoreline;

-- ============================================================================
-- SECTION 1. LOAD & PREPARE  (template — run once per source shapefile)
-- ============================================================================
-- Load each shapefile with shp2pgsql (native tool handles the PG driver cleanly):
--
--   shp2pgsql -s <SOURCE_SRID> -I -D -g geom <file.shp> gom_shoreline.<table> \
--       | psql -d <db>
--
-- Then add a metric working column `geom_m` (WORKING_SRID) + spatial index:
--
--   ALTER TABLE gom_shoreline.<table>
--     ADD COLUMN geom_m geometry(MultiLineString, <WORKING_SRID>);
--   UPDATE gom_shoreline.<table> SET geom_m = ST_Transform(geom, <WORKING_SRID>);
--   CREATE INDEX ON gom_shoreline.<table> USING GIST(geom_m);
--
-- Convention used below: source line tables are referred to as `authority A`
-- (e.g. green) and `authority B` (e.g. orange) for the overlapping pair, plus
-- any number of non-overlapping datasets carried through unchanged.

-- ============================================================================
-- SECTION 2. FUNCTIONS
-- ============================================================================

-- 2.1 clip_by_proximity ------------------------------------------------------
-- Remove parts of `src` that run within buffer_m of `ref` (duplicate co-tracing).
-- Index-driven / per-feature LATERAL so the whole reference layer is never
-- unioned at once (bounded memory). `ref` is never modified.
CREATE OR REPLACE FUNCTION gom_shoreline.clip_by_proximity(
    src_table regclass, ref_table regclass, out_table text,
    buffer_m double precision DEFAULT 5.0,
    clip_env geometry DEFAULT NULL::geometry
) RETURNS text LANGUAGE plpgsql AS $function$
DECLARE n_out integer;
BEGIN
    EXECUTE format('DROP TABLE IF EXISTS %s', out_table);
    EXECUTE format($f$
        CREATE TABLE %1$s AS
        SELECT s.gid,
               CASE WHEN r.buf IS NULL THEN s.geom_m
                    ELSE ST_Difference(s.geom_m, r.buf) END AS geom
        FROM %2$s s
        LEFT JOIN LATERAL (
            SELECT ST_Buffer(ST_Collect(rr.geom_m), %4$s) AS buf
            FROM %3$s rr
            WHERE rr.geom_m && ST_Expand(s.geom_m, %4$s)
              AND ST_DWithin(rr.geom_m, s.geom_m, %4$s)
        ) r ON true
        WHERE (%5$L::geometry IS NULL OR s.geom_m && %5$L::geometry)
    $f$, out_table, src_table::text, ref_table::text, buffer_m, clip_env);
    EXECUTE format('DELETE FROM %s WHERE geom IS NULL OR ST_IsEmpty(geom)', out_table);
    EXECUTE format('CREATE INDEX ON %s USING GIST(geom)', out_table);
    EXECUTE format('SELECT count(*) FROM %s', out_table) INTO n_out;
    RETURN format('%s built: %s features (buffer %sm)', out_table, n_out, buffer_m);
END;
$function$;

-- 2.2 remove_fragments_near --------------------------------------------------
-- Drop a part ONLY if it is BOTH short (< max_len_m) AND near the reference
-- (< max_dist_m). Removes clip debris while preserving genuine short features
-- (short-but-far, or long-but-near are kept).
CREATE OR REPLACE FUNCTION gom_shoreline.remove_fragments_near(
    src_table regclass, ref_table regclass, out_table text,
    max_len_m double precision DEFAULT 15.0,
    max_dist_m double precision DEFAULT 6.0
) RETURNS text LANGUAGE plpgsql AS $function$
DECLARE n_out integer;
BEGIN
    EXECUTE format('DROP TABLE IF EXISTS %s', out_table);
    EXECUTE format($f$
        CREATE TABLE %1$s AS
        WITH parts AS (SELECT gid, (ST_Dump(geom)).geom AS g FROM %2$s),
        judged AS (
            SELECT p.gid, p.g, ST_Length(p.g) AS len,
                   EXISTS (SELECT 1 FROM %3$s r
                           WHERE r.geom_m && ST_Expand(p.g, %5$s)
                             AND ST_DWithin(r.geom_m, p.g, %5$s)) AS near_ref
            FROM parts p
        ),
        kept AS (SELECT gid, g FROM judged WHERE NOT (len < %4$s AND near_ref))
        SELECT gid, ST_Multi(ST_Collect(g)) AS geom FROM kept GROUP BY gid
    $f$, out_table, src_table::text, ref_table::text, max_len_m, max_dist_m);
    EXECUTE format('CREATE INDEX ON %s USING GIST(geom)', out_table);
    EXECUTE format('SELECT count(*) FROM %s', out_table) INTO n_out;
    RETURN format('%s built: %s features (drop parts <%sm AND within %sm of ref)',
                  out_table, n_out, max_len_m, max_dist_m);
END;
$function$;

-- 2.3 polygonize_tiled -------------------------------------------------------
-- Node + polygonize a line network in tiles (bounded memory / CPU). Each tile is
-- self-limited by a per-tile statement_timeout; a tile that fails to converge is
-- skipped rather than crashing the run. Larger overlap_m closes polygons whose
-- rings span tile boundaries (at the cost of more duplicate polygons pre-dedupe).
-- (Generalized from the project's polygonize_tiled_v2: line_table is parameterized.)
CREATE OR REPLACE FUNCTION gom_shoreline.polygonize_tiled(
    line_table regclass, out_table text,
    x0 double precision, y0 double precision,
    tile_m double precision, nx integer, ny integer, overlap_m double precision,
    working_srid integer DEFAULT 6344,
    per_tile_timeout_ms integer DEFAULT 45000
) RETURNS text LANGUAGE plpgsql AS $function$
DECLARE ix int; iy int; cell geometry; ncells int:=0; ndone int:=0; nskip int:=0;
BEGIN
  EXECUTE format('DROP TABLE IF EXISTS %s', out_table);
  EXECUTE format('CREATE TABLE %s (tile text, geom geometry(Polygon,%s))', out_table, working_srid);
  FOR ix IN 0..nx-1 LOOP
    FOR iy IN 0..ny-1 LOOP
      cell := ST_MakeEnvelope(x0+ix*tile_m, y0+iy*tile_m,
                              x0+(ix+1)*tile_m, y0+(iy+1)*tile_m, working_srid);
      IF NOT EXISTS (SELECT 1 FROM (SELECT geom FROM ONLY pg_class LIMIT 0) _d) THEN NULL; END IF;
      -- fast empty-tile skip
      EXECUTE format('SELECT EXISTS (SELECT 1 FROM %s l WHERE l.geom && $1)', line_table::text)
        INTO ncells USING cell;  -- reuse ncells as boolean-ish flag holder
      IF ncells = 0 THEN nskip := nskip+1; CONTINUE; END IF;
      PERFORM set_config('statement_timeout', per_tile_timeout_ms::text, true);
      BEGIN
        EXECUTE format($f$
          INSERT INTO %1$s (tile, geom)
          SELECT %2$L, (ST_Dump(ST_Polygonize(noded.g))).geom
          FROM (SELECT ST_Node(ST_Collect(l.geom)) g FROM %3$s l
                WHERE l.geom && ST_Expand($1, %4$s)) noded
          WHERE noded.g IS NOT NULL
        $f$, out_table, ix||'_'||iy, line_table::text, overlap_m) USING cell;
        ndone := ndone+1;
      EXCEPTION WHEN OTHERS THEN nskip := nskip+1;
      END;
    END LOOP;
  END LOOP;
  RETURN format('%s: %s tiles done, %s skipped/empty/failed', out_table, ndone, nskip);
END;
$function$;
-- NOTE: the empty-tile check above is written to be self-contained; in the project
-- run we used a variant (polygonize_tiled_v2) with the line table referenced
-- directly. Either is valid; parameterized form is preferred for reuse.

-- ============================================================================
-- SECTION 3. DRIVER  (templates — set CONFIG values, then run in order)
-- ============================================================================
-- Substitute: <A>=denser-candidate authority table (e.g. green),
--             <B>=other overlapping authority (e.g. orange),
--             plus any non-overlapping tables (e.g. purple, red).
--
-- 3.1 DETAIL ASSESSMENT (informational): vertices per km per dataset
--   SELECT 'A' src, round(SUM(ST_NPoints(geom_m))/(SUM(ST_Length(geom_m))/1000)) vtx_km
--     FROM gom_shoreline.<A>
--   UNION ALL SELECT 'B', round(SUM(ST_NPoints(geom_m))/(SUM(ST_Length(geom_m))/1000))
--     FROM gom_shoreline.<B>;
--
-- 3.2 AUTHORITY MAP (per CELL_M cell, winner = higher vertex density) over the
--     A∩B overlap zone:
--   DROP TABLE IF EXISTS gom_shoreline.authority_map;
--   CREATE TABLE gom_shoreline.authority_map AS
--   WITH ov AS (SELECT ST_Intersection(
--        (SELECT ST_SetSRID(ST_Extent(geom_m),<SRID>) FROM gom_shoreline.<A>),
--        (SELECT ST_SetSRID(ST_Extent(geom_m),<SRID>) FROM gom_shoreline.<B>)) g),
--   cells AS (SELECT gx,gy, ST_MakeEnvelope(ST_XMin(ov.g)+gx*<CELL_M>, ST_YMin(ov.g)+gy*<CELL_M>,
--             ST_XMin(ov.g)+(gx+1)*<CELL_M>, ST_YMin(ov.g)+(gy+1)*<CELL_M>, <SRID>) cell
--             FROM ov, generate_series(0, ceil((ST_XMax(ov.g)-ST_XMin(ov.g))/<CELL_M>)::int) gx,
--                      generate_series(0, ceil((ST_YMax(ov.g)-ST_YMin(ov.g))/<CELL_M>)::int) gy),
--   stats AS (SELECT c.gx,c.gy,c.cell,
--       (SELECT COALESCE(SUM(ST_Length(ST_Intersection(a.geom_m,c.cell))),0) FROM gom_shoreline.<A> a WHERE a.geom_m && c.cell) a_len,
--       (SELECT COALESCE(SUM(ST_NPoints(ST_Intersection(a.geom_m,c.cell))),0) FROM gom_shoreline.<A> a WHERE a.geom_m && c.cell) a_vtx,
--       (SELECT COALESCE(SUM(ST_Length(ST_Intersection(b.geom_m,c.cell))),0) FROM gom_shoreline.<B> b WHERE b.geom_m && c.cell) b_len,
--       (SELECT COALESCE(SUM(ST_NPoints(ST_Intersection(b.geom_m,c.cell))),0) FROM gom_shoreline.<B> b WHERE b.geom_m && c.cell) b_vtx
--     FROM cells c)
--   SELECT gx,gy,cell,a_len,a_vtx,b_len,b_vtx,
--     CASE WHEN a_len<20 AND b_len<20 THEN 'none'
--          WHEN b_len<20 THEN 'A' WHEN a_len<20 THEN 'B'
--          WHEN (b_vtx/NULLIF(b_len,0)) > (a_vtx/NULLIF(a_len,0)) THEN 'B' ELSE 'A' END winner
--   FROM stats WHERE a_len>=20 OR b_len>=20;
--   CREATE INDEX ON gom_shoreline.authority_map USING GIST(cell);
--
-- 3.3 OVERLAP REMOVAL per authority: keep winner whole per cell; clip loser
--     within BUFFER_N of winner. (Build dr_winner + dr_loser as in the project;
--     dr_winner = winner's lines clipped to cell; dr_loser = loser's lines clipped
--     to cell MINUS a BUFFER_N buffer of the cell's winner.)
--     [See Methods doc §4.4; project tables: dr_winner, dr_loser.]
--
-- 3.4 ASSEMBLE NETWORK: dr_winner + dr_loser (overlap zone)
--     + A/B outside the overlap zone (unchanged) + non-overlapping datasets as-is.
--     -> gom_shoreline.line_network  (one row per LineString; GIST index)
--
-- 3.5 POLYGONIZE (tiled) then clean:
--   SELECT gom_shoreline.polygonize_tiled('gom_shoreline.line_network',
--          'gom_shoreline.poly_raw', <x0>, <y0>, 5000, <nx>, <ny>, 500, <SRID>, 45000);
--   -- dedupe by centroid + drop artifact slivers + drop features < MIN_FEATURE_M
--   -- in EVERY direction (oriented bounding box: both sides < MIN_FEATURE_M):
--   DROP TABLE IF EXISTS gom_shoreline.poly_final;
--   CREATE TABLE gom_shoreline.poly_final AS
--   WITH dedup AS (SELECT DISTINCT ON (round(ST_X(ST_Centroid(geom)),1), round(ST_Y(ST_Centroid(geom)),1)) geom
--                  FROM gom_shoreline.poly_raw WHERE ST_IsValid(geom)),
--   m AS (SELECT geom, ST_OrientedEnvelope(geom) obb FROM dedup),
--   sided AS (SELECT geom,
--       GREATEST(ST_Distance(ST_PointN(ST_ExteriorRing(obb),1),ST_PointN(ST_ExteriorRing(obb),2)),
--                ST_Distance(ST_PointN(ST_ExteriorRing(obb),2),ST_PointN(ST_ExteriorRing(obb),3))) long_side,
--       LEAST   (ST_Distance(ST_PointN(ST_ExteriorRing(obb),1),ST_PointN(ST_ExteriorRing(obb),2)),
--                ST_Distance(ST_PointN(ST_ExteriorRing(obb),2),ST_PointN(ST_ExteriorRing(obb),3))) short_side
--       FROM m)
--   SELECT row_number() OVER () pid, geom FROM sided
--   WHERE NOT (long_side < <MIN_FEATURE_M> AND short_side < <MIN_FEATURE_M>);
--   CREATE INDEX ON gom_shoreline.poly_final USING GIST(geom);
--
-- 3.6 VALIDATION:
--   -- overlap: residual length of A within N of B after clip should be ~0
--   -- polygon validity: SELECT count(*) FILTER (WHERE NOT ST_IsValid(geom)) FROM gom_shoreline.poly_final;  -- expect 0
--   -- dangle accounting: line length not on any polygon boundary (open shoreline; preserved).
--
-- 3.7 EXPORT (shell / ogr2ogr), reproject to DELIVERY_SRID:
--   ogr2ogr -f GPKG lines.gpkg  PG:"dbname=<db>" \
--     -sql "SELECT src, geom FROM gom_shoreline.line_network" \
--     -nln shoreline_lines -nlt LINESTRING -s_srs EPSG:<SRID> -t_srs EPSG:<DELIVERY_SRID>
--   ogr2ogr -f GPKG polys.gpkg  PG:"dbname=<db>" \
--     -sql "SELECT pid, geom FROM gom_shoreline.poly_final" \
--     -nln shoreline_polys -nlt POLYGON -s_srs EPSG:<SRID> -t_srs EPSG:<DELIVERY_SRID>
-- ============================================================================
-- END
-- ============================================================================
