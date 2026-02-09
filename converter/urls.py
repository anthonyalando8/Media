"""
URL configuration for video converter app.
"""
from django.urls import path
from . import views

app_name = 'converter'

urlpatterns = [
    # Main page
    path('', views.index, name='index'),
    
    # API endpoints
    path('api/video-info/', views.get_video_info_view, name='get_video_info'),
    path('api/convert/', views.convert_video_view, name='convert_video'),
    path('api/progress/<str:task_id>/', views.check_progress, name='check_progress'),
    path('api/health/', views.health_check, name='health_check'),
    
    # Media serving
    path('media/<str:filename>', views.serve_media, name='serve_media'),
]