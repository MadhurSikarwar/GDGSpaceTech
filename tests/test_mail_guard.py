"""Mail to a domain reserved for tests and examples (example.test, example.com, ...) must never reach a real mail server:
the only thing it can do there is bounce into the sender's own inbox, which is exactly what the suite's @example.test
accounts did through Gmail. A server on this machine (a development sink) still receives everything."""
import secrets

import pytest


@pytest.fixture
def outbox(monkeypatch):
    from orbitwatch import config, db, notify
    try:
        db.query("jobs", "SELECT 1 AS x")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no OrbitWatch database reachable: {exc}")
    tag = secrets.token_hex(4)
    mine = {"test": f"guard-{tag}@example.test", "com": f"guard-{tag}@example.com", "real": f"guard-{tag}@university.edu"}
    sent = []
    monkeypatch.setattr(notify, "_send", lambda row: sent.append(row["to_address"]))     # no network, ever
    monkeypatch.setattr(config, "SMTP_USERNAME", "someone@remote.invalid")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "not-a-real-password")
    monkeypatch.setattr(config, "SMTP_SECURITY", "starttls")
    for kind, addr in mine.items():
        notify.enqueue("test", addr, f"guard {kind}", "x", dedupe_key=f"guard:{tag}:{kind}")
    yield {"mine": mine, "sent": sent, "notify": notify, "config": config, "db": db}
    db.execute("admin", "DELETE FROM email_outbox WHERE dedupe_key LIKE %s", (f"guard:{tag}:%",))


def status_of(box):
    rows = box["db"].query("jobs", "SELECT to_address, status, attempts, last_error FROM email_outbox WHERE to_address IN (%s, %s, %s)",
                           tuple(box["mine"].values()))
    return {r["to_address"]: r for r in rows}


def test_mg01_a_real_server_never_sees_test_addresses(outbox, monkeypatch):
    monkeypatch.setattr(outbox["config"], "SMTP_HOST", "smtp.gmail.com")
    sent, _ = outbox["notify"].dispatch(only_to=list(outbox["mine"].values()))
    assert outbox["sent"] == [outbox["mine"]["real"]] and sent == 1          # only the ordinary address is handed over
    rows = status_of(outbox)
    for kind in ("test", "com"):
        row = rows[outbox["mine"][kind]]
        assert row["status"] == "cancelled" and row["attempts"] == 0 and "reserved" in row["last_error"]
    assert rows[outbox["mine"]["real"]]["status"] == "sent"


def test_mg02_a_sink_on_this_machine_receives_everything(outbox, monkeypatch):
    monkeypatch.setattr(outbox["config"], "SMTP_HOST", "127.0.0.1")
    sent, _ = outbox["notify"].dispatch(only_to=list(outbox["mine"].values()))
    assert sent == 3 and sorted(outbox["sent"]) == sorted(outbox["mine"].values())
    assert {r["status"] for r in status_of(outbox).values()} == {"sent"}


def test_mg03_reserved_domains_are_recognised():
    from orbitwatch import notify, reconcile
    for address in ("a@example.com", "a@EXAMPLE.org", "a@example.net", "ops-viewer-1@example.test", "x@host.localhost", "x@y.invalid", "x@z.example"):
        assert notify.is_reserved_address(address), address
    for address in ("person@gmail.com", "someone@rvce.edu.in", "me@examples.com", "", None):
        assert not notify.is_reserved_address(address), address
    assert reconcile.is_test_email("ops-admin-1@example.test") and not reconcile.is_test_email("me@gmail.com")


def test_mg04_the_suite_cannot_use_the_mail_account_in_env():
    from orbitwatch import config
    assert not config.SMTP_USERNAME and not config.SMTP_PASSWORD and not config.smtp_configured()
