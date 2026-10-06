"""Catalogue rows for objects that are too new for the SATCAT snapshot.

Regression: Space-Track's "TBA - TO BE ASSIGNED" tracks all carry the designator "UNKNOWN". The designator column is
unique, so INSERT IGNORE kept one of them and every other one later failed the current_orbit foreign key (1452),
which aborted the whole ingest.
"""
from orbitwatch import db
from orbitwatch.jobs import ingest


class FakeCursor:
    def __init__(self, known, launches, designators):
        self._answers = {"FROM space_object\"": None}
        self.known, self.launches, self.designators = known, launches, designators
        self._last = None

    def execute(self, sql, params=None):
        self._last = sql

    def fetchall(self):
        if "decay_date" in self._last:
            return list(self.known.items())
        if "FROM launch" in self._last:
            return [(x,) for x in self.launches]
        return [(x,) for x in self.designators]


def _el(norad, name, intl):
    return {"norad_id": norad, "name": name, "intl_designator": intl}


def _run(monkeypatch, element_sets, known=None, designators=("1998-067A",)):
    captured = {}

    def fake_bulk(cur, sql, rows):
        captured["rows"] = list(rows)
    monkeypatch.setattr(db, "bulk_upsert", fake_bulk)
    cur = FakeCursor(known if known is not None else {25544: None}, ["2026-050"], designators)
    created, _ = ingest.ensure_objects(cur, element_sets)
    return created, captured["rows"]


def test_io01_placeholder_designators_are_stored_as_none(monkeypatch):
    sets = [_el(81011, "TBA - TO BE ASSIGNED", "UNKNOWN"), _el(81015, "TBA - TO BE ASSIGNED", "UNKNOWN"),
            _el(81021, "TBA - TO BE ASSIGNED", "UNKNOWN")]
    created, rows = _run(monkeypatch, sets)
    assert created == 3 and [r[0] for r in rows] == [81011, 81015, 81021]
    assert all(r[1] is None for r in rows)          # no two rows share "UNKNOWN"
    assert all(r[3] == "Unknown" for r in rows)


def test_io02_valid_designator_kept_and_launch_linked(monkeypatch):
    created, rows = _run(monkeypatch, [_el(70001, "NEW SAT", "2026-050A")])
    assert rows == [(70001, "2026-050A", "NEW SAT", "Unknown", "2026-050")]


def test_io03_designator_already_held_by_another_object_is_not_reused(monkeypatch):
    sets = [_el(70002, "CLASH", "1998-067A"), _el(70003, "TWIN A", "2026-051B"), _el(70004, "TWIN B", "2026-051B")]
    _, rows = _run(monkeypatch, sets)
    by_id = {r[0]: r for r in rows}
    assert by_id[70002][1] is None                    # held by NORAD 25544 already
    assert by_id[70003][1] == "2026-051B" and by_id[70004][1] is None   # first wins within one batch


def test_io04_known_objects_are_skipped_and_missing_names_get_a_label(monkeypatch):
    sets = [_el(25544, "ISS", "1998-067A"), _el(70005, None, "UNKNOWN"), _el(70006, "STARLINK DEB", "2026-052C")]
    created, rows = _run(monkeypatch, sets)
    assert created == 2 and 25544 not in [r[0] for r in rows]
    by_id = {r[0]: r for r in rows}
    assert by_id[70005][2] == "NORAD 70005"
    assert by_id[70006][3] == "Debris"
