"""`ow prepare`, which start-website.bat runs before every start: first-time detection and database updates.

The database is never changed here: the migration is replaced by a recorder, and the schema record is written to a
temporary file.
"""
import io

import pytest

import manage


@pytest.fixture
def no_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))          # not interactive: never prompts


def test_a_fresh_install_is_sent_to_first_time_setup(monkeypatch, no_tty):
    monkeypatch.delenv("MYSQL_VIEWER_PASSWORD", raising=False)
    monkeypatch.setenv("MYSQL_ROOT_PASSWORD", "x")
    assert manage.prepare() == manage.FIRST_TIME


def test_without_the_root_password_it_stops_and_says_so(monkeypatch, no_tty, capsys):
    monkeypatch.delenv("MYSQL_VIEWER_PASSWORD", raising=False)
    monkeypatch.delenv("MYSQL_ROOT_PASSWORD", raising=False)
    assert manage.prepare() == 1
    assert "MYSQL_ROOT_PASSWORD" in capsys.readouterr().out


def test_updates_are_applied_once_when_the_sql_files_change(monkeypatch, tmp_path, no_tty):
    from orbitwatch import db, mysql_setup, server
    try:
        db.query_one("viewer", "SELECT 1 AS x FROM space_object LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no OrbitWatch database reachable: {exc}")
    runs = []
    monkeypatch.setattr(mysql_setup, "run", lambda reset=False: runs.append(reset))
    monkeypatch.setattr(server, "make_cert", lambda: ("cert", "key"))
    monkeypatch.setattr(manage, "SCHEMA_STAMP", tmp_path / "schema.applied")
    monkeypatch.setenv("MYSQL_ROOT_PASSWORD", "x")
    assert manage.prepare() == 0 and runs == [False]                 # no record yet: applied, never with reset
    assert manage.prepare() == 0 and runs == [False]                 # unchanged: nothing to do
    monkeypatch.setattr(manage, "_schema_digest", lambda: "a-new-definition")
    assert manage.prepare() == 0 and runs == [False, False]          # changed: applied again


def test_the_schema_record_ignores_line_endings(tmp_path, monkeypatch):
    from orbitwatch import config
    (tmp_path / "a.sql").write_bytes(b"CREATE TABLE t (x INT);\r\n")
    monkeypatch.setattr(config, "SQL_DIR", tmp_path)
    crlf = manage._schema_digest()
    (tmp_path / "a.sql").write_bytes(b"CREATE TABLE t (x INT);\n")
    assert manage._schema_digest() == crlf                           # a git checkout must not look like a change
