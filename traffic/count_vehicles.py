"""Count cars, motorcycles and trucks crossing a line, and report traffic over time.

Uses Ultralytics YOLO26 (COCO weights) with its ObjectCounter solution. A vehicle is
counted once, when its tracked centre first crosses the line.

Usage:
    python -m traffic.count_vehicles highway.mp4 --start-time 2026-11-08T17:30:26 --site "Kampala Road"
    python -m traffic.count_vehicles rtsp://camera/stream --site "Kampala Road" --report-every 15 --no-video
    python -m traffic.count_vehicles 0 --report-every 5       (0 is the first webcam)

Writes a timestamped event log, a PDF report, a chart and a JSON summary to the output
folder, plus an annotated video unless --no-video is set. Press Ctrl+C to end a live
run; the report is still written. Times are East Africa Time. The line is given as
x1,y1,x2,y2 fractions of the frame; IN means moving down (or right, for a mostly
vertical line) and OUT means moving up (or left).
"""

import argparse
import csv
import json
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import imageio_ffmpeg
from ultralytics import solutions

from .traffic_report import DEFAULT_RECIPIENT, build_report

VEHICLE_CLASSES = {"car": 2, "motorcycle": 3, "truck": 7}  # COCO class IDs
EAT = timezone(timedelta(hours=3), "EAT")  # Uganda's time zone, with no daylight saving
DEFAULT_LINE = "0,0.6,1,0.6"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="video file, stream URL such as rtsp://..., or webcam number")
    parser.add_argument("--line", default=DEFAULT_LINE, help="counting line as x1,y1,x2,y2 frame fractions")
    parser.add_argument("--model", default="yolo26s.pt", help="YOLO weights, downloaded on first use")
    parser.add_argument("--imgsz", type=int, default=960, help="inference image size")
    parser.add_argument("--conf", type=float, default=0.25, help="minimum detection confidence")
    parser.add_argument("--tracker", default="bytetrack.yaml", help="Ultralytics tracker config")
    parser.add_argument("--device", default=None, help="for example cpu or 0; automatic by default")
    parser.add_argument("--output", type=Path, default=Path("output"), help="output folder")
    parser.add_argument("--show", action="store_true", help="show a live preview window")
    parser.add_argument("--no-video", action="store_true", help="skip the annotated video, for long live runs")
    parser.add_argument("--start-time", help="when a recording began in East Africa Time, as YYYY-MM-DDTHH:MM:SS")
    parser.add_argument("--site", default="Unnamed site", help="counting location shown on the report")
    parser.add_argument("--interval", default="auto", help="report period such as 30s, 15min or 1h")
    parser.add_argument("--recipient", default=DEFAULT_RECIPIENT, help="who the report is prepared for")
    parser.add_argument("--report-every", type=float, default=0, help="live only: refresh the report every N minutes")
    return parser.parse_args()


def parse_line(text: str, width: int, height: int) -> list[tuple[int, int]]:
    """Convert 'x1,y1,x2,y2' frame fractions to two pixel points."""
    try:
        x1, y1, x2, y2 = (float(value) for value in text.split(","))
    except ValueError:
        raise ValueError(f"The counting line must be four comma-separated numbers, got: {text}") from None
    if not all(0 <= value <= 1 for value in (x1, y1, x2, y2)) or (x1, y1) == (x2, y2):
        raise ValueError(f"The counting line must be two different points with values from 0 to 1, got: {text}")
    return [(round(x1 * (width - 1)), round(y1 * (height - 1))), (round(x2 * (width - 1)), round(y2 * (height - 1)))]


def now_eat() -> datetime:
    return datetime.now(EAT).replace(tzinfo=None)


def recording_start(source: str, start_time: str | None) -> datetime:
    """When a video file began: start_time if given, otherwise the file's modified time."""
    if start_time:
        try:
            parsed = datetime.fromisoformat(start_time)
        except ValueError:
            raise ValueError(f"The start time must look like 2026-11-08T17:30:26, got: {start_time}") from None
        return parsed.astimezone(EAT).replace(tzinfo=None) if parsed.tzinfo else parsed
    print("No start time given, so report times use the file's modified time and may be wrong.")
    return datetime.fromtimestamp(Path(source).stat().st_mtime, EAT).replace(tzinfo=None)


def new_crossings(classwise: dict, seen: dict):
    """Yield (vehicle class, direction) for each crossing added since the last frame."""
    for name, directions in classwise.items():
        for direction, value in directions.items():
            for _ in range(value - seen.get((name, direction), 0)):
                yield name, direction.lower()
            seen[(name, direction)] = value


def count_traffic(
    source: str,
    output: Path,
    *,
    name: str | None = None,
    line: str = DEFAULT_LINE,
    model: str = "yolo26s.pt",
    imgsz: int = 960,
    conf: float = 0.25,
    tracker: str = "bytetrack.yaml",
    device: str | None = None,
    show: bool = False,
    save_video: bool = True,
    start_time: str | None = None,
    site: str = "Unnamed site",
    interval: str = "auto",
    recipient: str = DEFAULT_RECIPIENT,
    report_every: float = 0,
    on_progress: Callable[[int, int, dict], bool | None] | None = None,
) -> dict:
    """Count vehicles in a video or stream, write the outputs, and return a summary.

    on_progress(frames done, total frames or 0, per-class counts) runs after each frame;
    returning True stops early. Raises ValueError for an unusable source or setting.
    """
    live = source.isdigit() or "://" in source
    capture = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if not capture.isOpened():
        raise ValueError(f"Could not open video source: {source}")

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = 0 if live else int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    region = parse_line(line, width, height)
    started = now_eat() if live else recording_start(source, start_time)
    name = name or (f"live_{started:%Y%m%d_%H%M%S}" if live else Path(source).stem)

    output.mkdir(parents=True, exist_ok=True)
    video_path = output / f"{name}_counted.mp4"
    json_path = output / f"{name}_counts.json"
    events_path = output / f"{name}_events.csv"

    writer = None
    if save_video:
        # H.264 so the result plays in VS Code and browsers; OpenCV's mp4v output does not.
        writer = imageio_ffmpeg.write_frames(
            str(video_path),
            (width, height),
            pix_fmt_in="bgr24",
            fps=fps,
            codec="libx264",
            quality=6,
            macro_block_size=2,
            ffmpeg_log_level="error",
            output_params=["-movflags", "+faststart"],
        )
        writer.send(None)  # start ffmpeg

    counter = solutions.ObjectCounter(
        model=model,
        region=region,
        classes=list(VEHICLE_CLASSES.values()),
        conf=conf,
        imgsz=imgsz,
        tracker=tracker,
        device=device,
        show=show,
        line_width=max(2, width // 640),  # keeps boxes and labels readable on 4K footage
        verbose=False,
    )

    def write_report() -> dict[str, Path]:
        try:
            paths = build_report(events_path, output, site, interval, recipient)
        except OSError as error:  # for example the PDF is open in a viewer that locks it
            print(f"Report not updated: {error}")
            return {}
        print(f"Report updated: {paths['pdf']}", flush=True)
        return paths

    classwise: dict[str, dict[str, int]] = {}
    seen: dict[tuple[str, str], int] = {}
    frames = 0
    moment = started
    last_report = started

    # The log is flushed on every row so a crash or power cut keeps the counts so far.
    with events_path.open("w", newline="", encoding="utf-8") as events_file:
        events = csv.writer(events_file)

        def log(when: datetime, event: str, vehicle_class: str = "", direction: str = "") -> None:
            events.writerow([when.isoformat(), event, vehicle_class, direction, "measured"])
            events_file.flush()

        events.writerow(["timestamp", "event", "vehicle_class", "direction", "data_source"])
        log(started, "session_start")
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    if live:
                        print("Stream ended or was interrupted.")
                    break
                moment = now_eat() if live else started + timedelta(seconds=frames / fps)
                results = counter(frame)
                classwise = results.classwise_count
                for vehicle_class, direction in new_crossings(classwise, seen):
                    log(moment, "vehicle", vehicle_class, direction)
                if writer is not None:
                    writer.send(results.plot_im.tobytes())
                frames += 1
                if frames % 100 == 0:
                    print(f"Processed {frames}/{total_frames or '?'} frames", flush=True)
                if on_progress is not None and on_progress(frames, total_frames, classwise):
                    break
                if live and report_every and moment - last_report >= timedelta(minutes=report_every):
                    log(moment, "checkpoint")  # marks how far monitoring has reached
                    write_report()
                    last_report = moment
        except KeyboardInterrupt:
            print("\nStopping and writing the report.")
        finally:
            capture.release()
            if writer is not None:
                writer.close()
            log(moment if live else started + timedelta(seconds=frames / fps), "session_end")

    counts = {}
    for vehicle in VEHICLE_CLASSES:
        directions = classwise.get(vehicle, {"IN": 0, "OUT": 0})
        counts[vehicle] = {"in": directions["IN"], "out": directions["OUT"], "total": directions["IN"] + directions["OUT"]}

    summary = {
        "video": "live stream" if live else source,  # stream URLs can contain passwords
        "frames": frames,
        "model": model,
        "counts": counts,
        "total": sum(item["total"] for item in counts.values()),
    }
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_report()
    return {
        **summary,
        "events_path": events_path,
        "json_path": json_path,
        "video_path": video_path if save_video else None,
    }


def main() -> None:
    args = parse_args()
    try:
        summary = count_traffic(
            args.source,
            args.output,
            line=args.line,
            model=args.model,
            imgsz=args.imgsz,
            conf=args.conf,
            tracker=args.tracker,
            device=args.device,
            show=args.show,
            save_video=not args.no_video,
            start_time=args.start_time,
            site=args.site,
            interval=args.interval,
            recipient=args.recipient,
            report_every=args.report_every,
        )
    except ValueError as error:
        sys.exit(str(error))

    print(f"\n{'class':<12}{'in':>6}{'out':>6}{'total':>7}")
    for vehicle, item in summary["counts"].items():
        print(f"{vehicle:<12}{item['in']:>6}{item['out']:>6}{item['total']:>7}")
    print(f"{'all':<12}{'':>6}{'':>6}{summary['total']:>7}")
    print(f"\nSaved {summary['events_path']} and {summary['json_path']}")


if __name__ == "__main__":
    main()
