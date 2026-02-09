"""
API views for video conversion service.
Handles video info extraction and format conversion requests.
"""
import uuid
import logging
from django.shortcuts import render
from django.http import JsonResponse, FileResponse, Http404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django import forms
from .tasks import get_video_info, convert_video_to_format
import os

logger = logging.getLogger(__name__)


# ============================================================================
# FORMS
# ============================================================================

class VideoURLForm(forms.Form):
    """Form for validating video URL input."""
    url = forms.URLField(
        required=True,
        widget=forms.URLInput(attrs={
            'placeholder': 'Paste YouTube or video URL here...',
            'class': 'form-control',
            'id': 'id_url'
        })
    )


class ConvertForm(forms.Form):
    """Form for video conversion with format selection."""
    url = forms.URLField(required=True)
    format = forms.CharField(
        required=False,
        initial='mp3-192'
    )
    title = forms.CharField(
        required=False,
        max_length=200
    )


# ============================================================================
# VIEWS
# ============================================================================

def index(request):
    """
    Main page - Video converter interface.
    """
    form = VideoURLForm()
    return render(request, 'converter/index.html', {'form': form})


@csrf_exempt
@require_http_methods(["POST"])
def get_video_info_view(request):
    """
    API endpoint to get video information and available formats.
    
    This is a fast operation (2-3 seconds) that extracts metadata only.
    No download happens at this stage.
    
    POST /api/video-info/
    Body: { "url": "https://youtube.com/..." }
    
    Returns:
        JSON with video metadata and available format options
    """
    try:
        # Parse request body
        import json
        try:
            data = json.loads(request.body)
            url = data.get('url', '').strip()
        except json.JSONDecodeError:
            url = request.POST.get('url', '').strip()
        
        # Validate URL
        if not url:
            return JsonResponse({
                'status': 'error',
                'error': 'URL is required'
            }, status=400)
        
        # Basic URL validation
        if not (url.startswith('http://') or url.startswith('https://')):
            return JsonResponse({
                'status': 'error',
                'error': 'Invalid URL format. Must start with http:// or https://'
            }, status=400)
        
        logger.info(f"[API] Getting video info for: {url}")
        
        # Call Celery task synchronously (it's fast, 2-3 seconds)
        # For async: result = get_video_info.apply_async(args=[url])
        result = get_video_info(url)
        
        logger.info(f"[API] Video info retrieved: {result.get('title', 'Unknown')}")
        
        return JsonResponse(result, status=200)
        
    except Exception as e:
        error_message = str(e)
        logger.error(f"[API] Get video info failed: {error_message}")
        
        return JsonResponse({
            'status': 'error',
            'error': error_message
        }, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def convert_video_view(request):
    """
    API endpoint to start video conversion with format selection.
    
    POST /api/convert/
    Body: {
        "url": "https://youtube.com/...",
        "format": "m4a",           // Format choice (m4a, mp3-192, video-720p, etc.)
        "title": "Song Name"        // Optional: pre-fetched title
    }
    
    Returns:
        JSON with task_id for progress tracking
    """
    try:
        # Parse request body
        import json
        try:
            data = json.loads(request.body)
            url = data.get('url', '').strip()
            format_choice = data.get('format', 'mp3-192').strip()
            video_title = data.get('title', '').strip() or None
        except json.JSONDecodeError:
            url = request.POST.get('url', '').strip()
            format_choice = request.POST.get('format', 'mp3-192').strip()
            video_title = request.POST.get('title', '').strip() or None
        
        # Validate inputs
        if not url:
            return JsonResponse({
                'status': 'error',
                'error': 'URL is required'
            }, status=400)
        
        if not (url.startswith('http://') or url.startswith('https://')):
            return JsonResponse({
                'status': 'error',
                'error': 'Invalid URL format'
            }, status=400)
        
        # Validate format choice
        valid_formats = [
            'm4a',
            'mp3-128', 'mp3-192', 'mp3-320',
            'video-360p', 'video-480p', 'video-720p', 'video-1080p'
        ]
        
        if format_choice not in valid_formats:
            return JsonResponse({
                'status': 'error',
                'error': f'Invalid format. Must be one of: {", ".join(valid_formats)}'
            }, status=400)
        
        # Generate unique file ID
        file_id = str(uuid.uuid4())
        
        logger.info("=" * 80)
        logger.info(f"[API] Conversion request")
        logger.info(f"[API] URL: {url}")
        logger.info(f"[API] Format: {format_choice}")
        logger.info(f"[API] File ID: {file_id}")
        logger.info(f"[API] Title: {video_title or 'Not provided'}")
        logger.info("=" * 80)
        
        # Start Celery task asynchronously
        task = convert_video_to_format.apply_async(
            args=[url, file_id, format_choice, video_title]
        )
        
        logger.info(f"[API] Task started: {task.id}")
        
        # Determine file extension from format
        if format_choice == 'm4a':
            extension = 'm4a'
        elif format_choice.startswith('mp3-'):
            extension = 'mp3'
        elif format_choice.startswith('video-'):
            extension = 'mp4'
        else:
            extension = 'mp3'
        
        return JsonResponse({
            'status': 'success',
            'task_id': task.id,
            'file_id': file_id,
            'format': format_choice,
            'extension': extension,
            'message': f'Conversion to {extension.upper()} started',
            'title': video_title
        }, status=200)
        
    except Exception as e:
        error_message = str(e)
        logger.error(f"[API] Conversion request failed: {error_message}")
        
        return JsonResponse({
            'status': 'error',
            'error': error_message
        }, status=400)


@csrf_exempt
@require_http_methods(["GET"])
def serve_media(request, filename):
    """
    Serve media files from MEDIA_ROOT.
    Supports all formats: MP3, M4A, MP4, etc.
    
    GET /media/<filename>
    """
    try:
        # Security: Prevent directory traversal
        filename = os.path.basename(filename)
        
        file_path = os.path.join(settings.MEDIA_ROOT, filename)
        
        # Check if file exists
        if not os.path.exists(file_path):
            logger.error(f"[SERVE] File not found: {file_path}")
            logger.error(f"[SERVE] Available files: {os.listdir(settings.MEDIA_ROOT)}")
            raise Http404("File not found")
        
        # Check if it's actually a file (not a directory)
        if not os.path.isfile(file_path):
            raise Http404("Not a file")
        
        # Determine content type based on extension
        extension = os.path.splitext(filename)[1].lower()
        content_types = {
            '.mp3': 'audio/mpeg',
            '.m4a': 'audio/mp4',
            '.mp4': 'video/mp4',
            '.webm': 'video/webm',
        }
        content_type = content_types.get(extension, 'application/octet-stream')
        
        logger.info(f"[SERVE] Serving file: {filename} ({content_type})")
        
        # Open and serve the file
        response = FileResponse(
            open(file_path, 'rb'),
            content_type=content_type
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Content-Length'] = os.path.getsize(file_path)
        
        return response
        
    except Exception as e:
        logger.error(f"[SERVE] Error serving file: {e}")
        raise Http404(f"Error serving file: {str(e)}")


@csrf_exempt
@require_http_methods(["GET"])
def check_progress(request, task_id):
    """
    Check conversion progress (REST API alternative to WebSocket).
    
    GET /api/progress/<task_id>/
    
    Returns:
        JSON with current progress status
    """
    try:
        from .tasks import redis_client
        import json
        
        # Get progress from Redis
        progress_key = f'conversion_progress:{task_id}'
        progress_data = redis_client.get(progress_key)
        
        if not progress_data:
            return JsonResponse({
                'status': 'error',
                'error': 'Task not found or expired'
            }, status=404)
        
        progress = json.loads(progress_data)
        
        return JsonResponse(progress, status=200)
        
    except Exception as e:
        logger.error(f"[PROGRESS] Error checking progress: {e}")
        return JsonResponse({
            'status': 'error',
            'error': str(e)
        }, status=500)


@require_http_methods(["GET"])
def health_check(request):
    """
    Health check endpoint for monitoring.
    
    GET /api/health/
    """
    try:
        # Check Redis connection
        from .tasks import redis_client
        redis_client.ping()
        redis_status = 'ok'
    except Exception as e:
        redis_status = f'error: {str(e)}'
    
    # Check media directory
    try:
        media_writable = os.access(settings.MEDIA_ROOT, os.W_OK)
        media_status = 'ok' if media_writable else 'not writable'
    except Exception as e:
        media_status = f'error: {str(e)}'
    
    overall_status = 'healthy' if redis_status == 'ok' and media_status == 'ok' else 'degraded'
    
    return JsonResponse({
        'status': overall_status,
        'redis': redis_status,
        'media_dir': media_status,
        'version': '2.0.0'
    }, status=200 if overall_status == 'healthy' else 503)