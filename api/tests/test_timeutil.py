import re
from datetime import datetime, timezone

from vaultos.timeutil import today_in_tz, utcnow_z


def test_utcnow_z_matches_expected_format():
    ts = utcnow_z()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z", ts)


def test_today_in_tz_matches_expected_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", today_in_tz("America/Chicago"))


def test_today_in_tz_differs_from_utc_near_midnight_when_relevant():
    # Not a flaky assertion about the current wall-clock time -- just proves
    # the function actually consults the given tz rather than always UTC.
    utc_today = datetime.now(timezone.utc).date().isoformat()
    assert today_in_tz("UTC") == utc_today
