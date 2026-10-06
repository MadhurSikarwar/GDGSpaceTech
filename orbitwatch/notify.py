"""E-mail notifications: a transactional outbox (MySQL email_outbox) and its sender.

* Anything that wants to send mail inserts a row; nothing sends inline. A
  dedupe key (e.g. 'alert:<id>') makes queueing idempotent.
* `queue_alert_emails` (scheduled) turns new close-approach alerts into e-mails
  for subscribers whose preferences ask for them (minimum risk level).
* `dispatch` (scheduled every minute, and nudged right after a password-reset
  request) sends due messages over SMTP. A failure is retried with growing
  back-off (1, 5, 15, 60, 180 minutes) up to max_attempts, then marked failed.
  Without SMTP settings the messages simply wait in the queue.
* The body of a password-reset message (which contains the reset link) is
  erased once sent.
"""
import logging
import smtplib
import ssl
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from orbitwatch import config, db, events
from orbitwatch.jobs.runner import SkipJob

log = logging.getLogger(__name__)

BACKOFF_MIN = (1, 5, 15, 60, 180)
RISK_ORDER = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# Domains reserved for tests and examples (RFC 2606): no mail server accepts them. Sent through a real server they only
# bounce back into the sender's own inbox (the suite's @example.test accounts did exactly that through Gmail).
RESERVED_DOMAINS = {"example.com", "example.org", "example.net"}
RESERVED_SUFFIXES = (".test", ".invalid", ".localhost", ".example")
RESERVED_NOTE = "not sent: the address is on a domain reserved for tests and examples (RFC 2606)"


def is_reserved_address(address):
    domain = (address or "").rsplit("@", 1)[-1].lower()
    return domain in RESERVED_DOMAINS or domain.endswith(RESERVED_SUFFIXES)


def _local_smtp():
    """A server on this machine (a development sink) may take any address; only a real one must not see test addresses."""
    return (config.SMTP_HOST or "").strip().lower() in ("127.0.0.1", "localhost", "::1")


def mail_from():
    return config.MAIL_FROM or config.SMTP_USERNAME or "orbitwatch@localhost"


def enqueue(kind, to_address, subject, body, *, user_id=None, dedupe_key=None, account="jobs"):
    """Queue one message; returns True if queued (False if the dedupe key was already used)."""
    rowcount, _ = db.execute(account, """
        INSERT IGNORE INTO email_outbox (user_id, to_address, kind, subject, body_text, dedupe_key)
        VALUES (%s, %s, %s, %s, %s, %s)""", (user_id, to_address, kind, subject[:200], body, dedupe_key))
    return rowcount == 1


def _send(msg_row):
    msg = EmailMessage()
    msg["From"] = f"OrbitWatch <{mail_from()}>"
    msg["To"] = msg_row["to_address"]
    msg["Subject"] = msg_row["subject"]
    msg["Message-ID"] = f"<orbitwatch-{msg_row['email_id']}@{mail_from().split('@')[-1]}>"
    msg.set_content(msg_row["body_text"])
    timeout = 30
    if config.SMTP_SECURITY == "ssl":
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=timeout,
                                  context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=timeout)
    try:
        if config.SMTP_SECURITY == "starttls":
            server.starttls(context=ssl.create_default_context())
        if config.SMTP_USERNAME and config.SMTP_PASSWORD:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            pass


def dispatch(ctx=None, limit=50, only_to=None):
    """Send due messages. Scheduled every minute; safe to run concurrently (rows are claimed first).

    only_to (tests): restrict to these recipient addresses, so a test never sends anybody else's mail.
    """
    only = list(only_to or [])
    flt = f" AND to_address IN ({','.join(['%s'] * len(only))})" if only else ""
    waiting = db.query_one("jobs", f"SELECT COUNT(*) AS n FROM email_outbox WHERE status = 'queued'{flt}", tuple(only))["n"]
    if not config.smtp_configured():
        raise SkipJob(f"SMTP is not configured (SMTP_USERNAME / SMTP_PASSWORD in .env); {waiting} e-mails waiting")
    if not waiting:
        raise SkipJob("no e-mail due")
    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(f"""SELECT email_id, to_address FROM email_outbox WHERE status = 'queued' AND next_attempt_at <= NOW(3){flt}
                         ORDER BY next_attempt_at LIMIT %s FOR UPDATE SKIP LOCKED""", (*only, limit))
        due = cur.fetchall()
        blocked = [r["email_id"] for r in due if not _local_smtp() and is_reserved_address(r["to_address"])]
        ids = [r["email_id"] for r in due if r["email_id"] not in blocked]
        if blocked:
            cur.execute(f"UPDATE email_outbox SET status = 'cancelled', last_error = %s "
                        f"WHERE email_id IN ({','.join(['%s'] * len(blocked))})", (RESERVED_NOTE, *blocked))
            log.info("%s e-mail(s) to reserved test domains cancelled, not sent", len(blocked))
        if ids:
            cur.execute(f"UPDATE email_outbox SET status = 'sending', attempts = attempts + 1 "
                        f"WHERE email_id IN ({','.join(['%s'] * len(ids))})", ids)
        conn.commit()
    if not ids:
        raise SkipJob(f"{len(blocked)} e-mails to reserved test domains were cancelled, not sent" if blocked
                      else f"{waiting} e-mails waiting for their retry time")
    sent = failed = 0
    for email_id in ids:
        row = db.query_one("jobs", "SELECT * FROM email_outbox WHERE email_id = %s", (email_id,))
        try:
            _send(row)
            scrub = ", body_text = '[password reset link removed after sending]'" if row["kind"] == "password_reset" else ""
            db.execute("jobs", f"UPDATE email_outbox SET status = 'sent', sent_at = NOW(3), last_error = NULL{scrub} "
                               "WHERE email_id = %s", (email_id,))
            sent += 1
        except (OSError, smtplib.SMTPException) as exc:
            failed += 1
            attempt = row["attempts"]
            final = attempt >= row["max_attempts"]
            delay = BACKOFF_MIN[min(attempt - 1, len(BACKOFF_MIN) - 1)]
            db.execute("jobs", """UPDATE email_outbox SET status = %s, last_error = %s,
                                      next_attempt_at = NOW(3) + INTERVAL %s MINUTE WHERE email_id = %s""",
                       ("failed" if final else "queued", f"{type(exc).__name__}: {exc}"[:1000], delay, email_id))
            log.warning("e-mail %s attempt %s failed: %s", email_id, attempt, exc)
            if final:
                events.record("system", "email_failed", f"E-mail #{email_id} ({row['kind']}) could not be delivered",
                              severity="warning", visibility="admin", entity_type="email", entity_id=email_id)
    if failed and not sent:
        raise RuntimeError(f"{failed} e-mails failed (will retry)")
    return sent, f"{sent} sent, {failed} failed and rescheduled"


def dispatch_soon():
    """Try to deliver right away (e.g. a password reset) without blocking the request."""
    if not config.smtp_configured():
        return

    def go():
        try:
            dispatch(limit=10)
        except SkipJob:
            pass
        except Exception as exc:  # noqa: BLE001 - the scheduler retries
            log.warning("immediate e-mail dispatch failed: %s", exc)
    threading.Thread(target=go, daemon=True, name="email-dispatch").start()


# --------------------------------------------------------------------------- alert e-mails
IST = timedelta(hours=5, minutes=30)   # India Standard Time, UTC+05:30, no daylight saving


def both_times(utc_dt, seconds=True):
    """'2026-10-06 19:39:27 IST (14:09:27 UTC)': e-mails go to readers in India, who read IST."""
    fmt = "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M"
    ist = utc_dt + IST
    utc_part = utc_dt.strftime("%H:%M:%S" if seconds else "%H:%M") if ist.date() == utc_dt.date() \
        else utc_dt.strftime("%m-%d " + ("%H:%M:%S" if seconds else "%H:%M"))
    return f"{ist.strftime(fmt)} IST ({utc_part} UTC)"


def _alert_body(a):
    tca = a["time_of_closest_approach"]
    pc = f"{a['probability_of_collision']:.1e}" if a["probability_of_collision"] is not None else "not computed"
    return (f"Hello {a['name']},\n\n"
            f"OrbitWatch recorded a new close approach for an object you follow.\n\n"
            f"  {a['primary_name']} (NORAD {a['primary_norad']})\n"
            f"  vs {a['secondary_name']} (NORAD {a['secondary_norad']}, {a['secondary_type']})\n\n"
            f"  Time of closest approach: {both_times(tca)}\n"
            f"  Miss distance:            {float(a['miss_distance_km']):.3f} km\n"
            f"  Relative velocity:        {float(a['relative_velocity']):.2f} km/s\n"
            f"  Risk level:               {a['risk_level']}\n"
            f"  Probability of collision: {pc}\n\n"
            f"Details: {config.APP_BASE_URL}/#/conjunctions?event={a['event_id']}\n\n"
            f"Screened with SGP4 from {a['source'] or 'CelesTrak/Space-Track'} element sets. You receive these e-mails "
            f"for {a['min_risk_level']} risk and above; change that under My alerts > Notifications.\n")


def queue_alert_emails(ctx=None):
    """Alerts of the last two days -> e-mails, per each subscriber's preferences (idempotent)."""
    rows = db.query("jobs", """
        SELECT a.alert_id, a.user_id, u.name, u.email, COALESCE(p.min_risk_level, 'HIGH') AS min_risk_level,
               e.event_id, e.time_of_closest_approach, e.miss_distance_km, e.relative_velocity, e.risk_level,
               e.probability_of_collision, e.primary_norad, po.name AS primary_name,
               e.secondary_norad, so.name AS secondary_name, so.object_type AS secondary_type, co.source
          FROM alert a
          JOIN app_user u ON u.user_id = a.user_id AND u.is_active
          LEFT JOIN notification_pref p ON p.user_id = a.user_id
          JOIN conjunction_event e ON e.event_id = a.event_id AND e.origin = 'orbitwatch'
          JOIN space_object po ON po.norad_id = e.primary_norad
          JOIN space_object so ON so.norad_id = e.secondary_norad
          LEFT JOIN current_orbit co ON co.norad_id = e.primary_norad
          LEFT JOIN email_outbox o ON o.dedupe_key = CONCAT('alert:', a.alert_id)
         WHERE a.sent_on > NOW() - INTERVAL 2 DAY AND NOT a.acknowledged
           AND COALESCE(p.email_alerts, TRUE) AND o.email_id IS NULL
           AND e.time_of_closest_approach > UTC_TIMESTAMP()""")
    queued = 0
    for a in rows:
        if RISK_ORDER.index(a["risk_level"]) < RISK_ORDER.index(a["min_risk_level"]):
            continue
        subject = (f"[OrbitWatch] {a['risk_level']} close approach: {a['primary_name']} vs {a['secondary_name']} "
                   f"at {both_times(a['time_of_closest_approach'], seconds=False)}")
        queued += enqueue("alert", a["email"], subject, _alert_body(a), user_id=a["user_id"],
                          dedupe_key=f"alert:{a['alert_id']}")
    if not rows:
        raise SkipJob("no new alerts to e-mail")
    return queued, f"{queued} alert e-mails queued from {len(rows)} new alerts"


def queue_decision_emails(ctx=None):
    """Manoeuvre decisions of the last two days -> e-mails to analysts/admins who asked for them (idempotent)."""
    decisions = db.query("jobs", """
        SELECT d.decision_id, d.assessment_id, d.status, d.reason, d.delta_v_mps, d.burn_time, d.burn_direction,
               u.name AS decided_by_name, a.demo_event_id IS NOT NULL AS synthetic,
               COALESCE(po.name, tso.name) AS primary_name, COALESCE(so.name, dob.name) AS secondary_name
          FROM maneuver_decision d
          JOIN agent_assessment a ON a.assessment_id = d.assessment_id
          LEFT JOIN app_user u ON u.user_id = d.decided_by
          LEFT JOIN conjunction_event e ON e.event_id = a.event_id
          LEFT JOIN space_object po ON po.norad_id = e.primary_norad
          LEFT JOIN space_object so ON so.norad_id = e.secondary_norad
          LEFT JOIN demo_event de ON de.demo_event_id = a.demo_event_id
          LEFT JOIN space_object tso ON tso.norad_id = de.target_norad
          LEFT JOIN demo_object dob ON dob.demo_object_id = de.demo_object_id
         WHERE d.origin = 'orbitwatch' AND d.decided_at > NOW() - INTERVAL 2 DAY""")
    if not decisions:
        return 0, "no new decisions"
    readers = db.query("jobs", """SELECT u.user_id, u.name, u.email FROM app_user u
                                    JOIN notification_pref p ON p.user_id = u.user_id
                                   WHERE u.is_active AND u.role IN ('analyst', 'admin') AND p.email_decisions""")
    queued = 0
    for d in decisions:
        d["decided_by_name"] = d["decided_by_name"] or "A reviewer"
        for r in readers:
            subject, text = decision_email(r["name"], d)
            queued += enqueue("decision", r["email"], subject + (" [synthetic demo]" if d["synthetic"] else ""), text,
                              user_id=r["user_id"], dedupe_key=f"decision:{d['decision_id']}:{r['user_id']}")
    return queued, f"{queued} decision e-mails queued"


def queue_all(ctx=None):
    """Scheduled every 5 minutes: alert and decision e-mails (both idempotent through dedupe keys)."""
    try:
        a, _ = queue_alert_emails(ctx)
    except SkipJob:
        a = 0
    b, _ = queue_decision_emails(ctx)
    if not a and not b:
        raise SkipJob("nothing new to e-mail")
    return a + b, f"{a} alert and {b} decision e-mails queued"


def reset_email(name, link, minutes):
    return ("[OrbitWatch] Reset your password",
            f"Hello {name},\n\nSomeone (hopefully you) asked to reset the password of your OrbitWatch account.\n\n"
            f"Open this link within {minutes} minutes to choose a new password:\n\n  {link}\n\n"
            "The link works once. If you did not ask for this, ignore this message: your password is unchanged.\n")


def password_changed_email(name, how):
    return ("[OrbitWatch] Your password was changed",
            f"Hello {name},\n\nThe password of your OrbitWatch account was {how} at "
            f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, and every other session was signed out.\n\n"
            f"If this was not you, reset your password now: {config.APP_BASE_URL}/#/forgot\n")


def decision_email(name, d):
    return (f"[OrbitWatch] Manoeuvre {d['status'].lower()}: {d['primary_name']}",
            f"Hello {name},\n\n{d['decided_by_name']} {d['status'].lower()} the recommended avoidance manoeuvre for "
            f"{d['primary_name']} vs {d['secondary_name']} (assessment #{d['assessment_id']}).\n\n"
            + (f"Burn: {d['delta_v_mps']:.3f} m/s {d['burn_direction']} at {both_times(d['burn_time'])} "
               f"(SIMULATED: OrbitWatch has no command uplink).\n" if d["status"] == "APPROVED" and d.get("delta_v_mps")
               else f"Reason: {d.get('reason') or '-'}\n")
            + f"\n{config.APP_BASE_URL}/#/conjunctions?assessment={d['assessment_id']}\n")


def housekeeping(ctx=None):
    """Prune rate-limit hits, spent reset tokens and old sent mail bodies; clear finished demo scenarios."""
    n = 0
    n += db.execute("jobs", "DELETE FROM rate_limit_event WHERE occurred_at < NOW(3) - INTERVAL 1 DAY")[0]
    n += db.execute("jobs", "DELETE FROM password_reset_token WHERE expires_at < NOW(3) - INTERVAL 7 DAY")[0]
    n += db.execute("jobs", "UPDATE job_run SET status = 'failed', finished_at = CURRENT_TIMESTAMP(3), "
                            "message = CONCAT(COALESCE(message, ''), ' [process ended while running]') "
                            "WHERE status = 'running' AND started_at < NOW() - INTERVAL 6 HOUR")[0]
    n += db.execute("jobs", "DELETE FROM demo_scenario WHERE status = 'cleared' "
                            "AND created_at < NOW() - INTERVAL 7 DAY")[0]
    return n, f"{n} rows pruned"


def due_count():
    return db.query_one("jobs", "SELECT COUNT(*) AS n FROM email_outbox WHERE status = 'queued' "
                                "AND next_attempt_at <= NOW(3)")["n"]


def due_summary():
    return db.query_one("jobs", """SELECT SUM(status = 'queued') AS queued, SUM(status = 'failed') AS failed,
                                          SUM(status = 'sent' AND sent_at > NOW() - INTERVAL 1 DAY) AS sent_24h
                                     FROM email_outbox""")

