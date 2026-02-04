"""
Views for video to MP3 converter.
Production-grade with async task handling and validation.
"""
import os
import uuid
from django.shortcuts import render
from django.conf import settings
from django.http import JsonResponse, HttpResponse, FileResponse, Http404
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from .forms import VideoURLForm
from .tasks import convert_video_to_mp3


@ensure_csrf_cookie
def index(request):
    """Main page with conversion form"""
    form = VideoURLForm()
    return render(request, "converter/index.html", {"form": form})


@csrf_exempt
@require_http_methods(["GET"])
def health_check(request):
    """Health check endpoint for Render"""
    return HttpResponse("OK", status=200)


@require_http_methods(["POST"])
def start_conversion(request):
    """
    API endpoint to start video conversion.
    Returns task_id for progress tracking.
    """
    form = VideoURLForm(request.POST)
    
    if not form.is_valid():
        return JsonResponse({
            "error": "Invalid form data",
            "details": form.errors
        }, status=400)
    
    url = form.cleaned_data["url"]
    
    if not url:
        return JsonResponse({
            "error": "URL is required"
        }, status=400)
    
    file_id = str(uuid.uuid4())
    
    # Queue Celery task
    task = convert_video_to_mp3.delay(url, file_id)
    
    return JsonResponse({
        "success": True,
        "task_id": task.id,
        "file_id": file_id,
        "message": "Conversion started"
    })


@require_http_methods(["GET"])
def conversion_status(request, task_id):
    """Check the status of a conversion task."""
    from celery.result import AsyncResult
    import redis
    import json
    
    # Try to get detailed progress from Redis first
    try:
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        progress_key = f'conversion_progress:{task_id}'
        progress_data = redis_client.get(progress_key)
        
        if progress_data:
            return JsonResponse(json.loads(progress_data))
    except Exception as e:
        print(f"Redis error: {e}")
    
    # Fallback to Celery state
    result = AsyncResult(task_id)
    
    response_data = {
        "state": result.state,
        "status": result.status,
        "percent": 0,
        "message": "Processing..."
    }
    
    if result.ready():
        if result.successful():
            response_data.update({
                "state": "SUCCESS",
                "percent": 100,
                "result": result.result
            })
        else:
            response_data.update({
                "state": "FAILURE",
                "percent": 0,
                "error": str(result.info)
            })
    
    return JsonResponse(response_data)


@csrf_exempt
@require_http_methods(["GET"])
def serve_media(request, filename):
    """
    Serve media files from MEDIA_ROOT.
    CSRF exempt to allow direct downloads.
    """
    try:
        # Security: Prevent directory traversal
        filename = os.path.basename(filename)
        
        file_path = os.path.join(settings.MEDIA_ROOT, filename)
        
        # Check if file exists
        if not os.path.exists(file_path):
            print(f"[ERROR] File not found: {file_path}")
            print(f"[DEBUG] MEDIA_ROOT: {settings.MEDIA_ROOT}")
            print(f"[DEBUG] Files in MEDIA_ROOT: {os.listdir(settings.MEDIA_ROOT)}")
            raise Http404("File not found")
        
        # Check if it's actually a file
        if not os.path.isfile(file_path):
            raise Http404("Not a file")
        
        print(f"[SUCCESS] Serving file: {file_path}")
        
        # Open and serve the file
        response = FileResponse(
            open(file_path, 'rb'),
            content_type='audio/mpeg'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Content-Length'] = os.path.getsize(file_path)
        
        return response
        
    except Exception as e:
        print(f"[ERROR] Error serving media file: {e}")
        raise Http404(f"Error serving file: {str(e)}")