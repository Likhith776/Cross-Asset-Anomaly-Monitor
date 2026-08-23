"""Chart downsampling logic (windowed /chart endpoint)."""

from datetime import datetime, timedelta, timezone

from src.api.main import downsample_points
from src.api.models import ChartPoint


def _points(n, start_min_ago=None):
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=n) if start_min_ago is None else start_min_ago
    return [
        ChartPoint(timestamp=start + timedelta(minutes=i), price=float(i), composite_score=0.1)
        for i in range(n)
    ]


def test_short_series_untouched():
    pts = _points(100)
    assert downsample_points(pts, 1440) is pts


def test_long_series_thinned_and_capped():
    pts = _points(10_000)
    out = downsample_points(pts, 1440)
    assert len(out) <= 1441  # stride sampling + guaranteed latest point
    assert out[-1] is pts[-1]  # latest point always kept


def test_stride_preserves_order():
    pts = _points(5000)
    out = downsample_points(pts, 1000)
    timestamps = [p.timestamp for p in out]
    assert timestamps == sorted(timestamps)
    assert out[0] is pts[0]  # stride 5: first point kept


def test_moderate_series_exact_stride():
    pts = _points(2880)  # stride 2 for cap 1440
    out = downsample_points(pts, 1440)
    # pts[::2] yields 1440 points; the odd-indexed latest is appended
    assert len(out) == 1441
    assert out[0] is pts[0]
    assert out[1] is pts[2]
    assert out[-1] is pts[-1]
