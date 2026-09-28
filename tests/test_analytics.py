from datetime import datetime, timedelta

import pandas as pd

import analytics
import database
import pipeline


def _ledger(values):
    start = datetime(2026, 1, 1)
    return pd.DataFrame({
        "timestamp": [start + timedelta(days=i) for i in range(len(values))],
        "source": "s", "intensity": 50, "immediacy": 50, "scale": 50,
        "calculated_tension": values,
    })


def test_flags_spike_and_valley_without_lookahead():
    values = [1000, 1010, 990, 1005, 995, 1000, 1002, 998, 1001, 999, 5000, 1000, 10]
    daily = analytics.compute_trends(_ledger(values))
    assert daily["spike"].tolist().index(True) == 10
    assert daily["spike"].sum() == 1
    assert bool(daily["valley"].iloc[-1])
    # first MIN_BASELINE_DAYS days have no baseline, so they can never be flagged
    assert not daily["spike"].iloc[: analytics.MIN_BASELINE_DAYS].any()
    alerts = analytics.report_anomalies(daily)
    assert [a["kind"] for a in alerts] == ["TENSION SPIKE", "PRESSURE VALLEY"]


def test_rolling_columns_present():
    daily = analytics.compute_trends(_ledger([100.0] * 40))
    for col in ("intensity_ma7", "intensity_ma30", "tension_ma7", "tension_ma30", "tension_std7", "tension_std30"):
        assert col in daily
    assert daily["tension_ma30"].iloc[-1] == 100.0


def test_end_to_end_demo(tmp_path):
    db = tmp_path / "demo.db"
    database.commit_packets(pipeline.synthetic_packets(days=60, per_day=10), db)
    daily = analytics.compute_trends(analytics.load_ledger(db))
    assert len(daily) >= 59
    assert daily["spike"].any()
    png = analytics.plot_trends(daily, tmp_path / "chart.png")
    assert png.stat().st_size > 10_000
