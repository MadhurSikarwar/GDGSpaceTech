"""Web Push notifications: close-approach alerts on a user's devices, even while OrbitWatch is closed.

* The browser subscribes through its own push service (Google FCM for Chrome/Edge, Mozilla for Firefox, Apple for
  Safari, Microsoft WNS) and gives the site an endpoint URL plus two keys. Only endpoints on those services are
  accepted (HTTPS, no other host), so the server can never be pointed at an arbitrary address.
* Messages are encrypted for the browser (RFC 8291, aes128gcm) and signed with the site's VAPID key (RFC 8292). The
  key pair is generated once and kept in .env (VAPID_PRIVATE_KEY); only the public half ever leaves the server.
* The endpoint URL is a bearer capability: it is never sent back to the browser or written to logs or errors.
* `send_alerts` (run with the notify job, right after each screening and every 5 minutes) delivers new alerts at or
  above the user's push_min_risk to every device. push_delivery makes it idempotent; a failed delivery is retried by
  later runs up to MAX_ATTEMPTS, and a device whose subscription the push service reports gone (404/410) is removed.
"""
import base64
import hashlib
import json
import logging
import os
import struct
import time
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from orbitwatch import config, db

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RECORD_SIZE = 4096
MAX_PAYLOAD = 3000                      # well under one 4096-byte record
RISK_ORDER = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
# The push services of the major browsers. A host matches exactly, or as a subdomain of an entry starting with '.'.
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com",
              "push.services.mozilla.com", "web.push.apple.com", ".notify.windows.com")


class PushError(Exception):
    """A delivery failed. The message never contains the endpoint (it is a capability URL)."""


# --------------------------------------------------------------------------- encoding helpers
def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64u(text):
    text = str(text or "")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def endpoint_hash(endpoint):
    return hashlib.sha256(endpoint.encode()).hexdigest()


def endpoint_allowed(endpoint):
    try:
        p = urlsplit(endpoint)
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443) or len(endpoint) > 1000:
        return False
    return any(host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


def validate_subscription(data):
    """(endpoint, p256dh, auth) from a browser PushSubscription.toJSON(), or ValueError with a reason."""
    if not isinstance(data, dict):
        raise ValueError("expected a push subscription")
    endpoint = str(data.get("endpoint") or "")
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    if not endpoint_allowed(endpoint):
        raise ValueError("this browser's push service is not one OrbitWatch can deliver to")
    try:
        p256dh, auth = unb64u(keys.get("p256dh")), unb64u(keys.get("auth"))
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
    except (ValueError, TypeError):
        raise ValueError("the subscription keys are not valid") from None
    if len(p256dh) != 65 or len(auth) != 16:
        raise ValueError("the subscription keys are not valid")
    return endpoint, b64u(p256dh), b64u(auth)


# --------------------------------------------------------------------------- VAPID (RFC 8292)
_key_cache = {}


def _private_key():
    if "key" not in _key_cache:
        raw = os.getenv("VAPID_PRIVATE_KEY")
        if raw:
            key = ec.derive_private_key(int.from_bytes(unb64u(raw), "big"), ec.SECP256R1())
        else:
            key = ec.generate_private_key(ec.SECP256R1())
            scalar = key.private_numbers().private_value.to_bytes(32, "big")
            config.set_env_values({"VAPID_PRIVATE_KEY": b64u(scalar)})
            log.info("generated the VAPID key pair for push notifications (stored in .env)")
        _key_cache["key"] = key
    return _key_cache["key"]


def public_key():
    """The applicationServerKey the browser subscribes with (base64url, uncompressed P-256 point)."""
    return b64u(_private_key().public_key().public_bytes(serialization.Encoding.X962,
                                                         serialization.PublicFormat.UncompressedPoint))


def _subject():
    sender = config.MAIL_FROM or config.SMTP_USERNAME
    if sender and "@" in sender:
        return f"mailto:{sender}"
    base = config.APP_BASE_URL or ""
    return base if base.startswith("https://") else "mailto:orbitwatch@localhost"


def vapid_authorization(endpoint, now=None):
    p = urlsplit(endpoint)
    seg = lambda obj: b64u(json.dumps(obj, separators=(",", ":")).encode())
    claims = {"aud": f"{p.scheme}://{p.netloc}", "exp": int(now or time.time()) + 12 * 3600, "sub": _subject()}
    signing_input = f"{seg({'typ': 'JWT', 'alg': 'ES256'})}.{seg(claims)}"
    r, s = decode_dss_signature(_private_key().sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    token = f"{signing_input}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={token}, k={public_key()}"


# --------------------------------------------------------------------------- message encryption (RFC 8291)
def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload, p256dh, auth, *, salt=None, server_key=None):
    """The aes128gcm body for one push message (one record)."""
    ua_public = unb64u(p256dh)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = server_key.public_key().public_bytes(serialization.Encoding.X962,
                                                     serialization.PublicFormat.UncompressedPoint)
    ikm = _hkdf(unb64u(auth), server_key.exchange(ec.ECDH(), ua_key), b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    record = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)        # 0x02: padding delimiter of the last record
    return salt + struct.pack("!I", RECORD_SIZE) + bytes([len(as_public)]) + as_public + record


def send(sub, message, ttl=86400, urgency="normal"):
    """Deliver one message to one device. Returns 'sent' or 'gone' (the subscription no longer exists)."""
    body = json.dumps(message, separators=(",", ":")).encode()
    if len(body) > MAX_PAYLOAD:
        raise PushError("message too long")
    endpoint = sub["endpoint"]
    if not endpoint_allowed(endpoint):
        raise PushError("not a known push service")
    try:
        r = requests.post(endpoint, data=encrypt(body, sub["p256dh"], sub["auth_secret"]), timeout=10,
                          allow_redirects=False,
                          headers={"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                                   "TTL": str(int(max(60, min(ttl, 28 * 86400)))), "Urgency": urgency,
                                   "Authorization": vapid_authorization(endpoint)})
    except requests.RequestException as exc:
        raise PushError(f"push service unreachable ({type(exc).__name__})") from None
    if r.status_code in (200, 201, 202):
        return "sent"
    if r.status_code in (404, 410):
        return "gone"
    raise PushError(f"push service answered HTTP {r.status_code}")


# --------------------------------------------------------------------------- alerts -> devices
def alert_message(a):
    from orbitwatch.notify import both_times
    return {"title": f"{a['risk_level']} close approach · OrbitWatch",
            "body": f"{a['primary_name']} vs {a['secondary_name']}\nmiss {float(a['miss_distance_km']):.3f} km · "
                    f"TCA {both_times(a['time_of_closest_approach'], seconds=False)}",
            "url": f"/#/conjunctions?event={a['event_id']}", "tag": f"ow-alert-{a['alert_id']}"}


def _record_device(account, subscription_id, outcome, error=None):
    if outcome == "sent":
        db.execute(account, "UPDATE push_subscription SET last_success_at = NOW(3), last_error = NULL "
                            "WHERE subscription_id = %s", (subscription_id,))
    elif outcome == "gone":
        db.execute(account, "DELETE FROM push_subscription WHERE subscription_id = %s", (subscription_id,))
    else:
        db.execute(account, "UPDATE push_subscription SET last_failure_at = NOW(3), last_error = %s "
                            "WHERE subscription_id = %s", (str(error)[:300], subscription_id))


def send_alerts(ctx=None):
    """New alerts -> push messages on every device of every subscriber who asked for that risk level.
    Returns (sent, failed)."""
    rows = db.query("jobs", """
        SELECT a.alert_id, a.user_id, COALESCE(p.push_min_risk, 'HIGH') AS push_min_risk,
               ps.subscription_id, ps.endpoint, ps.p256dh, ps.auth_secret, d.attempts,
               e.event_id, e.time_of_closest_approach, e.miss_distance_km, e.risk_level,
               po.name AS primary_name, so.name AS secondary_name,
               TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), e.time_of_closest_approach) AS seconds_to_tca
          FROM alert a
          JOIN app_user u ON u.user_id = a.user_id AND u.is_active
          JOIN push_subscription ps ON ps.user_id = a.user_id
          LEFT JOIN notification_pref p ON p.user_id = a.user_id
          JOIN conjunction_event e ON e.event_id = a.event_id AND e.origin = 'orbitwatch'
          JOIN space_object po ON po.norad_id = e.primary_norad
          JOIN space_object so ON so.norad_id = e.secondary_norad
          LEFT JOIN push_delivery d ON d.alert_id = a.alert_id AND d.subscription_id = ps.subscription_id
         WHERE a.sent_on > NOW() - INTERVAL 2 DAY AND NOT a.acknowledged
           AND e.time_of_closest_approach > UTC_TIMESTAMP()
           AND (d.delivery_id IS NULL OR (d.status = 'pending' AND d.attempts < %s))
         ORDER BY a.alert_id, ps.subscription_id LIMIT 500""", (MAX_ATTEMPTS,))
    sent = failed = 0
    gone = set()
    for a in rows:
        if a["subscription_id"] in gone or RISK_ORDER.index(a["risk_level"]) < RISK_ORDER.index(a["push_min_risk"]):
            continue
        db.execute("jobs", "INSERT IGNORE INTO push_delivery (alert_id, subscription_id) VALUES (%s, %s)",
                   (a["alert_id"], a["subscription_id"]))
        attempt = (a["attempts"] or 0) + 1
        try:
            outcome = send(a, alert_message(a), ttl=a["seconds_to_tca"] or 3600,
                           urgency="high" if a["risk_level"] in ("HIGH", "CRITICAL") else "normal")
        except PushError as exc:
            failed += 1
            db.execute("jobs", "UPDATE push_delivery SET status = %s, attempts = %s, last_error = %s "
                               "WHERE alert_id = %s AND subscription_id = %s",
                       ("failed" if attempt >= MAX_ATTEMPTS else "pending", attempt, str(exc)[:300], a["alert_id"],
                        a["subscription_id"]))
            _record_device("jobs", a["subscription_id"], "failed", exc)
            log.warning("push alert %s to device %s, attempt %s: %s", a["alert_id"], a["subscription_id"], attempt, exc)
            continue
        if outcome == "gone":
            gone.add(a["subscription_id"])
            _record_device("jobs", a["subscription_id"], "gone")       # its deliveries go with it (ON DELETE CASCADE)
            log.info("push device %s unsubscribed at its push service: removed", a["subscription_id"])
            continue
        db.execute("jobs", "UPDATE push_delivery SET status = 'sent', attempts = %s, sent_at = NOW(3), last_error = NULL "
                           "WHERE alert_id = %s AND subscription_id = %s", (attempt, a["alert_id"], a["subscription_id"]))
        _record_device("jobs", a["subscription_id"], "sent")
        sent += 1
    return sent, failed


def pending_count():
    return db.query_one("jobs", "SELECT COUNT(*) AS n FROM push_delivery WHERE status = 'pending' AND attempts < %s",
                        (MAX_ATTEMPTS,))["n"]


def send_test(user_id, name):
    """A test message to every device of one user (My account > Notifications). Returns per-device outcomes."""
    devices = db.query("auth", "SELECT subscription_id, endpoint, p256dh, auth_secret FROM push_subscription "
                               "WHERE user_id = %s", (user_id,))
    message = {"title": "OrbitWatch test notification",
               "body": f"Hello {name}: close-approach alerts for the satellites you follow will appear like this.",
               "url": "/#/account", "tag": "ow-test"}
    out = []
    for d in devices:
        try:
            outcome = send(d, message, ttl=600)
            _record_device("auth", d["subscription_id"], outcome)
            out.append({"subscription_id": d["subscription_id"], "outcome": outcome})
        except PushError as exc:
            _record_device("auth", d["subscription_id"], "failed", exc)
            out.append({"subscription_id": d["subscription_id"], "outcome": "failed", "error": str(exc)})
    return out
