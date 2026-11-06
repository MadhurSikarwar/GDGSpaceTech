"""A self-hosted "are you a person?" check for the login form: a small proof of work.

After repeated failed logins the server asks for one: it hands out a random salt (signed with the app's secret key,
valid 10 minutes) and the browser must find a number `nonce` such that SHA-256("<salt>:<nonce>") starts with BITS zero
bits. A browser does that in about a second; a script guessing passwords pays the same for every single guess, which
makes guessing at scale expensive. No third-party CAPTCHA service, no tracking, nothing to type. Each solved
challenge is accepted once (the salt is remembered in rate_limit_event until it expires).
"""
import hashlib
import hmac
import os
import secrets
import time

from orbitwatch import config, ratelimit

BITS = int(os.getenv("ORBITWATCH_LOGIN_POW_BITS", "16"))   # ~65,000 hashes on average
TTL_S = 600


def _secret():
    return config.ensure_secret("FLASK_SECRET_KEY", 32).encode()


def _sign(salt, expires, bits):
    return hmac.new(_secret(), f"challenge:{salt}:{expires}:{bits}".encode(), hashlib.sha256).hexdigest()


def issue():
    salt, expires = secrets.token_hex(16), int(time.time()) + TTL_S
    return {"algorithm": "SHA-256", "salt": salt, "bits": BITS, "expires": expires, "sig": _sign(salt, expires, BITS)}


def leading_zero_bits(digest):
    n = 0
    for byte in digest:
        if byte == 0:
            n += 8
            continue
        return n + 8 - byte.bit_length()
    return n


def solve(ch):
    """What the browser does (used by the tests and by scripts that legitimately log in)."""
    nonce = 0
    while leading_zero_bits(hashlib.sha256(f"{ch['salt']}:{nonce}".encode()).digest()) < ch["bits"]:
        nonce += 1
    return {**ch, "nonce": nonce}


def verify(answer):
    """True for a correctly signed, unexpired, unused and solved challenge (and marks it used)."""
    if not isinstance(answer, dict):
        return False
    try:
        salt, expires, bits, nonce = str(answer["salt"]), int(answer["expires"]), int(answer["bits"]), int(answer["nonce"])
    except (KeyError, TypeError, ValueError):
        return False
    if bits < BITS or expires < time.time() or not hmac.compare_digest(_sign(salt, expires, bits), str(answer.get("sig"))):
        return False
    if leading_zero_bits(hashlib.sha256(f"{salt}:{nonce}".encode()).digest()) < bits:
        return False
    if ratelimit.exceeded("challenge_used", salt):          # replay of an answer already accepted
        return False
    ratelimit.hit("challenge_used", salt)
    return True
