from django.urls import path
from .views import index, serve_media, start_conversion, conversion_status, health_check

urlpatterns = [
    path("", index, name="home"),
    path("health/", health_check, name="health_check"),
    path("api/convert/", start_conversion, name="start_conversion"),
    path("api/status/<str:task_id>/", conversion_status, name="conversion_status"),
    path("media/<str:filename>", serve_media, name="serve_media"),  # Add this
]