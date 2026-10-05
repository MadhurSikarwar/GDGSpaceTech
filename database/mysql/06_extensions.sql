-- =====================================================================
-- OrbitWatch — extensions carried over from OrbitalGuard (additive only):
-- probability of collision, ground stations, space weather and the
-- agentic decision-support assessments. Re-runnable.
-- =====================================================================

-- Idempotent column additions (MySQL has no ADD COLUMN IF NOT EXISTS).
DROP PROCEDURE IF EXISTS ow_add_column;

DELIMITER $$
CREATE PROCEDURE ow_add_column(IN p_table VARCHAR(64), IN p_column VARCHAR(64), IN p_definition VARCHAR(255))
MODIFIES SQL DATA
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = p_table AND COLUMN_NAME = p_column) THEN
        SET @ddl = CONCAT('ALTER TABLE ', p_table, ' ADD COLUMN ', p_column, ' ', p_definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

CALL ow_add_column('conjunction_event', 'probability_of_collision', 'DOUBLE NULL');
CALL ow_add_column('conjunction_event', 'pc_method', 'VARCHAR(40) NULL');
-- Where an object orbits (SATCAT ORBIT_CENTER: EA Earth, SU Sun, MO Moon, MA Mars, EL1/EL2 Lagrange
-- points ...; ORBIT_TYPE: ORB in orbit, DOC docked) and whether elements are published (DATA_STATUS:
-- NEA no elements available). Not every object that has not re-entered is orbiting the Earth.
CALL ow_add_column('space_object', 'orbit_center', 'VARCHAR(8) NULL');
CALL ow_add_column('space_object', 'orbit_type', 'VARCHAR(4) NULL');
CALL ow_add_column('space_object', 'data_status', 'VARCHAR(4) NULL');
CALL ow_add_column('space_object', 'in_earth_orbit',
    'BOOLEAN AS (decay_date IS NULL AND COALESCE(orbit_center, ''EA'') = ''EA'' AND COALESCE(orbit_type, ''ORB'') = ''ORB'') STORED');
DROP PROCEDURE ow_add_column;

-- ---------------------------------------------------------------------
-- Ground stations used for contact (uplink) windows before a manoeuvre.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ground_station (
    station_id         VARCHAR(16)  NOT NULL,
    name               VARCHAR(120) NOT NULL,
    latitude           DECIMAL(8,5) NOT NULL,
    longitude          DECIMAL(8,5) NOT NULL,
    altitude_m         DECIMAL(7,1) NOT NULL DEFAULT 0,
    min_elevation_deg  DECIMAL(4,1) NOT NULL DEFAULT 10,
    country_code       VARCHAR(8)   NULL,
    PRIMARY KEY (station_id),
    CONSTRAINT fk_station_country FOREIGN KEY (country_code)
        REFERENCES country (country_code) ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT ck_station_latitude  CHECK (latitude  BETWEEN -90  AND 90),
    CONSTRAINT ck_station_longitude CHECK (longitude BETWEEN -180 AND 180),
    CONSTRAINT ck_station_mask      CHECK (min_elevation_deg BETWEEN 0 AND 45)
) ENGINE=InnoDB;

-- The three polar stations from OrbitalGuard, plus ISRO's ISTRAC network
-- (coordinates approximate, to about 0.1 degree).
INSERT IGNORE INTO ground_station (station_id, name, latitude, longitude, altitude_m, min_elevation_deg) VALUES
    ('SVALBARD',  'Svalbard Satellite Station (SvalSat), Norway', 78.23000, 15.39000, 458, 10),
    ('FAIRBANKS', 'Gilmore Creek Station, Fairbanks, Alaska',     64.98000, -147.50000, 200, 10),
    ('MCMURDO',   'McMurdo Ground Station, Antarctica',          -77.85000, 166.67000, 10, 10),
    ('ISTRAC-BLR', 'ISRO ISTRAC, Bengaluru, India',               13.03000, 77.51000, 900, 5),
    ('ISTRAC-LKO', 'ISRO ISTRAC, Lucknow, India',                 26.91000, 80.96000, 120, 5),
    ('ISTRAC-MAU', 'ISRO ISTRAC, Mauritius',                     -20.17000, 57.50000, 300, 5);

-- ---------------------------------------------------------------------
-- Space weather readings (NOAA SWPC): drive the covariance used for Pc.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS space_weather (
    observed_at  DATETIME     NOT NULL,
    kp           DECIMAL(4,2) NOT NULL,
    ap           DECIMAL(6,1) NOT NULL,
    f107         DECIMAL(6,1) NOT NULL,
    activity     ENUM('QUIET','MODERATE','ELEVATED','STORM') NOT NULL,
    drag_scalar  DECIMAL(6,3) NOT NULL,
    source       VARCHAR(40)  NOT NULL,
    live         BOOLEAN      NOT NULL,
    fetched_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (observed_at),
    CONSTRAINT ck_sw_kp CHECK (kp BETWEEN 0 AND 9)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Agentic decision support: one assessment per agent run, with every step
-- (LLM tool choice, tool result, guardrail) recorded for audit.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_assessment (
    assessment_id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_id                 BIGINT UNSIGNED NOT NULL,
    requested_by             INT UNSIGNED    NULL,
    status                   ENUM('running','complete','failed') NOT NULL DEFAULT 'running',
    engine                   VARCHAR(80)     NOT NULL,
    risk_tier                ENUM('LOW','MEDIUM','HIGH','CRITICAL') NULL,
    decision                 ENUM('MONITOR','MANEUVER_RECOMMENDED','NO_FEASIBLE_MANEUVER','DATA_UNAVAILABLE') NULL,
    pc_before                DOUBLE          NULL,
    pc_after                 DOUBLE          NULL,
    delta_v_mps              DOUBLE          NULL,
    burn_time                DATETIME(3)     NULL,
    burn_direction           VARCHAR(20)     NULL,
    maneuver                 JSON            NULL,
    explanation              TEXT            NULL,
    human_approval_required  BOOLEAN         NOT NULL DEFAULT TRUE,
    started_at               DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    finished_at              DATETIME(3)     NULL,
    PRIMARY KEY (assessment_id),
    KEY ix_assessment_event (event_id, started_at),
    CONSTRAINT fk_assessment_event FOREIGN KEY (event_id) REFERENCES conjunction_event (event_id) ON DELETE CASCADE,
    CONSTRAINT fk_assessment_user  FOREIGN KEY (requested_by) REFERENCES app_user (user_id) ON DELETE SET NULL,
    CONSTRAINT ck_assessment_dv    CHECK (delta_v_mps IS NULL OR delta_v_mps >= 0)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS agent_step (
    assessment_id  BIGINT UNSIGNED  NOT NULL,
    step_no        SMALLINT UNSIGNED NOT NULL,
    actor          ENUM('agent','tool','guardrail','system') NOT NULL,
    tool_name      VARCHAR(60)      NULL,
    arguments      JSON             NULL,
    summary        VARCHAR(1000)    NOT NULL,
    payload        JSON             NULL,
    created_at     DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (assessment_id, step_no),
    CONSTRAINT fk_step_assessment FOREIGN KEY (assessment_id) REFERENCES agent_assessment (assessment_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Conjunction detail now carries Pc as well (every original column is kept).
CREATE OR REPLACE VIEW v_conjunction_detail AS
SELECT e.event_id, e.time_of_closest_approach, e.miss_distance_km,
       e.relative_velocity, e.risk_level, e.run_id, e.created_at, e.updated_at,
       e.primary_norad,   p.name AS primary_name,   p.object_type AS primary_type,
       e.secondary_norad, s.name AS secondary_name, s.object_type AS secondary_type,
       e.probability_of_collision, e.pc_method
  FROM conjunction_event e
  JOIN space_object p ON p.norad_id = e.primary_norad
  JOIN space_object s ON s.norad_id = e.secondary_norad;

-- Catalogue view and debris report with the Earth-orbit flag (every original column is kept).
CREATE OR REPLACE VIEW v_object_catalog AS
SELECT so.norad_id, so.intl_designator, so.name, so.object_type, so.status,
       so.decay_date, so.launch_id, l.launch_date, so.parent_norad_id,
       cw.org_id, cw.org_name, cw.country_code, cw.country_name,
       co.epoch, co.perigee_km, co.apogee_km, co.inclination, co.period_min,
       co.mean_altitude_km, r.region_id, r.name AS region_name,
       so.in_earth_orbit, so.orbit_center, so.orbit_type, so.data_status
  FROM space_object so
  LEFT JOIN launch l           ON l.launch_id = so.launch_id
  LEFT JOIN v_current_owner cw ON cw.norad_id = so.norad_id
  LEFT JOIN current_orbit co   ON co.norad_id = so.norad_id
  LEFT JOIN orbit_region r
         ON co.mean_altitude_km >= r.min_altitude_km
        AND co.mean_altitude_km <  r.max_altitude_km;

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
   AND so.in_earth_orbit
 GROUP BY COALESCE(cw.country_code, '--'), COALESCE(cw.country_name, 'International / unknown');

-- ---------------------------------------------------------------------
-- Privileges for the new objects (r_admin already holds DML on orbitwatch.*)
-- ---------------------------------------------------------------------
GRANT SELECT ON orbitwatch.ground_station   TO r_viewer;
GRANT SELECT ON orbitwatch.space_weather    TO r_viewer;
GRANT SELECT ON orbitwatch.agent_assessment TO r_viewer;
GRANT SELECT ON orbitwatch.agent_step       TO r_viewer;
-- Running the agent is an analyst function.
GRANT INSERT, UPDATE ON orbitwatch.agent_assessment TO r_analyst;
GRANT INSERT         ON orbitwatch.agent_step       TO r_analyst;
GRANT INSERT, UPDATE ON orbitwatch.space_weather    TO r_jobs;
-- Column-level: the screening job may write Pc onto an event and nothing else about it
-- (events themselves are only ever written through sp_record_conjunction).
GRANT UPDATE (probability_of_collision, pc_method) ON orbitwatch.conjunction_event TO r_jobs;
