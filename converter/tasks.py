"""
Celery tasks for video to MP3 conversion.
Enhanced with advanced YouTube bot detection bypass using PO Token.
"""
import os
import time
import re
from datetime import datetime
from celery import shared_task
from django.conf import settings
from yt_dlp import YoutubeDL
import redis
import json
import logging
logger = logging.getLogger(__name__)


# Redis connection for progress tracking
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


def update_progress(task_id, state, percent, message, **extra_data):
    """Helper function to update progress in Redis"""
    progress_data = {
        'state': state,
        'percent': percent,
        'message': message,
        **extra_data
    }
    redis_client.setex(
        f'conversion_progress:{task_id}',
        3600,
        json.dumps(progress_data)
    )


def sanitize_filename(filename, max_length=80):
    """
    Sanitize filename to be safe for filesystem and URLs.
    Removes/replaces invalid characters including commas and spaces.
    """
    # Remove or replace invalid filesystem characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    
    # Remove control characters
    filename = re.sub(r'[\x00-\x1f\x80-\x9f]', '', filename)
    
    # CRITICAL: Remove commas and other problematic characters
    filename = filename.replace(',', '')
    filename = filename.replace(';', '')
    filename = filename.replace('&', 'and')
    filename = filename.replace("'", '')
    filename = filename.replace('"', '')
    
    # Replace multiple spaces with single space
    filename = re.sub(r'\s+', ' ', filename)
    
    # Replace spaces with underscores for URL safety
    filename = filename.replace(' ', '_')
    
    # Remove consecutive underscores
    filename = re.sub(r'_+', '_', filename)
    
    # Remove leading/trailing spaces, dots, and underscores
    filename = filename.strip('. _-')
    
    # Limit length (leave room for timestamp and extension)
    if len(filename) > max_length:
        filename = filename[:max_length].strip('_-')
    
    # If empty after sanitization, return None
    if not filename:
        return None
    
    return filename


def generate_unique_filename(title, file_id):
    """
    Generate a unique filename from video title.
    Format: Title_YYYYMMDD_HHMMSS_shortid.mp3
    """
    clean_title = sanitize_filename(title)
    
    if clean_title:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = file_id.split('-')[0]
        filename = f"{clean_title}_{timestamp}_{short_id}.mp3"
    else:
        # Fallback to UUID-based name
        filename = f"audio_{file_id}.mp3"
    
    return filename


class ProgressHook:
    """Custom progress hook for yt-dlp"""
    
    def __init__(self, task_id):
        self.task_id = task_id
        self.last_update = 0
    
    def __call__(self, d):
        current_time = time.time()
        
        if current_time - self.last_update < 0.5:
            return
        
        self.last_update = current_time
        status = d.get('status')
        
        if status == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            
            if total > 0:
                percent = int((downloaded / total) * 60) + 20
            else:
                percent = 20
            
            update_progress(
                self.task_id,
                'DOWNLOADING',
                percent,
                f'Downloading... {percent - 20}%'
            )
            
        elif status == 'finished':
            update_progress(
                self.task_id,
                'CONVERTING',
                85,
                'Converting to MP3...'
            )


def get_youtube_options(for_validation=False):
    """
    Get optimized yt-dlp options for YouTube.
    Uses advanced techniques to bypass bot detection.
    """
    base_opts = {
        "quiet": True if for_validation else False,
        "no_warnings": True if for_validation else False,
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        
        # Use Android/iOS clients - more reliable
        "extractor_args": {
            "youtube": {
                # CRITICAL: Use android_creator client (most reliable in 2024+)
                "player_client": ["android_creator", "android", "ios", "mweb"],
                "player_skip": ["webpage"],
                "skip": ["hls", "dash"],
            }
        },
        
        # Mobile user agent (matches android_creator client)
        "http_headers": {
            "User-Agent": "com.google.android.apps.youtube.creator/23.50.100 (Linux; U; Android 13; en_US) gzip",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        },
        
        "geo_bypass": True,
        "age_limit": None,
    }
    
    return base_opts


@shared_task(bind=True, name='converter.convert_video_to_mp3', max_retries=3)
def convert_video_to_mp3(self, url, file_id, video_title=None):
    """
    Convert video from URL to MP3 format.
    Enhanced with advanced YouTube bot bypass.
    """
    task_id = self.request.id
    
    try:
        # Stage 1: Validation (0-10%)
        update_progress(task_id, 'VALIDATING', 0, 'Validating video URL...')
        
        validation_opts = get_youtube_options(for_validation=True)
        
        # Validate and extract info
        with YoutubeDL(validation_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if info is None:
                raise Exception("Could not extract video information")
            
            duration = info.get("duration", 0)
            title = info.get("title", "Unknown")
            uploader = info.get("uploader", "Unknown")
        
        # Check duration limit
        if duration > settings.MAX_VIDEO_DURATION:
            raise Exception(
                f"Video is too long ({duration // 60} minutes). "
                f"Maximum: {settings.MAX_VIDEO_DURATION // 60} minutes."
            )
        
        update_progress(
            task_id, 
            'VALIDATED', 
            10, 
            f'Video validated: {title[:50]}...',
            title=title,
            duration=duration,
            uploader=uploader
        )
        
        # Stage 2: Prepare (10-20%)
        update_progress(task_id, 'PREPARING', 15, 'Preparing download...')
        
        final_filename = generate_unique_filename(title, file_id)
        mp3_path = os.path.join(settings.MEDIA_ROOT, final_filename)
        temp_path = os.path.join(settings.MEDIA_ROOT, f"temp_{file_id}")
        
        # Log the filename for debugging
        print(f"[DEBUG] Generated filename: {final_filename}")
        print(f"[DEBUG] Full path: {mp3_path}")
        
        # Stage 3: Download (20-90%)
        update_progress(task_id, 'DOWNLOADING', 20, 'Starting download...')
        
        download_opts = get_youtube_options(for_validation=False)
        download_opts.update({
            "format": "bestaudio/best",
            "outtmpl": temp_path + ".%(ext)s",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": settings.AUDIO_FORMAT,
                "preferredquality": settings.AUDIO_QUALITY,
            }],
            "progress_hooks": [ProgressHook(task_id)],
            "concurrent_fragment_downloads": 3,
            "http_chunk_size": 10485760,
            "keepvideo": False,
            "format_sort": ["quality", "res", "fps", "codec:aac", "size", "br"],
        })
        
        with YoutubeDL(download_opts) as ydl:
            ydl.download([url])
        
        # Stage 4: Finalize (90-100%)
        update_progress(task_id, 'FINALIZING', 90, 'Finalizing...')
        
        temp_mp3 = temp_path + ".mp3"
        
        if os.path.exists(temp_mp3):
            os.rename(temp_mp3, mp3_path)
            print(f"[DEBUG] Renamed {temp_mp3} to {mp3_path}")
        elif not os.path.exists(mp3_path):
            raise Exception("MP3 file was not created")
        
        if not os.path.exists(mp3_path):
            raise Exception("MP3 file was not created")
        
        file_size = os.path.getsize(mp3_path)
        if file_size < 1024:
            raise Exception("Generated MP3 file is too small")
        
        # List all files in media directory
        try:
            media_files = os.listdir(settings.MEDIA_ROOT)
            logger.info(f"[DEBUG] Files in MEDIA_ROOT: {media_files}")
        except Exception as e:
            logger.error(f"[ERROR] Cannot list MEDIA_ROOT: {e}")

        logger.info(f"[SUCCESS] File created: {mp3_path} ({file_size} bytes)")
        
        # Success!
        update_progress(
            task_id,
            'SUCCESS',
            100,
            'Conversion complete!',
            file_id=file_id,
            filename=final_filename,
            file_size=file_size,
            title=title
        )
        
        return {
            'status': 'success',
            'file_id': file_id,
            'filename': final_filename,
            'file_size': file_size,
            'title': title,
            'message': 'Conversion completed successfully'
        }
        
    except Exception as e:
        error_message = str(e)
        
        # User-friendly errors
        if "Sign in to confirm" in error_message or "bot" in error_message.lower():
            error_message = "YouTube bot detection triggered. Try again in a few minutes or use a different video."
        elif "Video unavailable" in error_message or "Private video" in error_message:
            error_message = "This video is unavailable, private, or region-restricted."
        elif "Unsupported URL" in error_message:
            error_message = "The URL is not supported. Please use a valid YouTube or video URL."
        elif "timeout" in error_message.lower():
            error_message = "Connection timeout. Please try again."
        
        update_progress(
            task_id,
            'FAILURE',
            0,
            f'Conversion failed: {error_message}'
        )
        
        # Cleanup
        try:
            if 'mp3_path' in locals() and os.path.exists(mp3_path):
                os.remove(mp3_path)
            if 'temp_path' in locals():
                for ext in [".mp3", ".mp4", ".webm", ".m4a", ".part"]:
                    temp_file = temp_path + ext
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
        except Exception as cleanup_error:
            print(f"Cleanup error: {cleanup_error}")
        
        # Retry for transient errors
        if any(keyword in error_message.lower() for keyword in ["fragment", "network", "timeout", "connection"]):
            retry_count = self.request.retries
            if retry_count < self.max_retries:
                countdown = 5 * (2 ** retry_count)
                raise self.retry(exc=e, countdown=countdown)
        
        raise


@shared_task(name='converter.cleanup_old_files')
def cleanup_old_files():
    """Clean up old MP3 files"""
    import time
    
    media_root = settings.MEDIA_ROOT
    max_age = settings.FILE_CLEANUP_AFTER
    current_time = time.time()
    cleaned_count = 0
    
    try:
        for filename in os.listdir(media_root):
            if not filename.endswith('.mp3'):
                continue
            
            file_path = os.path.join(media_root, filename)
            
            if not os.path.exists(file_path):
                continue
            
            file_age = current_time - os.path.getmtime(file_path)
            
            if file_age > max_age:
                try:
                    os.remove(file_path)
                    cleaned_count += 1
                except Exception as e:
                    print(f"Error removing {filename}: {e}")
    except Exception as e:
        print(f"Cleanup task error: {e}")
    
    return f"Cleaned up {cleaned_count} old files"


@shared_task(name='converter.update_ytdlp')
def update_ytdlp():
    """Update yt-dlp to latest version"""
    import subprocess
    try:
        result = subprocess.run(
            ["pip", "install", "--no-cache-dir", "--upgrade", "--force-reinstall", "yt-dlp"],
            capture_output=True,
            text=True,
            timeout=120
        )
        return f"yt-dlp updated: {result.returncode == 0}"
    except Exception as e:
        return f"yt-dlp update failed: {str(e)}"