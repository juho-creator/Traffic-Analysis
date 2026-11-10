"""Turn a vehicle event log into a traffic report: PDF, chart image and interval table.

Usage:
    python -m traffic.traffic_report output/1_events.csv --site "Kampala Road" --interval 15min

The event log is written by count_vehicles.py or simulate_traffic.py. All times are
East Africa Time. Logs marked SIMULATED are stamped as simulated on every output.
"""

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator

VEHICLE_TYPES = ("car", "motorcycle", "truck")
PLURALS = {"car": "Cars", "motorcycle": "Motorcycles", "truck": "Trucks"}
COLORS = {"car": "#2a78d6", "motorcycle": "#eb6834", "truck": "#1baf7a"}  # order validated for colour-blind readers

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
GRID = "#e4e3df"
BANNER = "#7a1f1f"

DEFAULT_RECIPIENT = "Administrator, Ministry of Works and Transport, Uganda"
AUTO_INTERVALS = (5, 10, 15, 30, 60, 300, 900, 1800, 3600, 7200, 21600, 86400)  # seconds
MAX_AUTO_BARS = 32
MAX_INTERACTIVE_BARS = 120
ROWS_PER_PAGE = 26
SIMULATED_NOTICE = "SIMULATED DATA FOR DEMONSTRATION ONLY - NOT A REAL TRAFFIC MEASUREMENT"


@dataclass
class Bucket:
    start: datetime
    coverage: float
    counts: dict = field(default_factory=lambda: dict.fromkeys(VEHICLE_TYPES, 0))
    directions: dict = field(default_factory=lambda: {"in": 0, "out": 0})

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def partial(self) -> bool:
        return self.coverage < 0.98


def parse_interval(text: str) -> int:
    """Convert '30s', '15min' or '1h' to seconds."""
    match = re.fullmatch(r"(\d+)\s*(s|sec|m|min|h|hr|hour)", text.strip().lower())
    if not match or int(match[1]) == 0:
        raise ValueError(f"interval must look like 30s, 15min or 1h, got: {text}")
    return int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2][0]]


def describe_interval(seconds: int) -> str:
    if seconds >= 3600:
        amount, unit = seconds // 3600, "hour"
    elif seconds >= 60:
        amount, unit = seconds // 60, "minute"
    else:
        amount, unit = seconds, "second"
    return unit if amount == 1 else f"{amount} {unit}s"


def period_name(seconds: int) -> str:
    """Interval as an adjective, for example '30-minute'."""
    words = describe_interval(seconds).split()
    return "-".join((words[0], words[1].removesuffix("s"))) if len(words) == 2 else f"1-{words[0]}"


def load_events(path: Path) -> tuple[datetime, datetime, list[tuple[datetime, str, str]], bool]:
    """Return (monitoring start, end, vehicle rows, simulated?) from an event log."""
    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError(f"No events in {path}")

    times = [datetime.fromisoformat(row["timestamp"]) for row in rows]
    vehicles = [
        (time, row["vehicle_class"], row["direction"])
        for time, row in zip(times, rows)
        if row["event"] == "vehicle"
    ]
    simulated = any(row["data_source"] == "SIMULATED" for row in rows)
    return min(times), max(times), vehicles, simulated


def choose_interval(start: datetime, end: datetime) -> int:
    span = (end - start).total_seconds()
    return next((s for s in AUTO_INTERVALS if span / s <= MAX_AUTO_BARS), AUTO_INTERVALS[-1])


def build_buckets(start: datetime, end: datetime, vehicles: list, interval: int) -> list[Bucket]:
    """Group vehicles into clock-aligned periods, keeping empty ones so gaps show as zero."""
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    index_of = lambda moment: int((moment - midnight).total_seconds() // interval)
    first, last = index_of(start), index_of(end)

    buckets = []
    for index in range(first, last + 1):
        bucket_start = midnight + timedelta(seconds=index * interval)
        bucket_end = bucket_start + timedelta(seconds=interval)
        watched = (min(bucket_end, end) - max(bucket_start, start)).total_seconds()
        buckets.append(Bucket(bucket_start, max(watched, 0) / interval))

    for moment, vehicle_type, direction in vehicles:
        bucket = buckets[index_of(moment) - first]
        bucket.counts[vehicle_type] += 1
        bucket.directions[direction] += 1

    # A session ending exactly on a boundary would otherwise add a final empty period.
    if len(buckets) > 1 and buckets[-1].coverage == 0 and buckets[-1].total == 0:
        buckets.pop()
    return buckets


def find_peak(buckets: list[Bucket]) -> Bucket | None:
    """Busiest period, preferring periods that were watched in full."""
    candidates = [b for b in buckets if not b.partial and b.total] or [b for b in buckets if b.total]
    return max(candidates, key=lambda b: b.total) if candidates else None


def period_label(bucket: Bucket, interval: int) -> str:
    time_format = "%H:%M:%S" if interval < 60 else "%H:%M"
    end = bucket.start + timedelta(seconds=interval)
    return f"{bucket.start.strftime(time_format)}–{end.strftime(time_format)}"


def axis_time_format(buckets: list[Bucket], interval: int) -> str:
    show_date = buckets[0].start.date() != buckets[-1].start.date()
    return ("%d %b " if show_date else "") + ("%H:%M:%S" if interval < 60 else "%H:%M")


def draw_chart(ax, buckets: list[Bucket], interval: int, peak: Bucket | None) -> None:
    """Stacked columns: height is total traffic, colours show the vehicle mix."""
    positions = range(len(buckets))
    bottoms = [0] * len(buckets)
    alphas = [0.4 if b.partial else 1.0 for b in buckets]

    for vehicle_type in VEHICLE_TYPES:
        heights = [b.counts[vehicle_type] for b in buckets]
        bars = ax.bar(
            positions, heights, bottom=bottoms, width=0.72, color=COLORS[vehicle_type],
            edgecolor=SURFACE, linewidth=1.2, label=PLURALS[vehicle_type],
        )
        for bar, alpha in zip(bars, alphas):
            bar.set_alpha(alpha)
        bottoms = [bottom + height for bottom, height in zip(bottoms, heights)]

    top = max(bottoms, default=0)
    ax.set_ylim(0, max(top * 1.15, 5))
    if peak is not None:
        x = buckets.index(peak)
        ax.annotate(f"Busiest: {peak.total:,}", (x, peak.total), xytext=(0, 5), textcoords="offset points",
                    ha="center", va="bottom", fontsize=9, fontweight="bold", color=INK)

    step = max(1, round(len(buckets) / 12))
    tick_format = axis_time_format(buckets, interval)
    ax.set_xticks(list(positions)[::step], [b.start.strftime(tick_format) for b in buckets[::step]])
    ax.set_xlim(-0.7, len(buckets) - 0.3)
    ax.set_xlabel(f"Start of each {period_name(interval)} period (East Africa Time)", color=INK_SECONDARY, fontsize=9)
    ax.set_ylabel("Vehicles counted", color=INK_SECONDARY, fontsize=9)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
    ax.yaxis.set_major_formatter(lambda value, _: f"{int(value):,}")

    ax.set_facecolor(SURFACE)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(colors=INK_SECONDARY, labelsize=8.5, length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)

    ax.legend(loc="upper left", ncols=3, frameon=False, fontsize=9, labelcolor=INK, borderaxespad=0, handlelength=1, handleheight=1)


def draw_summary_page(info: dict, buckets: list[Bucket], interval: int, peak: Bucket | None) -> Figure:
    fig = Figure(figsize=(11.69, 8.27), facecolor=SURFACE)  # A4 landscape
    y = 0.955
    if info["simulated"]:
        fig.add_artist(Rectangle((0, 0.925), 1, 0.06, transform=fig.transFigure, color=BANNER, zorder=-1))
        fig.text(0.5, 0.955, SIMULATED_NOTICE, ha="center", va="center", color="white", fontsize=11, fontweight="bold")
        y = 0.895

    fig.text(0.05, y, f"Traffic count report: {info['site']}", fontsize=19, fontweight="bold", color=INK, va="center")
    fig.text(0.05, y - 0.04, f"Prepared for: {info['recipient']}", fontsize=10, color=INK_SECONDARY, va="center")
    fig.text(0.05, y - 0.07, f"Monitored: {info['period']} (East Africa Time)", fontsize=10, color=INK_SECONDARY, va="center")

    totals = {t: sum(b.counts[t] for b in buckets) for t in VEHICLE_TYPES}
    grand_total = sum(totals.values())
    tiles = [("Total vehicles", f"{grand_total:,}", "")]
    for vehicle_type in VEHICLE_TYPES:
        share = f"{totals[vehicle_type] / grand_total:.0%} of traffic" if grand_total else ""
        tiles.append((PLURALS[vehicle_type], f"{totals[vehicle_type]:,}", share))
    if peak is not None:
        tiles.append(("Busiest period", period_label(peak, interval), f"{peak.total:,} vehicles"))
    else:
        tiles.append(("Busiest period", "None", "no vehicles counted"))

    tile_y = y - 0.13
    x_positions = (0.05, 0.20, 0.35, 0.50, 0.65)
    for x, (label, value, note) in zip(x_positions, tiles):
        fig.text(x, tile_y, label, fontsize=9.5, color=INK_SECONDARY, va="center")
        fig.text(x, tile_y - 0.04, value, fontsize=19, fontweight="bold", color=INK, va="center")
        fig.text(x, tile_y - 0.077, note, fontsize=9, color=INK_SECONDARY, va="center")

    chart_top = tile_y - 0.15
    ax = fig.add_axes((0.07, 0.205, 0.89, chart_top - 0.205 - 0.04))
    fig.text(0.05, chart_top, f"Vehicles per {describe_interval(interval)}", fontsize=13, fontweight="bold", color=INK, va="center")
    draw_chart(ax, buckets, interval, peak)

    notes = [
        "Vehicles are counted automatically from video when they cross a fixed line. Figures are estimates and have not been audited.",
        "Accuracy falls in crowded or low-resolution footage. Buses, bicycles and pedestrians are not counted.",
    ]
    if any(b.partial for b in buckets):
        notes.append("Paler columns were only partly monitored, so they understate traffic for that period.")
    notes.append("Prototype output. Not an official document of the Ministry.")
    for i, note in enumerate(notes):
        fig.text(0.05, 0.115 - i * 0.024, note, fontsize=8.5, color=INK_SECONDARY, va="center")
    return fig


def draw_table_pages(info: dict, buckets: list[Bucket], interval: int) -> list[Figure]:
    """The same figures as the chart, as a table that can be read or quoted exactly."""
    headers = ["Period", "Cars", "Motorcycles", "Trucks", "Total", "In", "Out"]
    pages = []
    chunks = [buckets[i:i + ROWS_PER_PAGE] for i in range(0, len(buckets), ROWS_PER_PAGE)]
    show_date = buckets[0].start.date() != buckets[-1].start.date()

    for number, chunk in enumerate(chunks, start=1):
        fig = Figure(figsize=(11.69, 8.27), facecolor=SURFACE)
        title = f"Counts by {period_name(interval)} period: {info['site']}"
        if info["simulated"]:
            fig.text(0.05, 0.955, SIMULATED_NOTICE, fontsize=10, fontweight="bold", color=BANNER, va="center")
        fig.text(0.05, 0.915, title, fontsize=14, fontweight="bold", color=INK, va="center")
        fig.text(0.95, 0.915, f"Table page {number} of {len(chunks)}", fontsize=9, color=INK_SECONDARY, ha="right", va="center")

        rows = []
        for bucket in chunk:
            label = period_label(bucket, interval)
            if show_date:
                label = f"{bucket.start:%d %b} {label}"
            rows.append(
                [label + (" *" if bucket.partial else "")]
                + [f"{bucket.counts[t]:,}" for t in VEHICLE_TYPES]
                + [f"{bucket.total:,}", f"{bucket.directions['in']:,}", f"{bucket.directions['out']:,}"]
            )

        row_height = 0.029
        height = row_height * (len(rows) + 1)
        ax = fig.add_axes((0.05, 0.885 - height, 0.90, height))
        ax.axis("off")
        table = ax.table(cellText=rows, colLabels=headers, bbox=(0, 0, 1, 1), colWidths=[0.28] + [0.12] * 6, edges="horizontal")
        table.auto_set_font_size(False)
        table.set_fontsize(9.5)
        for (row, column), cell in table.get_celld().items():
            cell.set_edgecolor(GRID)
            cell.set_facecolor(SURFACE)
            cell.set_linewidth(0.6)
            cell.get_text().set_ha("left" if column == 0 else "right")
            cell.PAD = 0.02
            cell.get_text().set_color(INK)
            if row == 0 or column == 4:
                cell.get_text().set_fontweight("bold")

        fig.text(0.05, 0.05, "In = moving down or to the right in the camera view. Out = moving up or to the left."
                 "   * Period only partly monitored.", fontsize=8.5, color=INK_SECONDARY, va="center")
        pages.append(fig)
    return pages


def compute_stats(
    events_path: Path,
    interval: str = "auto",
    direction: str | None = None,
    types: set[str] | None = None,
) -> dict:
    """Figures for the interactive graph, optionally limited to a direction and vehicle types."""
    start, end, vehicles, simulated = load_events(events_path)
    seconds = choose_interval(start, end) if interval == "auto" else parse_interval(interval)
    span = (end - start).total_seconds()
    if span / seconds > MAX_INTERACTIVE_BARS:
        raise ValueError(f"A {period_name(seconds)} period would need more than {MAX_INTERACTIVE_BARS} bars for this video.")

    if direction:
        vehicles = [v for v in vehicles if v[2] == direction]
    type_totals = {t: sum(1 for v in vehicles if v[1] == t) for t in VEHICLE_TYPES}
    if types is not None:
        vehicles = [v for v in vehicles if v[1] in types]

    buckets = build_buckets(start, end, vehicles, seconds)
    peak = find_peak(buckets)
    tick_format = axis_time_format(buckets, seconds)
    options = {s for s in AUTO_INTERVALS if span / s <= MAX_INTERACTIVE_BARS and s <= span} | {choose_interval(start, end)}

    return {
        "simulated": simulated,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "interval_seconds": seconds,
        "interval_name": period_name(seconds),
        "interval_options": [{"value": f"{s}s", "label": describe_interval(s)} for s in sorted(options)],
        "type_totals": type_totals,
        "total": len(vehicles),
        "peak": None if peak is None else {"label": period_label(peak, seconds), "total": peak.total, "index": buckets.index(peak)},
        "buckets": [
            {
                "label": period_label(b, seconds),
                "tick": b.start.strftime(tick_format),
                "offset": max((b.start - start).total_seconds(), 0),
                "counts": b.counts,
                "total": b.total,
                "in": b.directions["in"],
                "out": b.directions["out"],
                "partial": b.partial,
            }
            for b in buckets
        ],
    }


def build_report(
    events_path: Path,
    output_dir: Path | None = None,
    site: str = "Unnamed site",
    interval: str = "auto",
    recipient: str = DEFAULT_RECIPIENT,
) -> dict[str, Path]:
    """Create the PDF report, chart PNG and interval CSV. Returns their paths."""
    start, end, vehicles, simulated = load_events(events_path)
    seconds = choose_interval(start, end) if interval == "auto" else parse_interval(interval)
    buckets = build_buckets(start, end, vehicles, seconds)
    peak = find_peak(buckets)

    output_dir = output_dir or events_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = events_path.stem.removesuffix("_events")
    paths = {
        "pdf": output_dir / f"{stem}_report.pdf",
        "chart": output_dir / f"{stem}_traffic.png",
        "table": output_dir / f"{stem}_intervals.csv",
    }

    end_format = "%d %b %Y %H:%M:%S" if start.date() != end.date() else "%H:%M:%S"
    info = {
        "site": site,
        "recipient": recipient,
        "simulated": simulated,
        "period": f"{start:%d %b %Y %H:%M:%S} to {end.strftime(end_format)}",
    }

    with PdfPages(paths["pdf"]) as pdf:
        pdf.infodict()["Title"] = f"Traffic count report: {site}"
        pdf.savefig(draw_summary_page(info, buckets, seconds, peak))
        for page in draw_table_pages(info, buckets, seconds):
            pdf.savefig(page)

    chart = Figure(figsize=(12, 6), facecolor=SURFACE, layout="constrained")
    ax = chart.add_subplot()
    title = f"Vehicles per {describe_interval(seconds)}: {site}"
    ax.set_title(SIMULATED_NOTICE + "\n" + title if simulated else title, loc="left", fontsize=13, fontweight="bold", color=INK, pad=14)
    draw_chart(ax, buckets, seconds, peak)
    chart.savefig(paths["chart"], dpi=150)

    with paths["table"].open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["period_start", "period_seconds", *VEHICLE_TYPES, "total", "in", "out", "fully_monitored", "data_source"])
        for bucket in buckets:
            writer.writerow([
                bucket.start.isoformat(), seconds, *(bucket.counts[t] for t in VEHICLE_TYPES), bucket.total,
                bucket.directions["in"], bucket.directions["out"], not bucket.partial,
                "SIMULATED" if simulated else "measured",
            ])
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("events", type=Path, help="event log CSV")
    parser.add_argument("--site", default="Unnamed site", help="counting location shown on the report")
    parser.add_argument("--interval", default="auto", help="period length such as 30s, 15min or 1h; automatic by default")
    parser.add_argument("--recipient", default=DEFAULT_RECIPIENT, help="who the report is prepared for")
    parser.add_argument("--output", type=Path, default=None, help="output folder; defaults to the event log's folder")
    args = parser.parse_args()

    if not args.events.is_file():
        sys.exit(f"Event log not found: {args.events}")
    try:
        paths = build_report(args.events, args.output, args.site, args.interval, args.recipient)
    except ValueError as error:
        sys.exit(str(error))
    for path in paths.values():
        print(f"Saved {path}")


if __name__ == "__main__":
    main()
