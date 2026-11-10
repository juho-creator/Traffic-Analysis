import json
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

import cv2
from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from traffic.count_vehicles import parse_line
from traffic.traffic_report import DEFAULT_RECIPIENT, VEHICLE_TYPES, build_report, compute_stats

from . import processing
from .models import Job, empty_counts

VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".ts"}
DOWNLOADS = {
    "pdf": ("_report.pdf", "application/pdf"),
    "chart": ("_traffic.png", "image/png"),
    "table": ("_intervals.csv", "text/csv"),
}
RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)$")
BUSY_MESSAGE = "Another video is still being processed. Wait for it to finish."
report_lock = threading.Lock()  # reports for one job share file names


def error_response(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def get_job(job_id) -> Job:
    """Fetch a job, marking it failed if its processing thread no longer exists (server restart)."""
    job = get_object_or_404(Job, pk=job_id)
    if job.status == Job.Status.PROCESSING and not processing.is_running(job.pk):
        job.status = Job.Status.FAILED
        job.error = "Processing stopped because the server restarted. Start it again."
        job.save(update_fields=["status", "error"])
    return job


def finished_job(job_id) -> Job:
    job = get_job(job_id)
    if job.status != Job.Status.DONE:
        raise Http404
    return job


def safe_stem(filename: str) -> str:
    stem = re.sub(r"[^\w.-]+", "_", Path(filename).stem).strip("._")
    return stem or "traffic"


def ranged_file_response(request, path: Path, content_type: str, filename: str, as_attachment: bool):
    """Serve a file with HTTP range support, so the video player can jump to any moment."""
    size = path.stat().st_size
    match = RANGE_PATTERN.match(request.headers.get("Range", "").strip())
    if match and (match[1] or match[2]):
        if match[1]:
            start, end = int(match[1]), int(match[2]) if match[2] else size - 1
        else:
            start, end = max(size - int(match[2]), 0), size - 1
        end = min(end, size - 1)
        if start > end:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response

        def chunks(file, remaining: int):
            with file:
                file.seek(start)
                while remaining > 0:
                    data = file.read(min(remaining, 1024 * 1024))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        response = StreamingHttpResponse(chunks(path.open("rb"), end - start + 1), status=206, content_type=content_type)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = str(end - start + 1)
        response["Content-Disposition"] = content_disposition_header(as_attachment, filename)
    else:
        response = FileResponse(path.open("rb"), content_type=content_type, as_attachment=as_attachment, filename=filename)
    response["Accept-Ranges"] = "bytes"
    return response


@require_GET
@ensure_csrf_cookie
def index(request):
    return render(request, "counter/index.html")


@require_POST
def upload_video(request):
    if int(request.META.get("CONTENT_LENGTH") or 0) > settings.MAX_VIDEO_BYTES:
        return error_response("That video is larger than the 16 GB limit.", 413)
    upload = request.FILES.get("video")
    if upload is None or not upload.name:
        return error_response("No video was received.")
    suffix = Path(upload.name).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        return error_response(f"This file type is not supported. Use one of: {', '.join(sorted(VIDEO_SUFFIXES))}.")

    job = Job(filename=Path(upload.name).name[:255], source_name=f"source{suffix}", width=0, height=0, fps=0)
    job.folder.mkdir(parents=True)
    with job.source.open("wb") as file:
        for chunk in upload.chunks():
            file.write(chunk)

    capture = cv2.VideoCapture(str(job.source))
    ok, frame = capture.read()
    job.width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    job.height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    job.fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    job.total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    if not ok:
        shutil.rmtree(job.folder, ignore_errors=True)
        return error_response("That file could not be read as a video.")

    cv2.imwrite(str(job.folder / "frame.jpg"), frame)
    job.save()
    return JsonResponse(job.to_json())


@require_GET
def job_list(request):
    jobs = [get_job(job.pk) for job in Job.objects.all()[:20]]
    return JsonResponse({"jobs": [job.to_json() for job in jobs]})


@require_GET
def job_detail(request, job_id):
    return JsonResponse(get_job(job_id).to_json())


@require_GET
def first_frame(request, job_id):
    return FileResponse((get_job(job_id).folder / "frame.jpg").open("rb"), content_type="image/jpeg")


@require_POST
def start_job(request, job_id):
    job = get_job(job_id)
    if job.status == Job.Status.PROCESSING:
        return error_response("This video is already being processed.", 409)
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("The request could not be read.")
    if not isinstance(data, dict):
        return error_response("The request could not be read.")

    speed = data.get("speed", "balanced")
    if speed not in processing.SPEEDS:
        return error_response("Choose a valid speed.")
    start_time = str(data.get("start_time", "")).strip()
    try:
        datetime.fromisoformat(start_time)
    except ValueError:
        return error_response("Enter when the recording began.")
    try:
        values = [float(value) for value in data.get("line", [])]
        line = ",".join(str(value) for value in values)
        parse_line(line, job.width, job.height)
    except (TypeError, ValueError):
        return error_response("Place the counting line inside the frame, with its two ends apart.")
    if processing.is_busy():
        return error_response(BUSY_MESSAGE, 409)

    previous = (job.settings, job.run)
    job.settings = {
        "site": str(data.get("site", "")).strip()[:120] or "Unnamed site",
        "recipient": str(data.get("recipient", "")).strip()[:200] or DEFAULT_RECIPIENT,
        "start_time": start_time,
        "speed": speed,
        "line": values,
    }
    shutil.rmtree(job.results, ignore_errors=True)
    job.status, job.error, job.frames_done = Job.Status.PROCESSING, "", 0
    job.counts = empty_counts()
    job.started_at = timezone.now()
    job.run += 1
    job.save()
    if not processing.start(job, line, speed):  # another start won the race since the check above
        job.settings, job.run = previous
        job.status, job.started_at = Job.Status.READY, None
        job.save()
        return error_response(BUSY_MESSAGE, 409)
    return JsonResponse(job.to_json())


@require_POST
def cancel_job(request, job_id):
    processing.cancel(get_job(job_id).pk)
    return JsonResponse({"ok": True})


@require_POST
def delete_job(request, job_id):
    job = get_job(job_id)
    if job.status == Job.Status.PROCESSING:
        return error_response("Cancel processing before deleting this analysis.", 409)
    shutil.rmtree(job.folder, ignore_errors=True)
    job.delete()
    return JsonResponse({"ok": True})


@require_GET
def stats(request, job_id):
    job = finished_job(job_id)
    direction = request.GET.get("direction")
    types = request.GET.get("types")
    try:
        data = compute_stats(
            job.events,
            request.GET.get("interval", "auto"),
            direction if direction in ("in", "out") else None,
            None if types is None else set(types.split(",")) & set(VEHICLE_TYPES),
        )
    except ValueError as error:
        return error_response(str(error))
    return JsonResponse(data)


@require_GET
def counted_video(request, job_id):
    job = finished_job(job_id)
    path = job.results / "result_counted.mp4"
    if not path.is_file():
        raise Http404
    return ranged_file_response(
        request, path, "video/mp4", f"{safe_stem(job.filename)}_counted.mp4", as_attachment="download" in request.GET
    )


@require_GET
def download(request, job_id, kind: str):
    job = finished_job(job_id)
    if kind not in DOWNLOADS:
        raise Http404
    suffix, content_type = DOWNLOADS[kind]
    try:
        with report_lock:
            build_report(job.events, job.results, job.settings["site"], request.GET.get("interval", "auto"), job.settings["recipient"])
    except ValueError as error:
        return error_response(str(error))
    response = FileResponse(
        (job.results / f"result{suffix}").open("rb"),
        content_type=content_type,
        as_attachment="inline" not in request.GET,
        filename=f"{safe_stem(job.filename)}{suffix}",
    )
    response["Cache-Control"] = "no-store"
    return response
