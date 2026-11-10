import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone

from traffic.count_vehicles import DEFAULT_LINE
from traffic.traffic_report import DEFAULT_RECIPIENT, VEHICLE_TYPES


def empty_counts() -> dict:
    return dict.fromkeys(VEHICLE_TYPES, 0)


class Job(models.Model):
    """One uploaded video and its analysis."""

    class Status(models.TextChoices):
        READY = "ready", "Ready to count"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    filename = models.CharField(max_length=255)
    source_name = models.CharField(max_length=32)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    fps = models.FloatField()
    total_frames = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.READY)
    frames_done = models.PositiveIntegerField(default=0)
    counts = models.JSONField(default=empty_counts)
    error = models.TextField(blank=True)
    run = models.PositiveIntegerField(default=0)
    settings = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:
        return self.filename

    @property
    def folder(self) -> Path:
        return Path(settings.MEDIA_ROOT) / "jobs" / str(self.pk)

    @property
    def source(self) -> Path:
        return self.folder / self.source_name

    @property
    def results(self) -> Path:
        return self.folder / "results"

    @property
    def events(self) -> Path:
        return self.results / "result_events.csv"

    @property
    def total_vehicles(self) -> int:
        return sum(self.counts.values())

    def to_json(self) -> dict:
        elapsed = (timezone.now() - self.started_at).total_seconds() if self.started_at else 0
        return {
            "id": str(self.pk),
            "created": self.created.isoformat(),
            "status": self.status,
            "filename": self.filename,
            "width": self.width,
            "height": self.height,
            "duration": self.total_frames / self.fps if self.fps else 0,
            "total_frames": self.total_frames,
            "frames_done": self.frames_done,
            "elapsed": elapsed,
            "counts": self.counts,
            "total_vehicles": self.total_vehicles,
            "error": self.error,
            "run": self.run,
            "settings": self.settings,
            "default_line": [float(value) for value in DEFAULT_LINE.split(",")],
            "default_recipient": DEFAULT_RECIPIENT,
        }
