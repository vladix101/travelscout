from datetime import date, datetime

import pytest

from app.domain.airports import timezone_for
from app.services.timeplan import estimate_arrival_local, stay_dates, usable_time
from tests.conftest import make_leg


def test_timezone_lookup_with_overrides():
    assert timezone_for("BEG") == "Europe/Belgrade"
    assert timezone_for("LTN") == "Europe/London"
    assert timezone_for("TFS", "ES") == "Atlantic/Canary"
    assert timezone_for("XXX", None) is None


def test_arrival_estimate_crosses_time_zones():
    # BEG (UTC+1 in winter) -> LTN (UTC+0): 3h05 block, departs 21:45 local
    arr = estimate_arrival_local(datetime(2026, 11, 2, 21, 45), "Europe/Belgrade", "Europe/London", 185)
    assert arr == datetime(2026, 11, 2, 23, 50)


def test_arrival_estimate_across_dst_change():
    # EU DST ends 2026-10-25 03:00 local. 01:30 CEST departure + 120 min block.
    arr = estimate_arrival_local(datetime(2026, 10, 25, 1, 30), "Europe/Belgrade", "Europe/Belgrade", 120)
    assert arr == datetime(2026, 10, 25, 2, 30)  # clocks fall back one hour


def test_arrival_estimate_unknown_tz_returns_none():
    assert estimate_arrival_local(datetime(2026, 11, 2, 10, 0), None, "Europe/Rome", 90) is None


def test_overnight_arrival_needs_room_from_previous_night():
    out = make_leg(
        "BEG", "LTN", datetime(2026, 11, 2, 23, 30), datetime(2026, 11, 3, 1, 20), "40", arr_tz="Europe/London"
    )
    inb = make_leg("LTN", "BEG", datetime(2026, 11, 6, 6, 30), datetime(2026, 11, 6, 11, 30), "40")
    checkin, checkout, overnight = stay_dates(out, inb)
    assert overnight
    assert checkin == date(2026, 11, 2)
    assert checkout == date(2026, 11, 6)
    ut = usable_time(out, inb, transfer_minutes=60)
    assert ut.nights == 4
    assert ut.overnight_arrival
    assert ut.departure_note  # 06:30 return flight


def test_regular_arrival_same_day_checkin():
    out = make_leg("BEG", "FCO", datetime(2026, 11, 4, 15, 50), datetime(2026, 11, 4, 17, 25), "40")
    inb = make_leg("FCO", "BEG", datetime(2026, 11, 8, 18, 10), datetime(2026, 11, 8, 19, 45), "40")
    assert stay_dates(out, inb) == (date(2026, 11, 4), date(2026, 11, 8), False)


def test_usable_hours_account_for_transfers_and_buffers():
    out = make_leg("BEG", "FCO", datetime(2026, 11, 4, 8, 0), datetime(2026, 11, 4, 9, 0), "40")
    inb = make_leg("FCO", "BEG", datetime(2026, 11, 6, 21, 0), datetime(2026, 11, 6, 22, 30), "40")
    ut = usable_time(out, inb, transfer_minutes=60)
    # Day 1: free from 10:45 to 21:00 = 10.25h; day 2: 08-21 = 13h; day 3: 08:00 to 17:30 = 9.5h
    assert ut.usable_hours == pytest.approx(32.8, abs=0.1)
    assert ut.full_days == 1


def test_late_arrival_note():
    out = make_leg("BEG", "BCN", datetime(2026, 11, 3, 19, 10), datetime(2026, 11, 3, 21, 50), "40")
    inb = make_leg("BCN", "BEG", datetime(2026, 11, 7, 22, 55), datetime(2026, 11, 8, 1, 30), "40")
    ut = usable_time(out, inb)
    assert "Kasni dolazak" in (ut.arrival_note or "")


def test_unknown_arrival_time_is_flagged():
    out = make_leg("BEG", "FCO", datetime(2026, 11, 4, 15, 50), None, "40")
    inb = make_leg("FCO", "BEG", datetime(2026, 11, 8, 18, 10), None, "40")
    assert "nije poznato" in (usable_time(out, inb).arrival_note or "")


def test_return_before_stay_raises():
    out = make_leg("BEG", "FCO", datetime(2026, 11, 4, 3, 0), datetime(2026, 11, 4, 4, 30), "40")
    inb = make_leg("FCO", "BEG", datetime(2026, 11, 3, 18, 0), None, "40")
    with pytest.raises(ValueError):
        stay_dates(out, inb)
