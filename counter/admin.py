from django.contrib import admin

from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("filename", "site", "status", "total_vehicles", "created")
    list_filter = ("status",)
    search_fields = ("filename", "settings")
    readonly_fields = [field.name for field in Job._meta.fields]

    @admin.display(description="Site")
    def site(self, job: Job) -> str:
        return job.settings.get("site", "")

    def has_add_permission(self, request) -> bool:
        return False  # jobs are created by uploading a video in the app
