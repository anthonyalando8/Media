"""
Celery tasks for video to MP3 conversion.
Enhanced with better error handling and yt-dlp configuration.
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
    """
    Sanitize filename to be safe for filesystem.
    Removes/replaces invalid characters and limits length.
    """
    # Remove or replace invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = re.sub(r'[\x00-\x1f\x80-\x9f]', '', filename)  # Control characters
    
    # Replace multiple spaces with single space
    filename = re.sub(r'\s+', ' ', filename)
    
    # Remove leading/trailing spaces and dots
    filename = filename.strip('. ')
    
    # Limit length (leave room for timestamp and extension)
    if len(filename) > max_length:
        filename = filename[:max_length].strip()
    
    # If empty after sanitization, return None
    if not filename:
        return None
    
    return filename


def generate_unique_filename(title, file_id):
    """
    Generate a unique filename from video title.
    Format: "Title_YYYYMMDD_HHMMSS_shortid.mp3"
    Falls back to UUID if title is invalid.
    """
    # Sanitize the title
    clean_title = sanitize_filename(title)
    
    if clean_title:
        # Get current timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Get first 8 chars of UUID for uniqueness
        short_id = file_id.split('-')[0]
        
        # Combine: title_timestamp_shortid
        filename = f"{clean_title}_{timestamp}_{short_id}.mp3"
    else:
        # Fallback to UUID-based name
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
        
        # Update Redis every 0.5 seconds to avoid flooding
        if current_time - self.last_update < 0.5:
            return
        
        self.last_update = current_time
        
        status = d.get('status')
        
        if status == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            
            if total > 0:
                percent = int((downloaded / total) * 60) + 20  # 20-80% for download
            else:
                percent = 20
            
            speed = d.get('speed', 0)
            eta = d.get('eta', 0)
            
            update_progress(
                self.task_id,
                'DOWNLOADING',
                percent,
                f'Downloading... {percent - 20}%',
                downloaded=downloaded,
                total=total,
                speed=speed,
                eta=eta
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
    Enhanced with validation stage and better error handling.
    
    Args:
        url: Video URL to download and convert
        file_id: Unique identifier for the output file
        video_title: Original video title (optional, for filename)
    
    Returns:
        dict: Conversion result with file info and status
    """
    task_id = self.request.id
    
    try:
        # Stage 1: Validation (0-10%)
        update_progress(task_id, 'VALIDATING', 0, 'Validating video URL...')
        
        validation_opts = {
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web"],
                    "player_skip": ["webpage", "configs"],
                }
            },
            "geo_bypass": True,
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
            "concurrent_fragment_downloads": 5,
            "http_chunk_size": 10485760,
            "quiet": False,
            "no_warnings": False,
            "verbose": False,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web"],
                    "player_skip": ["webpage", "configs"],
                }
            },
            "geo_bypass": True,
            "format_sort": ["quality", "res", "fps", "hdr:12", "codec:vp9.2", "size", "br", "asr", "proto"],
            "keepvideo": False,
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
        if "Unsupported URL" in error_message or "not a valid URL" in error_message:
            error_message = "The provided URL is not supported. Please use a valid video URL."
        elif "Video unavailable" in error_message:
            error_message = "This video is unavailable, private, or restricted."
        elif "timeout" in error_message.lower():
            error_message = "Connection timeout. Please check your internet and try again."
        
        update_progress(
            task_id,
            'FAILURE',
            0,
            f'Conversion failed: {error_message}'
        )
        
        # Cleanup
        try:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
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
    """
    Periodic task to clean up old converted files.
    Run this via Celery Beat.
    """
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
    """
    Periodic task to update yt-dlp to the latest version.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True,
            text=True
        )
        return f"yt-dlp update: {result.stdout}"
    except Exception as e:
        return f"yt-dlp update failed: {str(e)}"