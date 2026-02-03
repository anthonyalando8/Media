from django.urls import path
from .views import index, start_conversion, conversion_status

urlpatterns = [
    path("", index, name="home"),
    path("api/convert/", start_conversion, name="start_conversion"),
    path("api/status/<str:task_id>/", conversion_status, name="conversion_status"),
]