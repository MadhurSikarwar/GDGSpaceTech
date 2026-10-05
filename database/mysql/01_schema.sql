-- =====================================================================
-- OrbitWatch — relational schema (MySQL 8.x)
--
-- 3NF design from the synopsis (section 4):
--   * Organisation and Country are separate relations (no repeated
--     country/organisation details on objects or launches).
--   * The orbital region of an object is NOT stored: it depends on
--     altitude, so it is derived by joining Current_Orbit with the
--     altitude ranges in Orbit_Region (see v_object_region).
--   * Perigee / apogee / period are generated columns computed by the
--     DBMS from the published mean elements, so they can never disagree
--     with the elements they derive from.
--
-- Run through `manage.py mysql-setup` (or MySQL Workbench). Every
-- statement ends its line with the active delimiter.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------

CREATE TABLE country (
    country_code  VARCHAR(8)   NOT NULL,
    name          VARCHAR(120) NOT NULL,
    PRIMARY KEY (country_code)
) ENGINE=InnoDB;

CREATE TABLE organisation (
    org_id        VARCHAR(16)  NOT NULL,
    name          VARCHAR(160) NOT NULL,
    org_type      ENUM('Government','Private','Academic') NOT NULL,
    country_code  VARCHAR(8)   NULL,
    PRIMARY KEY (org_id),
    KEY ix_org_name (name),
    KEY ix_org_country (country_code),
    CONSTRAINT fk_org_country FOREIGN KEY (country_code)
        REFERENCES country (country_code) ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE launch_site (
    site_id       VARCHAR(16)   NOT NULL,
    name          VARCHAR(160)  NOT NULL,
    country_code  VARCHAR(8)    NULL,
    latitude      DECIMAL(8,5)  NULL,
    longitude     DECIMAL(8,5)  NULL,
    PRIMARY KEY (site_id),
    KEY ix_site_country (country_code),
    CONSTRAINT fk_site_country FOREIGN KEY (country_code)
        REFERENCES country (country_code) ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT ck_site_latitude  CHECK (latitude  BETWEEN -90  AND 90),
    CONSTRAINT ck_site_longitude CHECK (longitude BETWEEN -180 AND 180)
) ENGINE=InnoDB;

CREATE TABLE launch_vehicle (
    vehicle_id  INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name        VARCHAR(64)  NOT NULL,
    org_id      VARCHAR(16)  NULL,
    PRIMARY KEY (vehicle_id),
    UNIQUE KEY uq_vehicle_name (name),
    KEY ix_vehicle_org (org_id),
    CONSTRAINT fk_vehicle_org FOREIGN KEY (org_id)
        REFERENCES organisation (org_id) ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB;

-- launch_id is the COSPAR launch tag ('2008-052'; failures look like '1958-F01').
CREATE TABLE launch (
    launch_id    VARCHAR(16)  NOT NULL,
    launch_date  DATETIME     NOT NULL,
    site_id      VARCHAR(16)  NULL,
    vehicle_id   INT UNSIGNED NULL,
    outcome      ENUM('Success','Failure','Unknown') NOT NULL DEFAULT 'Unknown',
    PRIMARY KEY (launch_id),
    KEY ix_launch_date (launch_date),
    KEY ix_launch_site (site_id),
    KEY ix_launch_vehicle (vehicle_id),
    CONSTRAINT fk_launch_site FOREIGN KEY (site_id)
        REFERENCES launch_site (site_id) ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT fk_launch_vehicle FOREIGN KEY (vehicle_id)
        REFERENCES launch_vehicle (vehicle_id) ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Space objects (recursive: debris -> parent object it broke away from)
-- ---------------------------------------------------------------------

CREATE TABLE space_object (
    norad_id         INT UNSIGNED NOT NULL,
    intl_designator  VARCHAR(16)  NULL,
    name             VARCHAR(100) NOT NULL,
    object_type      ENUM('Payload','Rocket Body','Debris','Unknown') NOT NULL,
    launch_id        VARCHAR(16)  NULL,
    parent_norad_id  INT UNSIGNED NULL,
    status           ENUM('Operational','Non-operational','Partially operational',
                          'Backup','Spare','Extended mission','Decayed','Unknown')
                     NOT NULL DEFAULT 'Unknown',
    decay_date       DATE NULL,
    PRIMARY KEY (norad_id),
    UNIQUE KEY uq_object_intl_designator (intl_designator),
    KEY ix_object_name (name),
    KEY ix_object_type (object_type),
    KEY ix_object_launch (launch_id),
    KEY ix_object_parent (parent_norad_id),
    KEY ix_object_decay (decay_date),
    CONSTRAINT fk_object_launch FOREIGN KEY (launch_id)
        REFERENCES launch (launch_id) ON UPDATE CASCADE ON DELETE SET NULL,
    -- No referential action on the parent FK: MySQL forbids actions on a
    -- column that a CHECK constraint uses, and a parent with debris
    -- children must not silently disappear anyway.
    CONSTRAINT fk_object_parent FOREIGN KEY (parent_norad_id)
        REFERENCES space_object (norad_id),
    CONSTRAINT ck_object_not_own_parent CHECK (parent_norad_id IS NULL OR parent_norad_id <> norad_id),
    CONSTRAINT ck_object_decay_status   CHECK (decay_date IS NULL OR status = 'Decayed')
) ENGINE=InnoDB;

CREATE TABLE mission (
    mission_id  INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name        VARCHAR(120) NOT NULL,
    purpose     VARCHAR(60)  NOT NULL DEFAULT 'Unspecified',
    org_id      VARCHAR(16)  NULL,
    PRIMARY KEY (mission_id),
    UNIQUE KEY uq_mission_name (name),
    KEY ix_mission_org (org_id),
    CONSTRAINT fk_mission_org FOREIGN KEY (org_id)
        REFERENCES organisation (org_id) ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE object_mission (
    norad_id    INT UNSIGNED NOT NULL,
    mission_id  INT UNSIGNED NOT NULL,
    PRIMARY KEY (norad_id, mission_id),
    KEY ix_object_mission_mission (mission_id),
    CONSTRAINT fk_om_object  FOREIGN KEY (norad_id)   REFERENCES space_object (norad_id) ON DELETE CASCADE,
    CONSTRAINT fk_om_mission FOREIGN KEY (mission_id) REFERENCES mission (mission_id)    ON DELETE CASCADE
) ENGINE=InnoDB;

-- Time-based ownership: satellites can change hands. One owner at a time
-- (overlaps are rejected by trg_ownership_no_overlap_*); to_date NULL = current.
CREATE TABLE object_ownership (
    norad_id   INT UNSIGNED NOT NULL,
    org_id     VARCHAR(16)  NOT NULL,
    from_date  DATE         NOT NULL,
    to_date    DATE         NULL,
    PRIMARY KEY (norad_id, from_date),
    KEY ix_ownership_org (org_id),
    KEY ix_ownership_current (norad_id, to_date),
    CONSTRAINT fk_ownership_object FOREIGN KEY (norad_id)
        REFERENCES space_object (norad_id) ON DELETE CASCADE,
    CONSTRAINT fk_ownership_org FOREIGN KEY (org_id)
        REFERENCES organisation (org_id) ON UPDATE CASCADE,
    CONSTRAINT ck_ownership_period CHECK (to_date IS NULL OR to_date >= from_date)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Orbits
-- ---------------------------------------------------------------------

-- Half-open altitude bands [min, max) on mean altitude; overlaps rejected by trigger.
CREATE TABLE orbit_region (
    region_id        TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name             VARCHAR(60)  NOT NULL,
    min_altitude_km  DECIMAL(9,1) NOT NULL,
    max_altitude_km  DECIMAL(9,1) NOT NULL,
    PRIMARY KEY (region_id),
    UNIQUE KEY uq_region_name (name),
    CONSTRAINT ck_region_min   CHECK (min_altitude_km >= 0),
    CONSTRAINT ck_region_range CHECK (max_altitude_km > min_altitude_km)
) ENGINE=InnoDB;

-- Latest element set of each object (weak entity of Space_Object). Full
-- history lives in MongoDB (orbit_history); only the newest epoch is kept here.
CREATE TABLE current_orbit (
    norad_id          INT UNSIGNED NOT NULL,
    epoch             DATETIME(6)  NOT NULL,
    mean_motion       DOUBLE       NOT NULL COMMENT 'rev/day (Kozai, as published)',
    eccentricity      DOUBLE       NOT NULL,
    inclination       DOUBLE       NOT NULL COMMENT 'deg',
    raan              DOUBLE       NOT NULL COMMENT 'deg',
    arg_perigee       DOUBLE       NOT NULL COMMENT 'deg',
    mean_anomaly      DOUBLE       NOT NULL COMMENT 'deg',
    bstar             DOUBLE       NOT NULL COMMENT 'SGP4 drag term, 1/earth radii',
    mean_motion_dot   DOUBLE       NOT NULL COMMENT 'rev/day^2 (/2 as in TLE)',
    mean_motion_ddot  DOUBLE       NOT NULL COMMENT 'rev/day^3 (/6 as in TLE)',
    element_set_no    INT          NULL,
    rev_at_epoch      INT          NULL,
    source            ENUM('CelesTrak','Space-Track') NOT NULL DEFAULT 'CelesTrak',
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    -- Derived attributes, computed by the DBMS (mu = 398600.4418 km^3/s^2, Re = 6378.137 km).
    period_min        DOUBLE AS (1440e0 / mean_motion) STORED,
    perigee_km        DOUBLE AS (POW(398600.4418e0 / POW(mean_motion * 2e0 * PI() / 86400e0, 2e0), 1e0 / 3e0)
                                 * (1e0 - eccentricity) - 6378.137e0) STORED,
    apogee_km         DOUBLE AS (POW(398600.4418e0 / POW(mean_motion * 2e0 * PI() / 86400e0, 2e0), 1e0 / 3e0)
                                 * (1e0 + eccentricity) - 6378.137e0) STORED,
    mean_altitude_km  DOUBLE AS (POW(398600.4418e0 / POW(mean_motion * 2e0 * PI() / 86400e0, 2e0), 1e0 / 3e0)
                                 - 6378.137e0) STORED,
    PRIMARY KEY (norad_id),
    KEY ix_orbit_mean_altitude (mean_altitude_km),
    KEY ix_orbit_perigee (perigee_km),
    KEY ix_orbit_epoch (epoch),
    CONSTRAINT fk_orbit_object FOREIGN KEY (norad_id)
        REFERENCES space_object (norad_id) ON DELETE CASCADE,
    CONSTRAINT ck_orbit_mean_motion  CHECK (mean_motion > 0),
    CONSTRAINT ck_orbit_eccentricity CHECK (eccentricity >= 0 AND eccentricity < 1),
    CONSTRAINT ck_orbit_inclination  CHECK (inclination BETWEEN 0 AND 180)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Users, subscriptions, alerts
-- ---------------------------------------------------------------------

CREATE TABLE app_user (
    user_id        INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name           VARCHAR(100) NOT NULL,
    email          VARCHAR(190) NOT NULL,
    role           ENUM('viewer','analyst','admin') NOT NULL DEFAULT 'viewer',
    password_hash  VARCHAR(255) NOT NULL,
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_user_email (email),
    CONSTRAINT ck_user_email CHECK (email LIKE '%_@_%._%'),
    CONSTRAINT ck_user_hash  CHECK (CHAR_LENGTH(password_hash) >= 50)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Operations: job runs, watchlist, configuration
-- ---------------------------------------------------------------------

CREATE TABLE job_run (
    run_id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    job_name           VARCHAR(40)  NOT NULL,
    triggered_by       ENUM('schedule','manual','cli') NOT NULL DEFAULT 'schedule',
    attempt            TINYINT UNSIGNED NOT NULL DEFAULT 1,
    status             ENUM('running','success','failed','skipped') NOT NULL DEFAULT 'running',
    started_at         DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    finished_at        DATETIME(3)  NULL,
    records_processed  INT UNSIGNED NULL,
    message            TEXT         NULL,
    PRIMARY KEY (run_id),
    KEY ix_job_name_started (job_name, started_at),
    CONSTRAINT ck_job_finished CHECK (finished_at IS NULL OR finished_at >= started_at)
) ENGINE=InnoDB;

-- Two roles of Space_Object in one relationship: primary and secondary.
CREATE TABLE conjunction_event (
    event_id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    primary_norad             INT UNSIGNED   NOT NULL,
    secondary_norad           INT UNSIGNED   NOT NULL,
    time_of_closest_approach  DATETIME(3)    NOT NULL,
    miss_distance_km          DECIMAL(10,3)  NOT NULL,
    relative_velocity         DECIMAL(8,3)   NOT NULL COMMENT 'km/s at TCA',
    risk_level                ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL,
    run_id                    BIGINT UNSIGNED NULL COMMENT 'screening run that last updated it',
    created_at                DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id),
    KEY ix_event_tca (time_of_closest_approach),
    KEY ix_event_primary (primary_norad, time_of_closest_approach),
    KEY ix_event_secondary (secondary_norad, time_of_closest_approach),
    KEY ix_event_risk (risk_level, time_of_closest_approach),
    CONSTRAINT fk_event_primary   FOREIGN KEY (primary_norad)   REFERENCES space_object (norad_id),
    CONSTRAINT fk_event_secondary FOREIGN KEY (secondary_norad) REFERENCES space_object (norad_id),
    CONSTRAINT fk_event_run FOREIGN KEY (run_id) REFERENCES job_run (run_id) ON DELETE SET NULL,
    CONSTRAINT ck_event_distinct_objects CHECK (primary_norad <> secondary_norad),
    CONSTRAINT ck_event_miss_distance    CHECK (miss_distance_km >= 0),
    CONSTRAINT ck_event_relative_velocity CHECK (relative_velocity >= 0)
) ENGINE=InnoDB;

CREATE TABLE subscription (
    user_id        INT UNSIGNED NOT NULL,
    norad_id       INT UNSIGNED NOT NULL,
    subscribed_on  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, norad_id),
    KEY ix_subscription_object (norad_id),
    CONSTRAINT fk_sub_user   FOREIGN KEY (user_id)  REFERENCES app_user (user_id)     ON DELETE CASCADE,
    CONSTRAINT fk_sub_object FOREIGN KEY (norad_id) REFERENCES space_object (norad_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE alert (
    alert_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_id         BIGINT UNSIGNED NOT NULL,
    user_id          INT UNSIGNED    NOT NULL,
    sent_on          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    acknowledged     BOOLEAN         NOT NULL DEFAULT FALSE,
    acknowledged_on  DATETIME        NULL,
    PRIMARY KEY (alert_id),
    UNIQUE KEY uq_alert_event_user (event_id, user_id),
    KEY ix_alert_user (user_id, acknowledged, sent_on),
    CONSTRAINT fk_alert_event FOREIGN KEY (event_id) REFERENCES conjunction_event (event_id) ON DELETE CASCADE,
    CONSTRAINT fk_alert_user  FOREIGN KEY (user_id)  REFERENCES app_user (user_id)          ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE watchlist (
    norad_id  INT UNSIGNED NOT NULL,
    reason    VARCHAR(200) NULL,
    added_by  INT UNSIGNED NULL,
    added_on  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (norad_id),
    CONSTRAINT fk_watch_object FOREIGN KEY (norad_id) REFERENCES space_object (norad_id) ON DELETE CASCADE,
    CONSTRAINT fk_watch_user   FOREIGN KEY (added_by) REFERENCES app_user (user_id)      ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE system_config (
    config_key    VARCHAR(64)  NOT NULL,
    config_value  VARCHAR(500) NOT NULL,
    description   VARCHAR(255) NULL,
    updated_by    INT UNSIGNED NULL,
    updated_on    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (config_key),
    CONSTRAINT fk_config_user FOREIGN KEY (updated_by) REFERENCES app_user (user_id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Summaries computed by MapReduce / aggregation over the MongoDB history
-- ---------------------------------------------------------------------

CREATE TABLE summary_monthly_altitude (
    norad_id         INT UNSIGNED NOT NULL,
    month            DATE         NOT NULL COMMENT 'first day of the month',
    avg_altitude_km  DOUBLE       NOT NULL,
    min_altitude_km  DOUBLE       NOT NULL,
    max_altitude_km  DOUBLE       NOT NULL,
    samples          INT UNSIGNED NOT NULL,
    computed_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (norad_id, month),
    CONSTRAINT fk_sma_object FOREIGN KEY (norad_id) REFERENCES space_object (norad_id) ON DELETE CASCADE,
    CONSTRAINT ck_sma_month CHECK (DAYOFMONTH(month) = 1)
) ENGINE=InnoDB;

CREATE TABLE summary_region_year (
    region_id     TINYINT UNSIGNED NOT NULL,
    year          SMALLINT UNSIGNED NOT NULL,
    object_count  INT UNSIGNED NOT NULL,
    computed_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (region_id, year),
    CONSTRAINT fk_sry_region FOREIGN KEY (region_id) REFERENCES orbit_region (region_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Output of the re-entry regression model (innovative experiment).
CREATE TABLE reentry_prediction (
    norad_id              INT UNSIGNED NOT NULL,
    predicted_on          DATETIME     NOT NULL,
    predicted_decay_date  DATE         NOT NULL,
    days_remaining        DOUBLE       NOT NULL,
    lower_days            DOUBLE       NOT NULL,
    upper_days            DOUBLE       NOT NULL,
    model_version         VARCHAR(40)  NOT NULL,
    PRIMARY KEY (norad_id),
    KEY ix_reentry_date (predicted_decay_date),
    CONSTRAINT fk_reentry_object FOREIGN KEY (norad_id) REFERENCES space_object (norad_id) ON DELETE CASCADE,
    CONSTRAINT ck_reentry_bounds CHECK (lower_days <= days_remaining AND days_remaining <= upper_days)
) ENGINE=InnoDB;
