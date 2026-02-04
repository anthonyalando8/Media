"""
Celery tasks for video to MP3 conversion.
Enhanced with YouTube bot detection bypass.
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
        3600,  # Expire after 1 hour
        json.dumps(progress_data)
    )


def sanitize_filename(filename, max_length=100):
    """Sanitize filename to be safe for filesystem."""
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = re.sub(r'[\x00-\x1f\x80-\x9f]', '', filename)
    filename = re.sub(r'\s+', ' ', filename)
    filename = filename.strip('. ')
    
    if len(filename) > max_length:
        filename = filename[:max_length].strip()
    
    if not filename:
        return None
    
    return filename


def generate_unique_filename(title, file_id):
    """Generate a unique filename from video title."""
    clean_title = sanitize_filename(title)
    
    if clean_title:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = file_id.split('-')[0]
        filename = f"{clean_title}_{timestamp}_{short_id}.mp3"
    else:
        filename = f"audio_{file_id}.mp3"
    
    return filename


class ProgressHook:
    """Custom progress hook for yt-dlp to track download/conversion progress"""
    
    def __init__(self, task_id):
        self.task_id = task_id
        self.last_update = 0
    
    def __call__(self, d):
        """Called by yt-dlp with progress information"""
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


@shared_task(bind=True, name='converter.convert_video_to_mp3', max_retries=3)
def convert_video_to_mp3(self, url, file_id, video_title=None):
    """
    Convert video from URL to MP3 format.
    Enhanced with YouTube bot detection bypass.
    """
    task_id = self.request.id
    
    try:
        # Stage 1: Validation (0-10%)
        update_progress(task_id, 'VALIDATING', 0, 'Validating video URL...')
        
        # Enhanced validation options to bypass bot detection
        validation_opts = {
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
            "extractor_args": {
                "youtube": {
                    # Use multiple client types for better success
                    "player_client": ["ios", "android", "web", "mweb"],
                    "player_skip": ["webpage", "configs"],
                    # Skip signature verification that might fail
                    "skip": ["dash", "hls"],
                }
            },
            "geo_bypass": True,
            # Add user agent to appear more like a real browser
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-us,en;q=0.5",
                "Sec-Fetch-Mode": "navigate",
            }
        }
        
        # Validate and extract info
        with YoutubeDL(validation_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if info is None:
                raise Exception("Could not extract video information from URL")
            
            duration = info.get("duration", 0)
            title = info.get("title", "Unknown")
            uploader = info.get("uploader", "Unknown")
        
        # Check duration limit
        if duration > settings.MAX_VIDEO_DURATION:
            raise Exception(
                f"Video is too long ({duration // 60} minutes). "
                f"Maximum allowed is {settings.MAX_VIDEO_DURATION // 60} minutes."
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
        
        # Stage 2: Prepare download (10-20%)
        update_progress(task_id, 'PREPARING', 15, 'Preparing download...')
        
        # Generate final filename
        final_filename = generate_unique_filename(title, file_id)
        mp3_path = os.path.join(settings.MEDIA_ROOT, final_filename)
        temp_path = os.path.join(settings.MEDIA_ROOT, f"temp_{file_id}")
        
        # Stage 3: Download and Convert (20-90%)
        update_progress(task_id, 'DOWNLOADING', 20, 'Starting download...')
        
        # Enhanced download options to bypass bot detection
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": temp_path + ".%(ext)s",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": settings.AUDIO_FORMAT,
                "preferredquality": settings.AUDIO_QUALITY,
            }],
            "progress_hooks": [ProgressHook(task_id)],
            "socket_timeout": 30,
            "retries": 10,
            "fragment_retries": 10,
            "skip_unavailable_fragments": False,
            "concurrent_fragment_downloads": 3,
            "http_chunk_size": 10485760,
            "quiet": False,
            "no_warnings": False,
            "verbose": False,
            
            # CRITICAL: Enhanced extractor arguments to bypass bot detection
            "extractor_args": {
                "youtube": {
                    # Try multiple client types in order
                    "player_client": ["ios", "android", "web", "tv_embedded", "mweb"],
                    "player_skip": ["webpage", "configs"],
                    "skip": ["dash", "hls"],
                    # Use iOS client for better reliability
                    "innertube_client": "ios",
                }
            },
            
            # Add browser-like headers
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-us,en;q=0.5",
                "Sec-Fetch-Mode": "navigate",
            },
            
            "geo_bypass": True,
            "format_sort": ["quality", "res", "fps", "codec:avc", "size", "br", "asr", "proto"],
            "keepvideo": False,
            
            # Disable age gate checks
            "age_limit": None,
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Stage 4: Finalize (90-100%)
        update_progress(task_id, 'FINALIZING', 90, 'Finalizing...')
        
        # Find and rename the extracted audio file
        temp_mp3 = temp_path + ".mp3"
        
        if os.path.exists(temp_mp3):
            os.rename(temp_mp3, mp3_path)
        elif not os.path.exists(mp3_path):
            raise Exception("MP3 file was not created. Conversion failed.")
        
        # Verify output
        if not os.path.exists(mp3_path):
            raise Exception("MP3 file was not created. Conversion failed.")
        
        file_size = os.path.getsize(mp3_path)
        if file_size < 1024:
            raise Exception("Generated MP3 file is too small. Conversion may have failed.")
        
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
        
        # User-friendly error messages
        if "Sign in to confirm" in error_message or "bot" in error_message.lower():
            error_message = "YouTube is blocking automated downloads. Please try a different video or try again later."
        elif "Unsupported URL" in error_message:
            error_message = "The provided URL is not supported. Please use a valid video URL."
        elif "Video unavailable" in error_message:
            error_message = "This video is unavailable, private, or restricted."
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
                temp_patterns = [
                    temp_path + ".mp3",
                    temp_path + ".mp4",
                    temp_path + ".webm",
                    temp_path + ".m4a",
                    temp_path + ".part",
                ]
                for temp_file in temp_patterns:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
        except Exception as cleanup_error:
            print(f"Cleanup error: {cleanup_error}")
        
        # Retry for network errors
        if "fragment" in error_message.lower() or "network" in error_message.lower():
            retry_count = self.request.retries
            if retry_count < self.max_retries:
                countdown = 5 * (2 ** retry_count)
                raise self.retry(exc=e, countdown=countdown)
        
        raise


@shared_task(name='converter.cleanup_old_files')
def cleanup_old_files():
    """Periodic task to clean up old converted files."""
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
    """Periodic task to update yt-dlp to the latest version."""
    import subprocess
    try:
        result = subprocess.run(
            ["pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True,
            text=True,
            timeout=60
        )
        return f"yt-dlp update: {result.stdout}"
    except Exception as e:
        return f"yt-dlp update failed: {str(e)}"