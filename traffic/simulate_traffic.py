"""Generate a made-up day of traffic to preview the report format.

Every row is marked SIMULATED, and the report prints that notice on every page.
Replace it with a real log from traffic.count_vehicles before sending anything.

Usage:
    python -m traffic.simulate_traffic
    python -m traffic.traffic_report output/demo_events.csv --site "Demo site" --interval 30min
"""

import argparse
import csv
from datetime import date, datetime, timedelta
from math import exp
from pathlib import Path

import numpy as np

MIX = (("motorcycle", 0.55), ("car", 0.35), ("truck", 0.10))


def vehicles_per_hour(hour: float) -> float:
    """Invented daily shape with morning and evening rush hours."""
    rush = 900 * exp(-((hour - 8.0) ** 2) / 1.5) + 1100 * exp(-((hour - 17.75) ** 2) / 2.0)
    return 350 + rush


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="YYYY-MM-DD")
    parser.add_argument("--output", type=Path, default=Path("output/demo_events.csv"))
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    start = datetime.combine(args.date, datetime.min.time()) + timedelta(hours=6)
    minutes = 16 * 60
    types = [name for name, _ in MIX]
    shares = [share for _, share in MIX]

    rows = []
    for minute in range(minutes):
        hour = 6 + minute / 60
        for _ in range(rng.poisson(vehicles_per_hour(hour) / 60)):
            moment = start + timedelta(minutes=minute, seconds=float(rng.uniform(0, 60)))
            direction = "in" if rng.random() < 0.5 else "out"
            rows.append([moment.isoformat(), "vehicle", str(rng.choice(types, p=shares)), direction, "SIMULATED"])
    rows.sort()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["timestamp", "event", "vehicle_class", "direction", "data_source"])
        writer.writerow([start.isoformat(), "session_start", "", "", "SIMULATED"])
        writer.writerows(rows)
        writer.writerow([(start + timedelta(minutes=minutes)).isoformat(), "session_end", "", "", "SIMULATED"])
    print(f"Saved {len(rows):,} simulated vehicles to {args.output}")


if __name__ == "__main__":
    main()
