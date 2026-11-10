"""Runs vehicle counting in a background thread, one video at a time."""

import shutil
import threading
import time
import traceback

from django.conf import settings
from django.db import close_old_connections, connection

from traffic.count_vehicles import count_traffic
from traffic.traffic_report import VEHICLE_TYPES

from .models import Job

SPEEDS = {  # model weights and inference size
    "fast": ("yolo26n.pt", 640),
    "balanced": ("yolo26s.pt", 960),
    "accurate": ("yolo26m.pt", 1280),
}
PROGRESS_EVERY = 0.5  # seconds between progress writes to the database

_busy = threading.Lock()  # processing fills the CPU, so jobs never overlap
_stop_events: dict[str, threading.Event] = {}


def is_running(job_id) -> bool:
    return str(job_id) in _stop_events


def is_busy() -> bool:
    return _busy.locked()


def start(job: Job, line: str, speed: str) -> bool:
    """Start counting in the background. Returns False if another video is processing."""
    if not _busy.acquire(blocking=False):
        return False
    stop = threading.Event()
    _stop_events[str(job.pk)] = stop
    threading.Thread(target=_run, args=(job.pk, line, speed, stop), daemon=True).start()
    return True


def cancel(job_id) -> None:
    stop = _stop_events.get(str(job_id))
    if stop is not None:
        stop.set()


def _totals(classwise: dict) -> dict:
    return {t: sum(classwise.get(t, {}).values()) for t in VEHICLE_TYPES}


def _run(job_id, line: str, speed: str, stop: threading.Event) -> None:
    close_old_connections()
    try:
        job = Job.objects.get(pk=job_id)
        model, imgsz = SPEEDS[speed]
        settings.WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
        last_write = 0.0

        def on_progress(frames: int, total: int, classwise: dict) -> bool:
            nonlocal last_write
            now = time.monotonic()
            if now - last_write >= PROGRESS_EVERY:
                Job.objects.filter(pk=job_id).update(
                    frames_done=frames, total_frames=total or job.total_frames, counts=_totals(classwise)
                )
                last_write = now
            return stop.is_set()

        summary = count_traffic(
            str(job.source),
            job.results,
            name="result",
            line=line,
            model=str(settings.WEIGHTS_DIR / model),
            imgsz=imgsz,
            start_time=job.settings["start_time"],
            site=job.settings["site"],
            recipient=job.settings["recipient"],
            on_progress=on_progress,
        )
    except ValueError as error:
        Job.objects.filter(pk=job_id).update(status=Job.Status.FAILED, error=str(error))
    except Exception:
        traceback.print_exc()
        Job.objects.filter(pk=job_id).update(
            status=Job.Status.FAILED, error="Processing failed. The terminal running the server has the details."
        )
    else:
        if stop.is_set():
            shutil.rmtree(job.results, ignore_errors=True)
            Job.objects.filter(pk=job_id).update(status=Job.Status.READY, frames_done=0)
        else:
            Job.objects.filter(pk=job_id).update(
                status=Job.Status.DONE,
                frames_done=summary["frames"],
                counts={t: summary["counts"][t]["total"] for t in VEHICLE_TYPES},
            )
    finally:
        _stop_events.pop(str(job_id), None)
        _busy.release()
        connection.close()
