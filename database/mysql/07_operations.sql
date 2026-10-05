-- =====================================================================
-- OrbitWatch — operations layer (additive only, re-runnable):
--   * data provenance: where every orbit came from and how fresh each source is
--   * human approval of avoidance manoeuvres (simulated execution) with audit trail
--   * synthetic-debris demo, kept in its own tables so it never mixes with the catalogue
--   * notification preferences, an e-mail outbox, password reset, DB-backed rate limits
--   * re-entry model registry, scheduler status, and an event log with role-filtered views
--   * the OrbitalGuard archive (close approaches and decisions of the previous version)
-- =====================================================================

DROP PROCEDURE IF EXISTS ow_add_column;
DELIMITER $$
CREATE PROCEDURE ow_add_column(IN p_table VARCHAR(64), IN p_column VARCHAR(64), IN p_definition VARCHAR(500))
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

DROP PROCEDURE IF EXISTS ow_add_index$$
CREATE PROCEDURE ow_add_index(IN p_table VARCHAR(64), IN p_index VARCHAR(64), IN p_definition VARCHAR(500))
MODIFIES SQL DATA
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = p_table AND INDEX_NAME = p_index) THEN
        SET @ddl = CONCAT('ALTER TABLE ', p_table, ' ADD ', p_definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$

DROP PROCEDURE IF EXISTS ow_add_fk$$
CREATE PROCEDURE ow_add_fk(IN p_table VARCHAR(64), IN p_name VARCHAR(64), IN p_definition VARCHAR(500))
MODIFIES SQL DATA
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = p_table AND CONSTRAINT_NAME = p_name) THEN
        SET @ddl = CONCAT('ALTER TABLE ', p_table, ' ADD CONSTRAINT ', p_name, ' ', p_definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

-- ---------------------------------------------------------------------
-- Users: a session version (bumped on password change or reset, which
-- signs out every other session) and login bookkeeping.
-- ---------------------------------------------------------------------
CALL ow_add_column('app_user', 'session_version', 'INT UNSIGNED NOT NULL DEFAULT 1');
CALL ow_add_column('app_user', 'password_changed_at', 'DATETIME NULL');
CALL ow_add_column('app_user', 'last_login_at', 'DATETIME NULL');

-- ---------------------------------------------------------------------
-- Provenance of the current orbit: when the provider served it and which
-- logged download it came from (MongoDB download_log.download_id).
-- ---------------------------------------------------------------------
CALL ow_add_column('current_orbit', 'fetched_at', 'DATETIME(3) NULL');
CALL ow_add_column('current_orbit', 'download_id', 'VARCHAR(40) NULL');

-- Close approaches screened by the previous version (OrbitalGuard) are kept as an archive.
CALL ow_add_column('conjunction_event', 'origin',
    'ENUM(''orbitwatch'',''orbitalguard-archive'') NOT NULL DEFAULT ''orbitwatch''');
CALL ow_add_column('conjunction_event', 'legacy_ref', 'VARCHAR(128) NULL');
CALL ow_add_index('conjunction_event', 'uq_event_legacy_ref', 'UNIQUE KEY uq_event_legacy_ref (legacy_ref)');

-- The element set a re-entry prediction was made from.
CALL ow_add_column('reentry_prediction', 'input_epoch', 'DATETIME(6) NULL');

-- ---------------------------------------------------------------------
-- Data sources and their freshness (shown wherever their data is shown).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_source (
    source_key               VARCHAR(40)  NOT NULL,
    name                     VARCHAR(120) NOT NULL,
    provider                 VARCHAR(80)  NOT NULL,
    url                      VARCHAR(255) NULL,
    description              VARCHAR(500) NULL,
    expected_interval_hours  DECIMAL(7,1) NULL COMMENT 'older than this = stale',
    requires_credentials     BOOLEAN      NOT NULL DEFAULT FALSE,
    last_attempt_at          DATETIME(3)  NULL,
    last_success_at          DATETIME(3)  NULL,
    last_status              ENUM('never','ok','error','skipped') NOT NULL DEFAULT 'never',
    last_records             INT UNSIGNED NULL,
    last_message             VARCHAR(500) NULL,
    PRIMARY KEY (source_key)
) ENGINE=InnoDB;

INSERT INTO data_source (source_key, name, provider, url, description, expected_interval_hours, requires_credentials) VALUES
    ('celestrak_gp', 'CelesTrak GP element sets', 'CelesTrak',
     'https://celestrak.org/NORAD/elements/gp.php',
     'OMM element sets of active satellites and selected debris groups (derived from 18 SDS / Space-Track data).', 6, FALSE),
    ('celestrak_satcat', 'CelesTrak SATCAT', 'CelesTrak', 'https://celestrak.org/pub/satcat.csv',
     'Catalogue of every object ever tracked: type, owner, launch, decay date, orbit centre.', 192, FALSE),
    ('gcat', 'GCAT (General Catalog of Artificial Space Objects)', 'J. McDowell, planet4589.org',
     'https://planet4589.org/space/gcat/', 'Organisations, launch sites, vehicles, launches, missions and parent objects.', 192, FALSE),
    ('spacetrack_gp', 'Space-Track GP catalogue', '18th Space Defense Squadron via Space-Track.org',
     'https://www.space-track.org/basicspacedata/query/class/gp',
     'Newest element set of every object on orbit, including debris and rocket bodies CelesTrak does not publish.', 6, TRUE),
    ('spacetrack_gp_history', 'Space-Track element-set history', '18th Space Defense Squadron via Space-Track.org',
     'https://www.space-track.org/basicspacedata/query/class/gp_history',
     'Older element sets: decay-rate history for the watchlist and training data for the re-entry model.', 168, TRUE),
    ('spacetrack_decay', 'Space-Track decay messages', '18th Space Defense Squadron via Space-Track.org',
     'https://www.space-track.org/basicspacedata/query/class/decay',
     'Observed re-entry dates: the ground truth the re-entry model is trained and evaluated on.', 168, TRUE),
    ('noaa_swpc', 'NOAA SWPC space weather', 'NOAA Space Weather Prediction Center',
     'https://services.swpc.noaa.gov/', 'Planetary Kp/Ap index and F10.7 solar flux.', 3, FALSE),
    ('orbitalguard_archive', 'OrbitalGuard archive', 'OrbitWatch (previous version)', NULL,
     'Element sets, close approaches and decisions recorded by OrbitalGuard in Aug-Sep 2026 (CelesTrak data).', NULL, FALSE)
AS n ON DUPLICATE KEY UPDATE name = n.name, provider = n.provider, url = n.url, description = n.description,
    expected_interval_hours = n.expected_interval_hours, requires_credentials = n.requires_credentials;

-- ---------------------------------------------------------------------
-- Synthetic-debris demo. Its own tables: synthetic objects never enter
-- space_object, conjunction_event, statistics or reports.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS demo_scenario (
    scenario_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    target_norad  INT UNSIGNED    NOT NULL,
    created_by    INT UNSIGNED    NULL,
    created_at    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    status        ENUM('active','cleared','archived') NOT NULL DEFAULT 'active',
    origin        ENUM('orbitwatch','orbitalguard-archive') NOT NULL DEFAULT 'orbitwatch',
    notes         VARCHAR(500)    NULL,
    PRIMARY KEY (scenario_id),
    KEY ix_demo_status (status, created_at),
    CONSTRAINT fk_demo_target FOREIGN KEY (target_norad) REFERENCES space_object (norad_id) ON DELETE CASCADE,
    CONSTRAINT fk_demo_user   FOREIGN KEY (created_by)   REFERENCES app_user (user_id)     ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS demo_object (
    demo_object_id    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    scenario_id       BIGINT UNSIGNED NOT NULL,
    designation       VARCHAR(40)  NOT NULL COMMENT 'SYN-xxxx: never a real NORAD number',
    name              VARCHAR(100) NOT NULL,
    epoch             DATETIME(6)  NOT NULL,
    mean_motion       DOUBLE NOT NULL,
    eccentricity      DOUBLE NOT NULL,
    inclination       DOUBLE NOT NULL,
    raan              DOUBLE NOT NULL,
    arg_perigee       DOUBLE NOT NULL,
    mean_anomaly      DOUBLE NOT NULL,
    bstar             DOUBLE NOT NULL DEFAULT 0,
    mean_motion_dot   DOUBLE NOT NULL DEFAULT 0,
    mean_motion_ddot  DOUBLE NOT NULL DEFAULT 0,
    PRIMARY KEY (demo_object_id),
    UNIQUE KEY uq_demo_designation (designation),
    CONSTRAINT fk_demo_object_scenario FOREIGN KEY (scenario_id) REFERENCES demo_scenario (scenario_id) ON DELETE CASCADE,
    CONSTRAINT ck_demo_designation CHECK (designation LIKE 'SYN-%'),
    CONSTRAINT ck_demo_ecc CHECK (eccentricity >= 0 AND eccentricity < 1)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS demo_event (
    demo_event_id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    scenario_id               BIGINT UNSIGNED NOT NULL,
    demo_object_id            BIGINT UNSIGNED NULL,
    target_norad              INT UNSIGNED    NOT NULL,
    time_of_closest_approach  DATETIME(3)     NOT NULL,
    miss_distance_km          DECIMAL(10,3)   NOT NULL,
    relative_velocity         DECIMAL(8,3)    NOT NULL,
    risk_level                ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL,
    probability_of_collision  DOUBLE          NULL,
    pc_method                 VARCHAR(40)     NULL,
    legacy_ref                VARCHAR(128)    NULL,
    created_at                DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (demo_event_id),
    UNIQUE KEY uq_demo_event_legacy (legacy_ref),
    KEY ix_demo_event_tca (time_of_closest_approach),
    CONSTRAINT fk_demo_event_scenario FOREIGN KEY (scenario_id)    REFERENCES demo_scenario (scenario_id) ON DELETE CASCADE,
    CONSTRAINT fk_demo_event_object   FOREIGN KEY (demo_object_id) REFERENCES demo_object (demo_object_id) ON DELETE CASCADE,
    CONSTRAINT fk_demo_event_target   FOREIGN KEY (target_norad)   REFERENCES space_object (norad_id) ON DELETE CASCADE,
    CONSTRAINT ck_demo_event_miss CHECK (miss_distance_km >= 0)
) ENGINE=InnoDB;

-- An agent assessment is about exactly one close approach: a real one or a synthetic demo one.
ALTER TABLE agent_assessment MODIFY event_id BIGINT UNSIGNED NULL;
CALL ow_add_column('agent_assessment', 'demo_event_id', 'BIGINT UNSIGNED NULL');
CALL ow_add_column('agent_assessment', 'parent_assessment_id', 'BIGINT UNSIGNED NULL COMMENT ''re-plan after a rejection''');
CALL ow_add_column('agent_assessment', 'feedback', 'VARCHAR(1000) NULL COMMENT ''reviewer feedback the re-plan must address''');
CALL ow_add_column('agent_assessment', 'min_miss_km', 'DOUBLE NULL COMMENT ''reviewer-required miss distance after the burn''');
CALL ow_add_column('agent_assessment', 'origin',
    'ENUM(''orbitwatch'',''orbitalguard-archive'') NOT NULL DEFAULT ''orbitwatch''');
CALL ow_add_column('agent_assessment', 'legacy_ref', 'VARCHAR(160) NULL COMMENT ''OrbitalGuard record it was imported from''');
CALL ow_add_index('agent_assessment', 'uq_assessment_legacy_ref', 'UNIQUE KEY uq_assessment_legacy_ref (legacy_ref)');
CALL ow_add_fk('agent_assessment', 'fk_assessment_demo_event',
    'FOREIGN KEY (demo_event_id) REFERENCES demo_event (demo_event_id) ON DELETE CASCADE');
CALL ow_add_fk('agent_assessment', 'fk_assessment_parent',
    'FOREIGN KEY (parent_assessment_id) REFERENCES agent_assessment (assessment_id) ON DELETE SET NULL');

DROP TRIGGER IF EXISTS trg_assessment_subject_ins;
DROP TRIGGER IF EXISTS trg_assessment_subject_upd;
DELIMITER $$
CREATE TRIGGER trg_assessment_subject_ins BEFORE INSERT ON agent_assessment FOR EACH ROW
BEGIN
    IF (NEW.event_id IS NULL) = (NEW.demo_event_id IS NULL) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An assessment must reference exactly one close approach.';
    END IF;
END$$
CREATE TRIGGER trg_assessment_subject_upd BEFORE UPDATE ON agent_assessment FOR EACH ROW
BEGIN
    IF (NEW.event_id IS NULL) = (NEW.demo_event_id IS NULL) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An assessment must reference exactly one close approach.';
    END IF;
END$$
DELIMITER ;

-- ---------------------------------------------------------------------
-- Human decision on a recommended manoeuvre. OrbitWatch has no command
-- uplink: an approved burn is executed in simulation only, and says so.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS maneuver_decision (
    decision_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    assessment_id   BIGINT UNSIGNED NOT NULL,
    status          ENUM('APPROVED','REJECTED') NOT NULL,
    decided_by      INT UNSIGNED    NULL,
    decided_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    reason          VARCHAR(1000)   NULL,
    candidate       JSON            NULL COMMENT 'the exact burn decided on',
    delta_v_mps     DOUBLE          NULL,
    burn_time       DATETIME(3)     NULL,
    burn_direction  VARCHAR(20)     NULL,
    miss_before_km  DOUBLE          NULL,
    miss_after_km   DOUBLE          NULL,
    pc_before       DOUBLE          NULL,
    pc_after        DOUBLE          NULL,
    execution       ENUM('SIMULATED','NONE') NOT NULL DEFAULT 'SIMULATED',
    origin          ENUM('orbitwatch','orbitalguard-archive') NOT NULL DEFAULT 'orbitwatch',
    PRIMARY KEY (decision_id),
    UNIQUE KEY uq_decision_assessment (assessment_id),
    KEY ix_decision_time (decided_at),
    CONSTRAINT fk_decision_assessment FOREIGN KEY (assessment_id) REFERENCES agent_assessment (assessment_id) ON DELETE CASCADE,
    CONSTRAINT fk_decision_user       FOREIGN KEY (decided_by)    REFERENCES app_user (user_id) ON DELETE SET NULL,
    CONSTRAINT ck_decision_reason CHECK (status = 'APPROVED' OR CHAR_LENGTH(COALESCE(reason, '')) >= 3),
    CONSTRAINT ck_decision_dv     CHECK (delta_v_mps IS NULL OR delta_v_mps >= 0)
) ENGINE=InnoDB;

-- The previous version's final risk score of each archived real close approach.
CREATE TABLE IF NOT EXISTS archive_risk_assessment (
    event_id           BIGINT UNSIGNED NOT NULL,
    risk_score         DOUBLE       NOT NULL,
    risk_level         ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL,
    collision_probability DOUBLE    NULL,
    uncertainty_model  VARCHAR(64)  NULL,
    confidence         VARCHAR(16)  NULL,
    notes              TEXT         NULL,
    evaluated_at       DATETIME(3)  NOT NULL,
    reassessments      INT UNSIGNED NOT NULL COMMENT 'how many times OrbitalGuard re-scored it',
    PRIMARY KEY (event_id),
    CONSTRAINT fk_archive_risk_event FOREIGN KEY (event_id) REFERENCES conjunction_event (event_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Notifications: per-user preferences and a transactional e-mail outbox.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_pref (
    user_id          INT UNSIGNED NOT NULL,
    email_alerts     BOOLEAN NOT NULL DEFAULT TRUE,
    min_risk_level   ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL DEFAULT 'HIGH',
    email_decisions  BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    CONSTRAINT fk_pref_user FOREIGN KEY (user_id) REFERENCES app_user (user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS email_outbox (
    email_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id          INT UNSIGNED    NULL,
    to_address       VARCHAR(190)    NOT NULL,
    kind             ENUM('alert','password_reset','password_changed','decision','test') NOT NULL,
    subject          VARCHAR(200)    NOT NULL,
    body_text        TEXT            NOT NULL,
    dedupe_key       VARCHAR(120)    NULL,
    status           ENUM('queued','sending','sent','failed','cancelled') NOT NULL DEFAULT 'queued',
    attempts         TINYINT UNSIGNED NOT NULL DEFAULT 0,
    max_attempts     TINYINT UNSIGNED NOT NULL DEFAULT 6,
    next_attempt_at  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    last_error       VARCHAR(1000)   NULL,
    created_at       DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    sent_at          DATETIME(3)     NULL,
    PRIMARY KEY (email_id),
    UNIQUE KEY uq_email_dedupe (dedupe_key),
    KEY ix_email_due (status, next_attempt_at),
    CONSTRAINT fk_email_user FOREIGN KEY (user_id) REFERENCES app_user (user_id) ON DELETE SET NULL,
    CONSTRAINT ck_email_address CHECK (to_address LIKE '%_@_%._%')
) ENGINE=InnoDB;

-- Password reset: only a SHA-256 of the token is stored; tokens are single-use and expire.
CREATE TABLE IF NOT EXISTS password_reset_token (
    token_hash    CHAR(64)     NOT NULL,
    user_id       INT UNSIGNED NOT NULL,
    created_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    expires_at    DATETIME(3)  NOT NULL,
    used_at       DATETIME(3)  NULL,
    requested_ip  VARCHAR(45)  NULL,
    PRIMARY KEY (token_hash),
    KEY ix_reset_user (user_id, created_at),
    CONSTRAINT fk_reset_user FOREIGN KEY (user_id) REFERENCES app_user (user_id) ON DELETE CASCADE,
    CONSTRAINT ck_reset_expiry CHECK (expires_at > created_at)
) ENGINE=InnoDB;

-- Rate limiting shared by every web process (login, registration, password reset).
CREATE TABLE IF NOT EXISTS rate_limit_event (
    bucket       VARCHAR(190) NOT NULL,
    occurred_at  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY ix_rate_bucket (bucket, occurred_at),
    KEY ix_rate_time (occurred_at)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Re-entry model registry: every trained model, its data and its test score.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ml_model (
    model_version      VARCHAR(40)  NOT NULL,
    task               VARCHAR(40)  NOT NULL DEFAULT 'reentry',
    algorithm          VARCHAR(160) NOT NULL,
    status             ENUM('active','retired','rejected') NOT NULL,
    trained_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    training_objects   INT UNSIGNED NOT NULL,
    training_rows      INT UNSIGNED NOT NULL,
    test_objects       INT UNSIGNED NOT NULL,
    test_rows          INT UNSIGNED NOT NULL,
    data_from          DATE         NULL,
    data_to            DATE         NULL,
    mae_days           DOUBLE       NULL,
    median_ae_days     DOUBLE       NULL,
    interval_coverage  DOUBLE       NULL COMMENT 'share of test re-entries inside the 10-90 % interval',
    metrics            JSON         NULL,
    features           JSON         NULL,
    dataset_sha256     CHAR(64)     NULL,
    artifact_path      VARCHAR(255) NULL,
    notes              VARCHAR(500) NULL,
    PRIMARY KEY (model_version),
    KEY ix_model_task (task, status, trained_at)
) ENGINE=InnoDB;

-- Observed re-entries (Space-Track decay messages; SATCAT decay dates as a fallback):
-- the ground truth the re-entry model learns from and is scored against.
CREATE TABLE IF NOT EXISTS reentry_observation (
    norad_id     INT UNSIGNED NOT NULL,
    source       VARCHAR(40)  NOT NULL,
    decay_epoch  DATETIME     NOT NULL,
    msg_epoch    DATETIME     NULL,
    fetched_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (norad_id, source),
    KEY ix_reentry_obs_epoch (decay_epoch),
    CONSTRAINT fk_reentry_obs_object FOREIGN KEY (norad_id) REFERENCES space_object (norad_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Scheduler status: one row per scheduled job, plus a heartbeat.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scheduler_job (
    job_id            VARCHAR(40)  NOT NULL,
    description       VARCHAR(200) NOT NULL,
    trigger_desc      VARCHAR(120) NOT NULL,
    next_run_at       DATETIME(3)  NULL,
    last_started_at   DATETIME(3)  NULL,
    last_finished_at  DATETIME(3)  NULL,
    last_status       ENUM('running','success','failed','skipped') NULL,
    last_success_at   DATETIME(3)  NULL,
    last_message      VARCHAR(500) NULL,
    updated_at        DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (job_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS scheduler_heartbeat (
    id            TINYINT UNSIGNED NOT NULL DEFAULT 1,
    host          VARCHAR(100) NOT NULL,
    pid           INT UNSIGNED NOT NULL,
    started_at    DATETIME(3)  NOT NULL,
    heartbeat_at  DATETIME(3)  NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_heartbeat_single CHECK (id = 1)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Event log: what happened, by whom, visible per role through views.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_log (
    log_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    occurred_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    category       ENUM('data','screening','alert','agent','maneuver','demo','auth','admin','system','job') NOT NULL,
    severity       ENUM('info','notice','warning','critical') NOT NULL DEFAULT 'info',
    visibility     ENUM('public','analyst','admin') NOT NULL DEFAULT 'public',
    actor_user_id  INT UNSIGNED NULL,
    action         VARCHAR(60)  NOT NULL,
    entity_type    VARCHAR(40)  NULL,
    entity_id      VARCHAR(64)  NULL,
    message        VARCHAR(500) NOT NULL,
    detail         JSON         NULL,
    PRIMARY KEY (log_id),
    KEY ix_log_time (occurred_at),
    KEY ix_log_category (category, occurred_at),
    CONSTRAINT fk_log_user FOREIGN KEY (actor_user_id) REFERENCES app_user (user_id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE OR REPLACE VIEW v_event_log_public AS
SELECT log_id, occurred_at, category, severity, action, entity_type, entity_id, message
  FROM event_log WHERE visibility = 'public';

CREATE OR REPLACE VIEW v_event_log_analyst AS
SELECT log_id, occurred_at, category, severity, visibility, action, entity_type, entity_id, message, detail
  FROM event_log WHERE visibility IN ('public', 'analyst');

-- Conjunction detail now also says where an event came from (every earlier column is kept).
CREATE OR REPLACE VIEW v_conjunction_detail AS
SELECT e.event_id, e.time_of_closest_approach, e.miss_distance_km,
       e.relative_velocity, e.risk_level, e.run_id, e.created_at, e.updated_at,
       e.primary_norad,   p.name AS primary_name,   p.object_type AS primary_type,
       e.secondary_norad, s.name AS secondary_name, s.object_type AS secondary_type,
       e.probability_of_collision, e.pc_method, e.origin
  FROM conjunction_event e
  JOIN space_object p ON p.norad_id = e.primary_norad
  JOIN space_object s ON s.norad_id = e.secondary_norad;

-- ---------------------------------------------------------------------
-- Alerts: archived events are history, not news; they alert nobody.
-- ---------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_conjunction_alerts;
DELIMITER $$
CREATE TRIGGER trg_conjunction_alerts
AFTER INSERT ON conjunction_event
FOR EACH ROW
BEGIN
    IF NEW.origin = 'orbitwatch' THEN
        INSERT INTO alert (event_id, user_id, sent_on)
        SELECT DISTINCT NEW.event_id, s.user_id, NOW()
          FROM subscription s
          JOIN app_user u ON u.user_id = s.user_id
         WHERE u.is_active
           AND s.norad_id IN (NEW.primary_norad, NEW.secondary_norad);
    END IF;
END$$
DELIMITER ;

-- ---------------------------------------------------------------------
-- Account procedures for the auth account, which cannot UPDATE app_user:
-- password change and reset only happen through these.
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_change_password;
DROP PROCEDURE IF EXISTS sp_request_password_reset;
DROP PROCEDURE IF EXISTS sp_reset_password;
DROP PROCEDURE IF EXISTS sp_record_login;
DELIMITER $$
CREATE PROCEDURE sp_change_password(IN p_user_id INT UNSIGNED, IN p_hash VARCHAR(255))
MODIFIES SQL DATA
SQL SECURITY DEFINER
COMMENT 'Set a new password hash and sign out every other session'
BEGIN
    UPDATE app_user
       SET password_hash = p_hash, session_version = session_version + 1, password_changed_at = NOW()
     WHERE user_id = p_user_id AND is_active;
    IF ROW_COUNT() = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Account not found or inactive.';
    END IF;
    UPDATE password_reset_token SET used_at = NOW(3) WHERE user_id = p_user_id AND used_at IS NULL;
END$$

CREATE PROCEDURE sp_request_password_reset(
    IN  p_email       VARCHAR(190),
    IN  p_token_hash  CHAR(64),
    IN  p_ttl_minutes INT,
    IN  p_ip          VARCHAR(45),
    OUT p_user_id     INT UNSIGNED,
    OUT p_name        VARCHAR(100))
MODIFIES SQL DATA
SQL SECURITY DEFINER
COMMENT 'Store a reset token for an active account (at most 3 per hour); silent for unknown e-mails'
BEGIN
    SET p_user_id = NULL, p_name = NULL;
    SELECT user_id, name INTO p_user_id, p_name FROM app_user WHERE email = p_email AND is_active LIMIT 1;
    IF p_user_id IS NOT NULL THEN
        IF (SELECT COUNT(*) FROM password_reset_token
             WHERE user_id = p_user_id AND created_at > NOW(3) - INTERVAL 1 HOUR) >= 3 THEN
            SET p_user_id = NULL, p_name = NULL;
        ELSE
            UPDATE password_reset_token SET used_at = NOW(3) WHERE user_id = p_user_id AND used_at IS NULL;
            INSERT INTO password_reset_token (token_hash, user_id, expires_at, requested_ip)
            VALUES (p_token_hash, p_user_id, NOW(3) + INTERVAL p_ttl_minutes MINUTE, p_ip);
        END IF;
    END IF;
END$$

CREATE PROCEDURE sp_reset_password(IN p_token_hash CHAR(64), IN p_hash VARCHAR(255), OUT p_user_id INT UNSIGNED)
MODIFIES SQL DATA
SQL SECURITY DEFINER
COMMENT 'Consume a valid reset token and set the new password'
BEGIN
    SET p_user_id = NULL;
    SELECT t.user_id INTO p_user_id
      FROM password_reset_token t JOIN app_user u ON u.user_id = t.user_id
     WHERE t.token_hash = p_token_hash AND t.used_at IS NULL AND t.expires_at > NOW(3) AND u.is_active
     FOR UPDATE;
    IF p_user_id IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This reset link is invalid or has expired.';
    END IF;
    UPDATE app_user
       SET password_hash = p_hash, session_version = session_version + 1, password_changed_at = NOW()
     WHERE user_id = p_user_id;
    UPDATE password_reset_token SET used_at = NOW(3) WHERE user_id = p_user_id AND used_at IS NULL;
END$$

CREATE PROCEDURE sp_record_login(IN p_user_id INT UNSIGNED)
MODIFIES SQL DATA
SQL SECURITY DEFINER
BEGIN
    UPDATE app_user SET last_login_at = NOW() WHERE user_id = p_user_id;
END$$
DELIMITER ;

DROP PROCEDURE ow_add_column;
DROP PROCEDURE ow_add_index;
DROP PROCEDURE ow_add_fk;

-- ---------------------------------------------------------------------
-- Privileges, by role (database-enforced, as for every other table).
-- ---------------------------------------------------------------------
GRANT SELECT (session_version, password_changed_at, last_login_at) ON orbitwatch.app_user TO r_auth;
GRANT EXECUTE ON PROCEDURE orbitwatch.sp_change_password        TO r_auth;
GRANT EXECUTE ON PROCEDURE orbitwatch.sp_request_password_reset TO r_auth;
GRANT EXECUTE ON PROCEDURE orbitwatch.sp_reset_password         TO r_auth;
GRANT EXECUTE ON PROCEDURE orbitwatch.sp_record_login           TO r_auth;
GRANT SELECT, INSERT, DELETE ON orbitwatch.rate_limit_event TO r_auth;
GRANT INSERT ON orbitwatch.email_outbox TO r_auth;
GRANT SELECT (user_id, email_alerts) ON orbitwatch.notification_pref TO r_auth;
GRANT INSERT ON orbitwatch.event_log TO r_auth;

GRANT SELECT ON orbitwatch.data_source             TO r_viewer;
GRANT SELECT ON orbitwatch.scheduler_job           TO r_viewer;
GRANT SELECT ON orbitwatch.scheduler_heartbeat     TO r_viewer;
GRANT SELECT ON orbitwatch.ml_model                TO r_viewer;
GRANT SELECT ON orbitwatch.reentry_observation     TO r_viewer;
GRANT SELECT ON orbitwatch.demo_scenario           TO r_viewer;
GRANT SELECT ON orbitwatch.demo_object             TO r_viewer;
GRANT SELECT ON orbitwatch.demo_event              TO r_viewer;
GRANT SELECT ON orbitwatch.maneuver_decision       TO r_viewer;
GRANT SELECT ON orbitwatch.archive_risk_assessment TO r_viewer;
GRANT SELECT ON orbitwatch.v_event_log_public      TO r_viewer;
GRANT SELECT, INSERT, UPDATE ON orbitwatch.notification_pref TO r_viewer;

GRANT SELECT ON orbitwatch.v_event_log_analyst TO r_analyst;
GRANT INSERT ON orbitwatch.event_log           TO r_analyst;
GRANT INSERT ON orbitwatch.maneuver_decision   TO r_analyst;
GRANT INSERT, UPDATE, DELETE ON orbitwatch.demo_scenario TO r_analyst;
GRANT INSERT, UPDATE, DELETE ON orbitwatch.demo_object   TO r_analyst;
GRANT INSERT, UPDATE, DELETE ON orbitwatch.demo_event    TO r_analyst;
GRANT INSERT ON orbitwatch.email_outbox TO r_analyst;
GRANT EXECUTE ON FUNCTION orbitwatch.fn_risk_level TO r_analyst;

GRANT INSERT, UPDATE ON orbitwatch.data_source         TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.scheduler_job       TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.scheduler_heartbeat TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.ml_model            TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.reentry_observation TO r_jobs;
GRANT INSERT, UPDATE ON orbitwatch.email_outbox        TO r_jobs;
GRANT INSERT         ON orbitwatch.event_log           TO r_jobs;
GRANT DELETE         ON orbitwatch.rate_limit_event    TO r_jobs;
GRANT DELETE         ON orbitwatch.password_reset_token TO r_jobs;
GRANT DELETE         ON orbitwatch.demo_scenario       TO r_jobs;

-- Schedules of the jobs added with this file (editable on the admin page like the others).
INSERT IGNORE INTO system_config (config_key, config_value, description) VALUES
    ('reentry_train_cron',     '0 2 * * sun', 'When the re-entry model is retrained and evaluated (cron, UTC)'),
    ('spacetrack_daily_cron',  '15 0 * * *',  'Space-Track decay messages + history of decaying objects (cron, UTC)'),
    ('spacetrack_weekly_cron', '0 5 * * sat', 'Space-Track history of the watchlist and of re-entered objects (cron, UTC)');

-- Who decided on a manoeuvre: analysts and administrators see the reviewer's name (definer view;
-- the analyst role has no access to app_user itself). The public sees decisions without names.
CREATE OR REPLACE VIEW v_maneuver_decision AS
SELECT d.*, u.name AS decided_by_name
  FROM maneuver_decision d LEFT JOIN app_user u ON u.user_id = d.decided_by;
GRANT SELECT ON orbitwatch.v_maneuver_decision TO r_analyst;
