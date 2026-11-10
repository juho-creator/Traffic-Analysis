from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/videos", views.upload_video, name="upload_video"),
    path("api/jobs", views.job_list, name="job_list"),
    path("api/jobs/<uuid:job_id>", views.job_detail, name="job_detail"),
    path("api/jobs/<uuid:job_id>/frame.jpg", views.first_frame, name="first_frame"),
    path("api/jobs/<uuid:job_id>/start", views.start_job, name="start_job"),
    path("api/jobs/<uuid:job_id>/cancel", views.cancel_job, name="cancel_job"),
    path("api/jobs/<uuid:job_id>/delete", views.delete_job, name="delete_job"),
    path("api/jobs/<uuid:job_id>/stats", views.stats, name="stats"),
    path("api/jobs/<uuid:job_id>/video", views.counted_video, name="counted_video"),
    path("api/jobs/<uuid:job_id>/download/<str:kind>", views.download, name="download"),
]
