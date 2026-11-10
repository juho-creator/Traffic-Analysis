import shutil
import tempfile
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .models import Job

EVENTS = """timestamp,event,vehicle_class,direction,data_source
2026-11-08T08:00:00,session_start,,,measured
2026-11-08T08:00:01,vehicle,car,in,measured
2026-11-08T08:00:03,vehicle,car,out,measured
2026-11-08T08:00:07,vehicle,truck,in,measured
2026-11-08T08:00:12,vehicle,motorcycle,in,measured
2026-11-08T08:00:14,session_end,,,measured
"""


def make_video(path: Path, frames: int = 8) -> bytes:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    for i in range(frames):
        writer.write(np.full((48, 64, 3), i * 20, dtype=np.uint8))
    writer.release()
    return path.read_bytes()


class CounterViewTests(TestCase):
    def setUp(self):
        self.media = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)

    def upload(self, name="road.mp4", content=None):
        if content is None:
            content = make_video(self.media / "sample.mp4")
        return self.client.post("/api/videos", {"video": SimpleUploadedFile(name, content, content_type="video/mp4")})

    def finished_job(self) -> Job:
        job = Job.objects.create(
            filename="road.mp4", source_name="source.mp4", width=64, height=48, fps=10, total_frames=140,
            status=Job.Status.DONE, counts={"car": 2, "motorcycle": 1, "truck": 1},
            settings={"site": "Test road", "recipient": "Someone", "start_time": "2026-11-08T08:00:00", "speed": "fast", "line": [0, 0.6, 1, 0.6]},
        )
        job.results.mkdir(parents=True)
        job.events.write_text(EVENTS, encoding="utf-8")
        (job.results / "result_counted.mp4").write_bytes(bytes(range(256)) * 4)
        return job

    def test_index_sets_csrf_cookie(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)
        self.assertContains(response, "Drop a road video here")

    def test_post_without_csrf_token_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post("/api/videos", {"video": SimpleUploadedFile("a.mp4", b"x")})
        self.assertEqual(response.status_code, 403)

    def test_upload_creates_job_with_first_frame(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data["width"], data["height"], data["status"]), (64, 48, "ready"))
        job = Job.objects.get(pk=data["id"])
        self.assertTrue((job.folder / "frame.jpg").is_file())
        self.assertEqual(self.client.get(f"/api/jobs/{job.pk}/frame.jpg").status_code, 200)

    def test_upload_rejects_unreadable_and_unsupported_files(self):
        response = self.upload(content=b"not a video")
        self.assertEqual(response.status_code, 400)
        self.assertIn("could not be read", response.json()["error"])
        self.assertEqual(self.upload(name="notes.txt", content=b"x").status_code, 400)
        self.assertFalse(Job.objects.exists())

    def test_start_validates_settings(self):
        job_id = self.upload().json()["id"]
        url = f"/api/jobs/{job_id}/start"
        good = {"site": "A", "start_time": "2026-11-08T08:00:00", "speed": "fast", "line": [0, 0.6, 1, 0.6]}
        for bad in ({"start_time": ""}, {"speed": "turbo"}, {"line": [0, 2, 1, 0.5]}, {"line": [0.5, 0.5, 0.5, 0.5]}, {"line": "x"}):
            response = self.client.post(url, {**good, **bad}, content_type="application/json")
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(self.client.post(url, b"[1]", content_type="application/json").status_code, 400)

        with mock.patch("counter.views.processing.start", return_value=True) as start:
            response = self.client.post(url, good, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processing")
        start.assert_called_once()

    def test_start_refused_while_another_video_processes(self):
        job_id = self.upload().json()["id"]
        good = {"site": "Refused", "start_time": "2026-11-08T08:00:00", "line": [0, 0.6, 1, 0.6]}
        with mock.patch("counter.views.processing.is_busy", return_value=True):
            response = self.client.post(f"/api/jobs/{job_id}/start", good, content_type="application/json")
        self.assertEqual(response.status_code, 409)
        with mock.patch("counter.views.processing.start", return_value=False):
            response = self.client.post(f"/api/jobs/{job_id}/start", good, content_type="application/json")
        self.assertEqual(response.status_code, 409)
        job = Job.objects.get(pk=job_id)
        self.assertEqual((job.status, job.settings, job.run), (Job.Status.READY, {}, 0))

    def test_processing_job_without_a_worker_is_marked_failed(self):
        job = Job.objects.create(filename="a.mp4", source_name="source.mp4", width=1, height=1, fps=1, status=Job.Status.PROCESSING, started_at=timezone.now())
        data = self.client.get(f"/api/jobs/{job.pk}").json()
        self.assertEqual(data["status"], "failed")
        self.assertIn("restarted", data["error"])

    def test_stats_and_filters(self):
        job = self.finished_job()
        data = self.client.get(f"/api/jobs/{job.pk}/stats?interval=5s").json()
        self.assertEqual(data["total"], 4)
        self.assertEqual([b["total"] for b in data["buckets"]], [2, 1, 1])
        self.assertEqual(data["peak"]["total"], 2)
        self.assertEqual(self.client.get(f"/api/jobs/{job.pk}/stats?direction=out").json()["total"], 1)
        filtered = self.client.get(f"/api/jobs/{job.pk}/stats?types=truck").json()
        self.assertEqual((filtered["total"], filtered["type_totals"]["car"]), (1, 2))
        self.assertEqual(self.client.get(f"/api/jobs/{job.pk}/stats?interval=banana").status_code, 400)

    def test_results_need_a_finished_job(self):
        job = self.finished_job()
        Job.objects.filter(pk=job.pk).update(status=Job.Status.READY)
        self.assertEqual(self.client.get(f"/api/jobs/{job.pk}/stats").status_code, 404)

    def test_video_supports_range_requests(self):
        job = self.finished_job()
        url = f"/api/jobs/{job.pk}/video"
        full = self.client.get(url)
        self.assertEqual((full.status_code, full["Accept-Ranges"]), (200, "bytes"))
        part = self.client.get(url, HTTP_RANGE="bytes=10-19")
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part["Content-Range"], "bytes 10-19/1024")
        self.assertEqual(b"".join(part.streaming_content), bytes(range(10, 20)))
        self.assertEqual(self.client.get(url, HTTP_RANGE="bytes=-4").status_code, 206)
        self.assertEqual(self.client.get(url, HTTP_RANGE="bytes=5000-").status_code, 416)
        self.assertIn("attachment", self.client.get(url + "?download=1")["Content-Disposition"])

    def test_downloads(self):
        job = self.finished_job()
        pdf = self.client.get(f"/api/jobs/{job.pk}/download/pdf?interval=5s&inline=1")
        self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(pdf["Content-Disposition"].startswith("inline"))
        table = self.client.get(f"/api/jobs/{job.pk}/download/table?interval=5s")
        self.assertIn('filename="road_intervals.csv"', table["Content-Disposition"])
        self.assertEqual(b"".join(table.streaming_content).decode().strip().count("\n"), 3)
        self.assertEqual(self.client.get(f"/api/jobs/{job.pk}/download/chart").status_code, 200)
        self.assertEqual(self.client.get(f"/api/jobs/{job.pk}/download/other").status_code, 404)

    def test_list_and_delete(self):
        job = self.finished_job()
        jobs = self.client.get("/api/jobs").json()["jobs"]
        self.assertEqual([j["id"] for j in jobs], [str(job.pk)])
        self.assertEqual(jobs[0]["total_vehicles"], 4)
        self.assertEqual(self.client.post(f"/api/jobs/{job.pk}/delete").status_code, 200)
        self.assertFalse(job.folder.exists())
        self.assertFalse(Job.objects.exists())
        self.assertEqual(self.client.get(f"/api/jobs/{job.pk}").status_code, 404)

    def test_get_only_and_post_only_endpoints(self):
        job = self.finished_job()
        self.assertEqual(self.client.get(f"/api/jobs/{job.pk}/delete").status_code, 405)
        self.assertEqual(self.client.post(f"/api/jobs/{job.pk}/stats").status_code, 405)
