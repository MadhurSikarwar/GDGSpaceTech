"""E-mail times are shown in India Standard Time (UTC+05:30) with UTC beside them."""
from datetime import datetime

from orbitwatch.notify import both_times


def test_tm01_ist_is_utc_plus_five_thirty():
    assert both_times(datetime(2026, 10, 6, 14, 9, 27)) == "2026-10-06 19:39:27 IST (14:09:27 UTC)"


def test_tm02_utc_date_is_shown_when_the_dates_differ():
    # 20:00 UTC is already 01:30 the next day in India
    assert both_times(datetime(2026, 10, 6, 20, 0, 0)) == "2026-10-07 01:30:00 IST (10-06 20:00:00 UTC)"


def test_tm03_minutes_only_for_subject_lines():
    assert both_times(datetime(2026, 12, 31, 18, 45, 59), seconds=False) == "2027-01-01 00:15 IST (12-31 18:45 UTC)"
