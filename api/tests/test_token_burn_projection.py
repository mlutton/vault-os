from datetime import datetime, timedelta, timezone

from vaultos.vault.metrics import project_trend


def test_project_trend_extrapolates_linear_series():
    t0 = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    samples = [
        (t0, 0.0),
        (t0 + timedelta(minutes=10), 10.0),
        (t0 + timedelta(minutes=20), 20.0),
        (t0 + timedelta(minutes=30), 30.0),
    ]
    # slope = 1.0 per minute; +1h horizon from the last sample (t0+30min) => +60
    projected = project_trend(samples, timedelta(hours=1))
    assert projected == 90.0


def test_project_trend_none_with_fewer_than_two_samples():
    t0 = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    assert project_trend([], timedelta(hours=1)) is None
    assert project_trend([(t0, 5.0)], timedelta(hours=1)) is None


def test_project_trend_flat_series_projects_flat():
    t0 = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    samples = [(t0, 5.0), (t0 + timedelta(minutes=10), 5.0), (t0 + timedelta(minutes=20), 5.0)]
    assert project_trend(samples, timedelta(hours=1)) == 5.0
