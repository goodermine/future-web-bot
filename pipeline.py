"""End-to-end orchestrator: spiders -> quantifier -> ledger -> trend engine.

Usage::

    python pipeline.py run --sources sources.txt            # one full cycle
    python pipeline.py demo                                  # synthetic 90-day history + chart
    python pipeline.py analyze --plot reports/tension.png   # analysis only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import analytics
import database
import ingest
import processor

log = logging.getLogger("webbot.pipeline")


def run_cycle(
    urls: list[str],
    db_path: str,
    backend: str = "auto",
    min_intensity: int = 40,
    raw_out: str | None = None,
    plot: str | None = None,
) -> dict:
    records = asyncio.run(ingest.ingest(urls))
    log.info("Ingested %d records from %d sources", len(records), len(urls))
    if raw_out:
        Path(raw_out).parent.mkdir(parents=True, exist_ok=True)
        Path(raw_out).write_text(json.dumps(records, indent=2, ensure_ascii=False))

    scored = processor.process_records(records, backend, min_intensity)
    inserted = database.commit_packets(scored, db_path)
    alerts = analyze(db_path, plot)
    return {"ingested": len(records), "scored": len(scored), "inserted": inserted, "alerts": alerts}


def analyze(db_path: str, plot: str | None = None, threshold: float = analytics.DEFAULT_THRESHOLD) -> list[dict]:
    df = analytics.load_ledger(db_path)
    if df.empty:
        log.warning("Ledger is empty; nothing to analyse")
        return []
    daily = analytics.compute_trends(df, threshold)
    alerts = analytics.report_anomalies(daily, threshold)
    if plot:
        analytics.plot_trends(daily, plot)
    return alerts


def synthetic_packets(days: int = 90, per_day: int = 40, seed: int = 7) -> list[dict]:
    """Plausible noisy baseline with two injected tension build-ups and one lull."""
    rng = np.random.default_rng(seed)
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    events = {int(days * 0.55): 28, int(days * 0.85): 34}  # day -> intensity boost
    lull = int(days * 0.7)
    packets = []
    for d in range(days):
        boost = sum(max(0, b - 6 * abs(d - day)) for day, b in events.items() if d <= day)
        boost -= 22 if d == lull else 0
        for i in range(per_day):
            ts = start + timedelta(days=d, seconds=int(rng.integers(0, 86400)))
            packets.append({
                "timestamp": ts.isoformat(),
                "source": f"synthetic://feed/{i % 5}",
                "headline": f"synthetic packet {d}-{i}",
                "intensity": int(np.clip(rng.normal(38 + boost, 12), 1, 100)),
                "immediacy": int(np.clip(rng.normal(40 + boost * 0.8, 12), 1, 100)),
                "scale": int(np.clip(rng.normal(45, 15), 1, 100)),
                "method": "synthetic",
            })
    return packets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Predictive linguistics engine pipeline")
    parser.add_argument("--db", default=database.DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Ingest, score, store, and analyse once")
    run.add_argument("urls", nargs="*")
    run.add_argument("--sources", default=None, help="File with one URL per line")
    run.add_argument("--backend", choices=["lexical", "claude", "auto"], default="auto")
    run.add_argument("--min-intensity", type=int, default=40,
                     help="Lexical intensity a packet needs before it is sent to Claude")
    run.add_argument("--raw-out", default=None, help="Also save raw ingested JSON here")
    run.add_argument("--plot", default="reports/tension.png")

    demo = sub.add_parser("demo", help="Fill a separate DB with synthetic history and chart it")
    demo.add_argument("--demo-db", default="data/demo.db")
    demo.add_argument("--days", type=int, default=90)
    demo.add_argument("--plot", default="reports/demo_tension.png")

    an = sub.add_parser("analyze", help="Run trend analysis on the ledger")
    an.add_argument("--threshold", type=float, default=analytics.DEFAULT_THRESHOLD)
    an.add_argument("--plot", default=None)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "run":
        urls = list(args.urls) + (ingest.load_sources(args.sources) if args.sources else [])
        if not urls:
            parser.error("provide URLs or --sources")
        summary = run_cycle(urls, args.db, args.backend, args.min_intensity, args.raw_out, args.plot)
        print(json.dumps(summary, indent=2))
    elif args.command == "demo":
        Path(args.demo_db).unlink(missing_ok=True)
        database.commit_packets(synthetic_packets(args.days), args.demo_db)
        alerts = analyze(args.demo_db, args.plot)
        print(json.dumps(alerts, indent=2))
    else:
        alerts = analyze(args.db, args.plot, args.threshold)
        print(json.dumps(alerts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
