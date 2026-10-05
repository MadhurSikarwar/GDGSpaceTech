-- =====================================================================
-- OrbitWatch — views
-- Orbital region is always derived here from Current_Orbit x Orbit_Region;
-- it is never stored against an object (it depends on altitude, not on
-- the object's key, so storing it would break 3NF).
-- =====================================================================

CREATE OR REPLACE VIEW v_object_region AS
SELECT co.norad_id, r.region_id, r.name AS region_name, co.mean_altitude_km
  FROM current_orbit co
  JOIN orbit_region r
    ON co.mean_altitude_km >= r.min_altitude_km
   AND co.mean_altitude_km <  r.max_altitude_km;

CREATE OR REPLACE VIEW v_current_owner AS
SELECT oo.norad_id, oo.org_id, o.name AS org_name, o.org_type,
       o.country_code, c.name AS country_name, oo.from_date
  FROM object_ownership oo
  JOIN organisation o ON o.org_id = oo.org_id
  LEFT JOIN country c ON c.country_code = o.country_code
 WHERE oo.to_date IS NULL;

CREATE OR REPLACE VIEW v_object_catalog AS
SELECT so.norad_id, so.intl_designator, so.name, so.object_type, so.status,
       so.decay_date, so.launch_id, l.launch_date, so.parent_norad_id,
       cw.org_id, cw.org_name, cw.country_code, cw.country_name,
       co.epoch, co.perigee_km, co.apogee_km, co.inclination, co.period_min,
       co.mean_altitude_km, r.region_id, r.name AS region_name
  FROM space_object so
  LEFT JOIN launch l          ON l.launch_id = so.launch_id
  LEFT JOIN v_current_owner cw ON cw.norad_id = so.norad_id
  LEFT JOIN current_orbit co  ON co.norad_id = so.norad_id
  LEFT JOIN orbit_region r
         ON co.mean_altitude_km >= r.min_altitude_km
        AND co.mean_altitude_km <  r.max_altitude_km;

CREATE OR REPLACE VIEW v_conjunction_detail AS
SELECT e.event_id, e.time_of_closest_approach, e.miss_distance_km,
       e.relative_velocity, e.risk_level, e.run_id, e.created_at, e.updated_at,
       e.primary_norad,   p.name AS primary_name,   p.object_type AS primary_type,
       e.secondary_norad, s.name AS secondary_name, s.object_type AS secondary_type
  FROM conjunction_event e
  JOIN space_object p ON p.norad_id = e.primary_norad
  JOIN space_object s ON s.norad_id = e.secondary_norad;

-- Last successful run of each job (public "data as of" timestamps).
CREATE OR REPLACE VIEW v_last_update AS
SELECT job_name, MAX(finished_at) AS last_success
  FROM job_run
 WHERE status = 'success'
 GROUP BY job_name;

-- Latest run of each job, whatever its status (admin monitoring).
CREATE OR REPLACE VIEW v_job_latest AS
SELECT j.run_id, j.job_name, j.triggered_by, j.attempt, j.status,
       j.started_at, j.finished_at, j.records_processed, j.message
  FROM job_run j
  JOIN (SELECT job_name, MAX(run_id) AS run_id FROM job_run GROUP BY job_name) latest
    ON latest.run_id = j.run_id;

-- ---------------------------------------------------------------------
-- Analytical reports (SRS 3.6)
-- ---------------------------------------------------------------------

-- Number of objects in each orbital region, by object type.
CREATE OR REPLACE VIEW v_report_region_counts AS
SELECT r.region_id, r.name AS region_name, r.min_altitude_km, r.max_altitude_km,
       COUNT(co.norad_id)                                          AS total_objects,
       COUNT(CASE WHEN so.object_type = 'Payload'     THEN 1 END)  AS payloads,
       COUNT(CASE WHEN so.object_type = 'Rocket Body' THEN 1 END)  AS rocket_bodies,
       COUNT(CASE WHEN so.object_type = 'Debris'      THEN 1 END)  AS debris,
       COUNT(CASE WHEN so.object_type = 'Unknown'     THEN 1 END)  AS unknown
  FROM orbit_region r
  LEFT JOIN current_orbit co
         ON co.mean_altitude_km >= r.min_altitude_km
        AND co.mean_altitude_km <  r.max_altitude_km
  LEFT JOIN space_object so ON so.norad_id = co.norad_id
 GROUP BY r.region_id, r.name, r.min_altitude_km, r.max_altitude_km;

-- Debris still in orbit, by the country of its current owner; the LEO
-- column answers "which countries own the most debris in low Earth orbit".
CREATE OR REPLACE VIEW v_report_debris_by_country AS
SELECT COALESCE(cw.country_code, '--')                       AS country_code,
       COALESCE(cw.country_name, 'International / unknown') AS country_name,
       COUNT(*)                                             AS debris_on_orbit,
       COUNT(CASE WHEN co.mean_altitude_km < 2000 THEN 1 END) AS debris_in_leo,
       COUNT(co.norad_id)                                   AS debris_with_current_orbit
  FROM space_object so
  LEFT JOIN v_current_owner cw ON cw.norad_id = so.norad_id
  LEFT JOIN current_orbit co   ON co.norad_id = so.norad_id
 WHERE so.object_type = 'Debris'
   AND so.decay_date IS NULL
 GROUP BY COALESCE(cw.country_code, '--'), COALESCE(cw.country_name, 'International / unknown');

-- Re-entries per year, by object type.
CREATE OR REPLACE VIEW v_report_reentries_per_year AS
SELECT YEAR(decay_date)                                         AS year,
       COUNT(*)                                                 AS reentries,
       COUNT(CASE WHEN object_type = 'Payload'     THEN 1 END)  AS payloads,
       COUNT(CASE WHEN object_type = 'Rocket Body' THEN 1 END)  AS rocket_bodies,
       COUNT(CASE WHEN object_type = 'Debris'      THEN 1 END)  AS debris,
       COUNT(CASE WHEN object_type = 'Unknown'     THEN 1 END)  AS unknown
  FROM space_object
 WHERE decay_date IS NOT NULL
 GROUP BY YEAR(decay_date);
