-- =====================================================================
-- OrbitWatch — stored functions, procedures and triggers
-- (re-runnable: everything is dropped and recreated)
-- =====================================================================

DROP FUNCTION  IF EXISTS fn_risk_level;
DROP PROCEDURE IF EXISTS sp_register_user;
DROP PROCEDURE IF EXISTS sp_record_conjunction;
DROP TRIGGER   IF EXISTS trg_conjunction_alerts;
DROP TRIGGER   IF EXISTS trg_object_decay_ins;
DROP TRIGGER   IF EXISTS trg_object_decay_upd;
DROP TRIGGER   IF EXISTS trg_object_decayed_cleanup;
DROP TRIGGER   IF EXISTS trg_ownership_no_overlap_ins;
DROP TRIGGER   IF EXISTS trg_ownership_no_overlap_upd;
DROP TRIGGER   IF EXISTS trg_region_no_overlap_ins;
DROP TRIGGER   IF EXISTS trg_region_no_overlap_upd;

DELIMITER $$

-- ---------------------------------------------------------------------
-- Risk level of a close approach.
-- Base tier from miss distance; co-orbital pairs (relative velocity
-- below 0.1 km/s — formation flying, freshly separated rideshare
-- payloads) drift slowly and predictably, so they are capped at LOW.
-- ---------------------------------------------------------------------
CREATE FUNCTION fn_risk_level(p_miss_km DOUBLE, p_rel_vel_kms DOUBLE)
RETURNS VARCHAR(8)
DETERMINISTIC NO SQL
COMMENT 'Risk tier of a conjunction from miss distance (km) and relative velocity (km/s)'
BEGIN
    IF p_rel_vel_kms < 0.1 THEN RETURN 'LOW'; END IF;
    IF p_miss_km < 1 THEN RETURN 'CRITICAL'; END IF;
    IF p_miss_km < 5 THEN RETURN 'HIGH'; END IF;
    IF p_miss_km < 10 THEN RETURN 'MEDIUM'; END IF;
    RETURN 'LOW';
END$$

-- ---------------------------------------------------------------------
-- Self-registration. The auth account may only EXECUTE this procedure,
-- never INSERT into app_user directly, so a registration request can
-- never create an analyst or administrator — enforced by the database.
-- ---------------------------------------------------------------------
CREATE PROCEDURE sp_register_user(IN p_name VARCHAR(100), IN p_email VARCHAR(190), IN p_hash VARCHAR(255))
MODIFIES SQL DATA
SQL SECURITY DEFINER
COMMENT 'Creates a Public Viewer account'
BEGIN
    INSERT INTO app_user (name, email, role, password_hash)
    VALUES (TRIM(p_name), LOWER(TRIM(p_email)), 'viewer', p_hash);
    SELECT LAST_INSERT_ID() AS user_id;
END$$

-- ---------------------------------------------------------------------
-- Record one close approach found by the screening job.
-- A later screening run re-predicts the same encounter with a slightly
-- different TCA as fresher element sets arrive, so an existing event for
-- the same (unordered) pair near that TCA is updated instead of
-- duplicated — and therefore does not alert subscribers a second time.
-- Co-orbital pairs have no sharp TCA, so they match within a day.
-- ---------------------------------------------------------------------
CREATE PROCEDURE sp_record_conjunction(
    IN  p_primary    INT UNSIGNED,
    IN  p_secondary  INT UNSIGNED,
    IN  p_tca        DATETIME(3),
    IN  p_miss_km    DOUBLE,
    IN  p_rel_vel    DOUBLE,
    IN  p_run_id     BIGINT UNSIGNED,
    OUT p_event_id   BIGINT UNSIGNED,
    OUT p_is_new     BOOLEAN)
MODIFIES SQL DATA
SQL SECURITY DEFINER
COMMENT 'Insert or refine a conjunction event; inserts fire subscriber alerts'
BEGIN
    DECLARE v_event  BIGINT UNSIGNED DEFAULT NULL;
    DECLARE v_window INT DEFAULT IF(p_rel_vel < 0.1, 1440, 15);

    SELECT event_id INTO v_event
      FROM conjunction_event
     WHERE ((primary_norad = p_primary   AND secondary_norad = p_secondary)
         OR (primary_norad = p_secondary AND secondary_norad = p_primary))
       AND time_of_closest_approach BETWEEN p_tca - INTERVAL v_window MINUTE
                                        AND p_tca + INTERVAL v_window MINUTE
     ORDER BY ABS(TIMESTAMPDIFF(SECOND, time_of_closest_approach, p_tca))
     LIMIT 1
     FOR UPDATE;

    IF v_event IS NULL THEN
        INSERT INTO conjunction_event
            (primary_norad, secondary_norad, time_of_closest_approach,
             miss_distance_km, relative_velocity, risk_level, run_id)
        VALUES
            (p_primary, p_secondary, p_tca, p_miss_km, p_rel_vel,
             fn_risk_level(p_miss_km, p_rel_vel), p_run_id);
        SET p_event_id = LAST_INSERT_ID(), p_is_new = TRUE;
    ELSE
        UPDATE conjunction_event
           SET time_of_closest_approach = p_tca,
               miss_distance_km         = p_miss_km,
               relative_velocity        = p_rel_vel,
               risk_level               = fn_risk_level(p_miss_km, p_rel_vel),
               run_id                   = p_run_id
         WHERE event_id = v_event;
        SET p_event_id = v_event, p_is_new = FALSE;
    END IF;
END$$

-- ---------------------------------------------------------------------
-- Alerts: every active subscriber of either object gets one alert when a
-- new close approach is recorded. Runs inside the screening transaction,
-- so events and their alerts become visible together or not at all.
-- ---------------------------------------------------------------------
CREATE TRIGGER trg_conjunction_alerts
AFTER INSERT ON conjunction_event
FOR EACH ROW
BEGIN
    INSERT INTO alert (event_id, user_id, sent_on)
    SELECT DISTINCT NEW.event_id, s.user_id, NOW()
      FROM subscription s
      JOIN app_user u ON u.user_id = s.user_id
     WHERE u.is_active
       AND s.norad_id IN (NEW.primary_norad, NEW.secondary_norad);
END$$

-- ---------------------------------------------------------------------
-- Decay bookkeeping: a decay date means the object has re-entered.
-- ---------------------------------------------------------------------
CREATE TRIGGER trg_object_decay_ins
BEFORE INSERT ON space_object
FOR EACH ROW
BEGIN
    IF NEW.decay_date IS NOT NULL THEN
        SET NEW.status = 'Decayed';
    END IF;
END$$

CREATE TRIGGER trg_object_decay_upd
BEFORE UPDATE ON space_object
FOR EACH ROW
BEGIN
    IF NEW.decay_date IS NOT NULL THEN
        SET NEW.status = 'Decayed';
    END IF;
END$$

-- A re-entered object no longer has a current orbit and cannot be screened.
CREATE TRIGGER trg_object_decayed_cleanup
AFTER UPDATE ON space_object
FOR EACH ROW
BEGIN
    IF NEW.decay_date IS NOT NULL AND OLD.decay_date IS NULL THEN
        DELETE FROM current_orbit WHERE norad_id = NEW.norad_id;
        DELETE FROM watchlist     WHERE norad_id = NEW.norad_id;
    END IF;
END$$

-- ---------------------------------------------------------------------
-- Ownership periods of one object must not overlap ([from, to), NULL = open).
-- The insert check skips the row with the same key: INSERT ... ON DUPLICATE
-- KEY UPDATE fires BEFORE INSERT first, and a same-key row is then updated
-- (checked by the update trigger), never inserted alongside.
-- ---------------------------------------------------------------------
CREATE TRIGGER trg_ownership_no_overlap_ins
BEFORE INSERT ON object_ownership
FOR EACH ROW
BEGIN
    IF EXISTS (SELECT 1 FROM object_ownership o
                WHERE o.norad_id = NEW.norad_id
                  AND o.from_date <> NEW.from_date
                  AND o.from_date < COALESCE(NEW.to_date, '9999-12-31')
                  AND NEW.from_date < COALESCE(o.to_date, '9999-12-31')) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Ownership periods of an object may not overlap';
    END IF;
END$$

CREATE TRIGGER trg_ownership_no_overlap_upd
BEFORE UPDATE ON object_ownership
FOR EACH ROW
BEGIN
    IF EXISTS (SELECT 1 FROM object_ownership o
                WHERE o.norad_id = NEW.norad_id
                  AND NOT (o.norad_id = OLD.norad_id AND o.from_date = OLD.from_date)
                  AND o.from_date < COALESCE(NEW.to_date, '9999-12-31')
                  AND NEW.from_date < COALESCE(o.to_date, '9999-12-31')) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Ownership periods of an object may not overlap';
    END IF;
END$$

-- ---------------------------------------------------------------------
-- Orbital regions must not overlap, so every altitude maps to one region.
-- ---------------------------------------------------------------------
CREATE TRIGGER trg_region_no_overlap_ins
BEFORE INSERT ON orbit_region
FOR EACH ROW
BEGIN
    IF EXISTS (SELECT 1 FROM orbit_region r
                WHERE r.min_altitude_km < NEW.max_altitude_km
                  AND NEW.min_altitude_km < r.max_altitude_km) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Orbital regions may not overlap';
    END IF;
END$$

CREATE TRIGGER trg_region_no_overlap_upd
BEFORE UPDATE ON orbit_region
FOR EACH ROW
BEGIN
    IF EXISTS (SELECT 1 FROM orbit_region r
                WHERE r.region_id <> OLD.region_id
                  AND r.min_altitude_km < NEW.max_altitude_km
                  AND NEW.min_altitude_km < r.max_altitude_km) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Orbital regions may not overlap';
    END IF;
END$$

DELIMITER ;
