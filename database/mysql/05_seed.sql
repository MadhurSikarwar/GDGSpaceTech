-- =====================================================================
-- OrbitWatch — seed data that is not downloaded from a catalogue
-- (catalogue reference data is loaded by `manage.py load-catalog`)
-- =====================================================================

-- Orbital regions on mean altitude, half-open [min, max). The trigger
-- trg_region_no_overlap_ins validates these rows as they are inserted.
INSERT INTO orbit_region (region_id, name, min_altitude_km, max_altitude_km) VALUES
    (1, 'Low LEO (below 500 km)',        0.0,      500.0),
    (2, 'Mid LEO (500-1,000 km)',        500.0,    1000.0),
    (3, 'High LEO (1,000-2,000 km)',     1000.0,   2000.0),
    (4, 'MEO (2,000-35,586 km)',         2000.0,   35586.0),
    (5, 'GEO (35,586-35,986 km)',        35586.0,  35986.0),
    (6, 'Beyond GEO (above 35,986 km)',  35986.0,  500000.0);

INSERT INTO system_config (config_key, config_value, description) VALUES
    ('screening_threshold_km',       '10',  'Record every approach closer than this distance (km)'),
    ('screening_horizon_hours',      '24',  'How far ahead the screening job propagates (hours)'),
    ('screening_step_seconds',       '60',  'Coarse propagation step of the screening job (seconds)'),
    ('screening_max_epoch_age_days', '30',  'Ignore element sets older than this (days)'),
    ('ingest_interval_hours',        '4',   'CelesTrak download interval (hours); CelesTrak updates about every 2 hours'),
    ('celestrak_groups',             'active,last-30-days,cosmos-2251-debris,fengyun-1c-debris,iridium-33-debris,special:gpz',
                                            'CelesTrak GP groups to ingest (special:<name> for SPECIAL= sets)'),
    ('catalog_refresh_cron',         '0 4 * * sun', 'When SATCAT/GCAT reference data is refreshed (cron, UTC)'),
    ('aggregation_cron',             '30 1 * * *',  'When MapReduce/aggregation summaries are rebuilt (cron, UTC)'),
    ('reentry_cron',                 '0 2 * * *',   'When re-entry predictions are refreshed (cron, UTC)'),
    ('backup_cron',                  '0 3 * * *',   'When MySQL and MongoDB backups run (cron, UTC)'),
    ('backup_keep',                  '7',   'Number of backup sets to keep');
