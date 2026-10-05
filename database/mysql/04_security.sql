-- =====================================================================
-- OrbitWatch — roles and privileges, enforced by MySQL itself (SRS 3.1).
--
-- The web application holds one MySQL account per application role and
-- picks the connection from the logged-in user's role. Even if the
-- application had a bug that let a Public Viewer reach an admin endpoint,
-- MySQL would reject the statement: ow_viewer simply has no privilege on
-- app_user, reference-data writes, the watchlist or the configuration.
--
--   r_auth     login lookup + self-registration (EXECUTE only, never INSERT)
--   r_viewer   Public Viewer: catalogue, orbits, events; own subscriptions/alerts
--   r_analyst  r_viewer + analytical reports and summary tables
--   r_admin    r_analyst + DML on everything (users, reference data, watchlist,
--              configuration, job status); no DDL
--   r_jobs     ingestion / screening / aggregation service account
--   r_backup   read-only dump account for mysqldump
--
-- {{...}} placeholders are filled by `manage.py mysql-setup` with generated
-- passwords that are written to .env only; they never appear in this file.
-- =====================================================================

CREATE ROLE IF NOT EXISTS r_auth, r_viewer, r_analyst, r_admin, r_jobs, r_backup;

-- ---- r_auth ---------------------------------------------------------
-- Column-level privilege: the auth account can read exactly what login needs.
GRANT SELECT (user_id, name, email, role, password_hash, is_active) ON orbitwatch.app_user TO r_auth;
GRANT EXECUTE ON PROCEDURE orbitwatch.sp_register_user TO r_auth;

-- ---- r_viewer -------------------------------------------------------
GRANT SELECT ON orbitwatch.country            TO r_viewer;
GRANT SELECT ON orbitwatch.organisation       TO r_viewer;
GRANT SELECT ON orbitwatch.launch_site        TO r_viewer;
GRANT SELECT ON orbitwatch.launch_vehicle     TO r_viewer;
GRANT SELECT ON orbitwatch.launch             TO r_viewer;
GRANT SELECT ON orbitwatch.space_object       TO r_viewer;
GRANT SELECT ON orbitwatch.mission            TO r_viewer;
GRANT SELECT ON orbitwatch.object_mission     TO r_viewer;
GRANT SELECT ON orbitwatch.object_ownership   TO r_viewer;
GRANT SELECT ON orbitwatch.orbit_region       TO r_viewer;
GRANT SELECT ON orbitwatch.current_orbit      TO r_viewer;
GRANT SELECT ON orbitwatch.conjunction_event  TO r_viewer;
GRANT SELECT ON orbitwatch.watchlist          TO r_viewer;
GRANT SELECT ON orbitwatch.reentry_prediction TO r_viewer;
GRANT SELECT (config_key, config_value, description) ON orbitwatch.system_config TO r_viewer;
GRANT SELECT ON orbitwatch.v_object_region        TO r_viewer;
GRANT SELECT ON orbitwatch.v_current_owner        TO r_viewer;
GRANT SELECT ON orbitwatch.v_object_catalog       TO r_viewer;
GRANT SELECT ON orbitwatch.v_conjunction_detail   TO r_viewer;
GRANT SELECT ON orbitwatch.v_report_region_counts TO r_viewer;
GRANT SELECT ON orbitwatch.v_last_update          TO r_viewer;
-- Own subscriptions and alerts. Alerts can only be acknowledged: the
-- UPDATE privilege covers just these two columns.
GRANT SELECT, INSERT, DELETE ON orbitwatch.subscription TO r_viewer;
GRANT SELECT ON orbitwatch.alert TO r_viewer;
GRANT UPDATE (acknowledged, acknowledged_on) ON orbitwatch.alert TO r_viewer;

-- ---- r_analyst ------------------------------------------------------
GRANT r_viewer TO r_analyst;
GRANT SELECT ON orbitwatch.summary_monthly_altitude    TO r_analyst;
GRANT SELECT ON orbitwatch.summary_region_year         TO r_analyst;
GRANT SELECT ON orbitwatch.v_report_debris_by_country  TO r_analyst;
GRANT SELECT ON orbitwatch.v_report_reentries_per_year TO r_analyst;

-- ---- r_admin --------------------------------------------------------
GRANT r_analyst TO r_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON orbitwatch.* TO r_admin;
GRANT EXECUTE ON orbitwatch.* TO r_admin;

-- ---- r_jobs ---------------------------------------------------------
GRANT SELECT ON orbitwatch.* TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.country          TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.organisation     TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.launch_site      TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.launch_vehicle   TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.launch           TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.space_object     TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.mission          TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.object_mission   TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.object_ownership TO r_jobs;
GRANT INSERT         ON orbitwatch.watchlist        TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.job_run          TO r_jobs;
GRANT INSERT, UPDATE, DELETE ON orbitwatch.current_orbit            TO r_jobs;
GRANT INSERT, UPDATE, DELETE ON orbitwatch.summary_monthly_altitude TO r_jobs;
GRANT INSERT, UPDATE, DELETE ON orbitwatch.summary_region_year      TO r_jobs;
GRANT INSERT, UPDATE, DELETE ON orbitwatch.reentry_prediction       TO r_jobs;
GRANT EXECUTE ON PROCEDURE orbitwatch.sp_record_conjunction TO r_jobs;

-- ---- r_backup -------------------------------------------------------
GRANT SELECT, SHOW VIEW, TRIGGER, LOCK TABLES, EVENT ON orbitwatch.* TO r_backup;
GRANT SHOW_ROUTINE ON *.* TO r_backup;

-- ---- accounts -------------------------------------------------------
CREATE USER IF NOT EXISTS 'ow_auth'@'localhost'    IDENTIFIED BY '{{MYSQL_AUTH_PASSWORD}}';
CREATE USER IF NOT EXISTS 'ow_viewer'@'localhost'  IDENTIFIED BY '{{MYSQL_VIEWER_PASSWORD}}';
CREATE USER IF NOT EXISTS 'ow_analyst'@'localhost' IDENTIFIED BY '{{MYSQL_ANALYST_PASSWORD}}';
CREATE USER IF NOT EXISTS 'ow_admin'@'localhost'   IDENTIFIED BY '{{MYSQL_ADMIN_PASSWORD}}';
CREATE USER IF NOT EXISTS 'ow_jobs'@'localhost'    IDENTIFIED BY '{{MYSQL_JOBS_PASSWORD}}';
CREATE USER IF NOT EXISTS 'ow_backup'@'localhost'  IDENTIFIED BY '{{MYSQL_BACKUP_PASSWORD}}';

-- Re-running setup rotates nothing unexpectedly: passwords come from .env.
ALTER USER 'ow_auth'@'localhost'    IDENTIFIED BY '{{MYSQL_AUTH_PASSWORD}}';
ALTER USER 'ow_viewer'@'localhost'  IDENTIFIED BY '{{MYSQL_VIEWER_PASSWORD}}';
ALTER USER 'ow_analyst'@'localhost' IDENTIFIED BY '{{MYSQL_ANALYST_PASSWORD}}';
ALTER USER 'ow_admin'@'localhost'   IDENTIFIED BY '{{MYSQL_ADMIN_PASSWORD}}';
ALTER USER 'ow_jobs'@'localhost'    IDENTIFIED BY '{{MYSQL_JOBS_PASSWORD}}';
ALTER USER 'ow_backup'@'localhost'  IDENTIFIED BY '{{MYSQL_BACKUP_PASSWORD}}';

GRANT r_auth    TO 'ow_auth'@'localhost';
GRANT r_viewer  TO 'ow_viewer'@'localhost';
GRANT r_analyst TO 'ow_analyst'@'localhost';
GRANT r_admin   TO 'ow_admin'@'localhost';
GRANT r_jobs    TO 'ow_jobs'@'localhost';
GRANT r_backup  TO 'ow_backup'@'localhost';

SET DEFAULT ROLE r_auth    TO 'ow_auth'@'localhost';
SET DEFAULT ROLE r_viewer  TO 'ow_viewer'@'localhost';
SET DEFAULT ROLE r_analyst TO 'ow_analyst'@'localhost';
SET DEFAULT ROLE r_admin   TO 'ow_admin'@'localhost';
SET DEFAULT ROLE r_jobs    TO 'ow_jobs'@'localhost';
SET DEFAULT ROLE r_backup  TO 'ow_backup'@'localhost';
