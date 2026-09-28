"""Module 4 - Trend & Anomaly Engine (analysis & visualisation).

Reads the modelspace ledger, aggregates packets per day, and computes 7- and
30-day rolling means / standard deviations for ``intensity`` and
``calculated_tension``. A day is flagged when its mean tension sits more than
``threshold`` standard deviations from the historic baseline:

* **tension spike**    - z >= +threshold
* **pressure valley**  - z <= -threshold

The baseline for each day is the mean/std of all *prior* non-anomalous days:
a day never counts toward its own baseline (no look-ahead), and flagged days
never enter it, so one spike cannot mask the next.

Usage::

    python analytics.py --db data/modelspace.db --plot reports/tension.png
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from database import DEFAULT_DB

log = logging.getLogger("webbot.analytics")

DEFAULT_THRESHOLD = 2.0
MIN_BASELINE_DAYS = 7


def load_ledger(db_path: str | Path = DEFAULT_DB, conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """Return every ledger row ordered chronologically, with a parsed timestamp."""
    query = (
        "SELECT timestamp, source, intensity, immediacy, scale, calculated_tension "
        "FROM modelspace_ledger ORDER BY timestamp ASC"
    )
    if conn is not None:
        df = pd.read_sql_query(query, conn)
    else:
        with sqlite3.connect(db_path) as c:
            df = pd.read_sql_query(query, c)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def compute_trends(
    df: pd.DataFrame,
    threshold: float = DEFAULT_THRESHOLD,
    min_baseline_days: int = MIN_BASELINE_DAYS,
) -> pd.DataFrame:
    """Daily series with rolling stats, baseline z-scores, and anomaly flags."""
    if df.empty:
        return pd.DataFrame()
    daily = (
        df.set_index("timestamp")
        .sort_index()
        .resample("D")
        .agg({"intensity": "mean", "calculated_tension": "mean", "source": "count"})
        .rename(columns={"calculated_tension": "tension", "source": "packets"})
    )
    daily.loc[daily["packets"] == 0, ["intensity", "tension"]] = np.nan

    for col in ("intensity", "tension"):
        for window in (7, 30):
            roll = daily[col].rolling(f"{window}D", min_periods=1)
            daily[f"{col}_ma{window}"] = roll.mean()
            daily[f"{col}_std{window}"] = roll.std()

    # Baseline = prior *ordinary* days only. Flagged days are kept out so one
    # spike can't inflate the std and mask the anomalies that follow it.
    means, stds, zs = [], [], []
    history: list[float] = []
    for value in daily["tension"].to_numpy():
        if len(history) >= min_baseline_days:
            mean, std = float(np.mean(history)), float(np.std(history, ddof=1))
            z = (value - mean) / std if std > 0 and not np.isnan(value) else np.nan
        else:
            mean = std = z = np.nan
        means.append(mean)
        stds.append(std)
        zs.append(z)
        if not np.isnan(value) and not (abs(z) >= threshold):
            history.append(float(value))
    daily["baseline_mean"], daily["baseline_std"], daily["z_score"] = means, stds, zs
    daily["spike"] = daily["z_score"] >= threshold
    daily["valley"] = daily["z_score"] <= -threshold
    return daily


def report_anomalies(daily: pd.DataFrame, threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """Log an alert line per anomalous day and return them as dicts."""
    if daily.empty:
        return []
    alerts = []
    for day, row in daily[daily["spike"] | daily["valley"]].iterrows():
        kind = "TENSION SPIKE" if row["spike"] else "PRESSURE VALLEY"
        alert = {
            "date": day.date().isoformat(),
            "kind": kind,
            "tension": round(float(row["tension"]), 1),
            "baseline_mean": round(float(row["baseline_mean"]), 1),
            "z_score": round(float(row["z_score"]), 2),
            "packets": int(row["packets"]),
        }
        alerts.append(alert)
        log.warning(
            "ALERT %s on %s: tension %.1f vs baseline %.1f (z=%+.2f, %d packets, threshold ±%.1fσ)",
            kind, alert["date"], alert["tension"], alert["baseline_mean"], alert["z_score"],
            alert["packets"], threshold,
        )
    if not alerts:
        log.info("No days beyond ±%.1fσ of baseline", threshold)
    return alerts


# Palette: reference data-viz instance (light surface).
_INK = {"surface": "#fcfcfb", "primary": "#0b0b0b", "secondary": "#52514e",
        "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7"}
_SERIES = {"daily": "#898781", "ma7": "#2a78d6", "ma30": "#eb6834"}
_STATUS = {"spike": "#d03b3b", "valley": "#4a3aa7"}


def plot_trends(daily: pd.DataFrame, output: str | Path) -> Path:
    """Render intensity and tension (two panels, shared time axis) to a PNG."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    plt.rcParams.update({
        "font.size": 10, "axes.edgecolor": _INK["axis"], "axes.labelcolor": _INK["secondary"],
        "xtick.color": _INK["muted"], "ytick.color": _INK["muted"], "text.color": _INK["primary"],
    })
    fig, (ax_i, ax_t) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                     gridspec_kw={"height_ratios": [1, 1.4]})
    fig.patch.set_facecolor(_INK["surface"])

    panels = ((ax_i, "intensity", "Intensity (1–100)"), (ax_t, "tension", "Tension (intensity × immediacy)"))
    for ax, col, title in panels:
        ax.set_facecolor(_INK["surface"])
        ax.plot(daily.index, daily[col], color=_SERIES["daily"], lw=1, alpha=0.8, label="Daily mean")
        ax.plot(daily.index, daily[f"{col}_ma7"], color=_SERIES["ma7"], lw=2, label="7-day average")
        ax.plot(daily.index, daily[f"{col}_ma30"], color=_SERIES["ma30"], lw=2, label="30-day average")
        ax.set_title(title, loc="left", fontsize=11, color=_INK["primary"])
        ax.grid(axis="y", color=_INK["grid"], lw=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.tick_params(axis="y", length=0)

    upper = daily["baseline_mean"] + DEFAULT_THRESHOLD * daily["baseline_std"]
    ax_t.plot(daily.index, upper, color=_INK["muted"], lw=1, ls="--", label="Baseline +2σ")
    spikes, valleys = daily[daily["spike"]], daily[daily["valley"]]
    ax_t.scatter(spikes.index, spikes["tension"], s=64, marker="^", color=_STATUS["spike"],
                 edgecolor=_INK["surface"], linewidth=2, zorder=5, label="Tension spike (▲)")
    ax_t.scatter(valleys.index, valleys["tension"], s=64, marker="v", color=_STATUS["valley"],
                 edgecolor=_INK["surface"], linewidth=2, zorder=5, label="Pressure valley (▼)")
    # Label only the peak day of each run of consecutive spike days.
    run_id = (~daily["spike"]).cumsum()[daily["spike"]]
    peaks = spikes.groupby(run_id)["tension"].idxmax()
    for day in peaks:
        row = spikes.loc[day]
        ax_t.annotate(day.strftime("%b %d"), (day, row["tension"]), textcoords="offset points",
                      xytext=(0, 9), ha="center", fontsize=8, color=_INK["secondary"])

    for ax in (ax_i, ax_t):
        ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False, fontsize=9,
                  labelcolor=_INK["secondary"])
    ax_t.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.suptitle("Modelspace linguistic tension", x=0.01, ha="left", fontsize=13, color=_INK["primary"])
    fig.tight_layout()
    fig.savefig(output, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    log.info("Wrote chart to %s", output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trend and anomaly analysis of the modelspace ledger.")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="σ threshold for alerts")
    parser.add_argument("--min-baseline-days", type=int, default=MIN_BASELINE_DAYS)
    parser.add_argument("--plot", help="Write a PNG chart to this path")
    parser.add_argument("--csv", help="Write the daily trend table to this CSV path")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    df = load_ledger(args.db)
    if df.empty:
        log.warning("Ledger is empty; nothing to analyse")
        return 0
    daily = compute_trends(df, args.threshold, args.min_baseline_days)
    report_anomalies(daily, args.threshold)
    if args.csv:
        daily.to_csv(args.csv)
    if args.plot:
        plot_trends(daily, args.plot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
