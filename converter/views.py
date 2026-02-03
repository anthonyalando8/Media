"""
Views for video to MP3 converter.
Production-grade with async task handling and validation.
"""
import uuid
from django.shortcuts import render
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie
from .forms import VideoURLForm
from .tasks import convert_video_to_mp3
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse


@ensure_csrf_cookie
def index(request):
    """Main page with conversion form"""
    form = VideoURLForm()
    return render(request, "converter/index.html", {"form": form})

@csrf_exempt
@require_http_methods(["GET"])
def health_check(request):
    """Health check endpoint for Fly.io"""
    return HttpResponse("OK", status=200)


@require_http_methods(["POST"])
def start_conversion(request):
    """
    API endpoint to start video conversion.
    Returns task_id for progress tracking.
    Minimal validation - heavy lifting done in Celery task.
    """
    form = VideoURLForm(request.POST)
    
    if not form.is_valid():
        return JsonResponse({
            "error": "Invalid form data",
            "details": form.errors
        }, status=400)
    
    url = form.cleaned_data["url"]
    
    # Basic URL validation
    if not url:
        return JsonResponse({
            "error": "URL is required"
        }, status=400)
    
    # Generate unique file ID
    file_id = str(uuid.uuid4())
    
    # Queue Celery task immediately without validation
    # Validation will happen in the background task
    task = convert_video_to_mp3.delay(url, file_id)
    
    return JsonResponse({
        "success": True,
        "task_id": task.id,
        "file_id": file_id,
        "message": "Conversion started"
    })


@require_http_methods(["GET"])
def conversion_status(request, task_id):
    """
    Check the status of a conversion task.
    Alternative to WebSocket for simple polling.
    """
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