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

# Setup logger
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
    """Sanitize filename to be safe for filesystem and URLs."""
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = re.sub(r'[\x00-\x1f\x80-\x9f]', '', filename)
    filename = filename.replace(',', '')
    filename = filename.replace(';', '')
    filename = filename.replace('&', 'and')
    filename = filename.replace("'", '')
    filename = filename.replace('"', '')
    filename = re.sub(r'\s+', ' ', filename)
    filename = filename.replace(' ', '_')
    filename = re.sub(r'_+', '_', filename)
    filename = filename.strip('. _-')
    
    if len(filename) > max_length:
        filename = filename[:max_length].strip('_-')
    
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
    """Get optimized yt-dlp options for YouTube."""
    base_opts = {
        "quiet": True if for_validation else False,
        "no_warnings": True if for_validation else False,
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        
        "extractor_args": {
            "youtube": {
                "player_client": ["android_creator", "android", "ios", "mweb"],
                "player_skip": ["webpage"],
                "skip": ["hls", "dash"],
            }
        },
        
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
    """Convert video from URL to MP3 format."""
    task_id = self.request.id
    
    # CRITICAL DEBUG: Check environment at task start
    logger.info("=" * 80)
    logger.info(f"[TASK START] Task ID: {task_id}")
    logger.info(f"[TASK START] File ID: {file_id}")
    logger.info(f"[TASK START] URL: {url}")
    logger.info(f"[TASK START] MEDIA_ROOT from settings: {settings.MEDIA_ROOT}")
    logger.info(f"[TASK START] MEDIA_ROOT exists: {os.path.exists(settings.MEDIA_ROOT)}")
    logger.info(f"[TASK START] MEDIA_ROOT is writable: {os.access(settings.MEDIA_ROOT, os.W_OK)}")
    logger.info(f"[TASK START] Current working directory: {os.getcwd()}")
    
    # List current files in media root
    try:
        files_before = os.listdir(settings.MEDIA_ROOT)
        logger.info(f"[TASK START] Files in MEDIA_ROOT before: {files_before}")
    except Exception as e:
        logger.error(f"[TASK START] Cannot list MEDIA_ROOT: {e}")
    
    logger.info("=" * 80)
    
    try:
        # Stage 1: Validation (0-10%)
        update_progress(task_id, 'VALIDATING', 0, 'Validating video URL...')
        
        validation_opts = get_youtube_options(for_validation=True)
        
        with YoutubeDL(validation_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if info is None:
                raise Exception("Could not extract video information")
            
            duration = info.get("duration", 0)
            title = info.get("title", "Unknown")
            uploader = info.get("uploader", "Unknown")
        
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
        
        logger.info("=" * 80)
        logger.info(f"[PATHS] Generated filename: {final_filename}")
        logger.info(f"[PATHS] Full mp3_path: {mp3_path}")
        logger.info(f"[PATHS] Temp path (without extension): {temp_path}")
        logger.info(f"[PATHS] Expected temp file: {temp_path}.mp3")
        logger.info("=" * 80)
        
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
        
        logger.info(f"[DOWNLOAD] Starting yt-dlp download...")
        logger.info(f"[DOWNLOAD] Output template: {temp_path}.%(ext)s")
        
        with YoutubeDL(download_opts) as ydl:
            ydl.download([url])
        
        logger.info(f"[DOWNLOAD] yt-dlp download completed")
        
        # Stage 4: Finalize (90-100%)
        update_progress(task_id, 'FINALIZING', 90, 'Finalizing...')
        
        # List all files in media directory after download
        logger.info("=" * 80)
        logger.info(f"[AFTER DOWNLOAD] Checking for files...")
        try:
            all_files = os.listdir(settings.MEDIA_ROOT)
            logger.info(f"[AFTER DOWNLOAD] All files in MEDIA_ROOT: {all_files}")
            
            # Look for any files matching our temp pattern
            matching_files = [f for f in all_files if f.startswith(f"temp_{file_id}")]
            logger.info(f"[AFTER DOWNLOAD] Files matching pattern: {matching_files}")
        except Exception as e:
            logger.error(f"[AFTER DOWNLOAD] Cannot list directory: {e}")
        logger.info("=" * 80)
        
        # Check for temp file
        temp_mp3 = temp_path + ".mp3"
        logger.info(f"[FINALIZE] Looking for: {temp_mp3}")
        logger.info(f"[FINALIZE] File exists: {os.path.exists(temp_mp3)}")
        
        if os.path.exists(temp_mp3):
            logger.info(f"[FINALIZE] Found temp file, renaming to: {mp3_path}")
            os.rename(temp_mp3, mp3_path)
            logger.info(f"[FINALIZE] Rename successful")
        else:
            # Maybe it was already converted to mp3_path?
            if os.path.exists(mp3_path):
                logger.info(f"[FINALIZE] File already at final location: {mp3_path}")
            else:
                # List all files to see what actually exists
                try:
                    all_files = os.listdir(settings.MEDIA_ROOT)
                    logger.error(f"[FINALIZE] Expected file not found!")
                    logger.error(f"[FINALIZE] Files in directory: {all_files}")
                except Exception as e:
                    logger.error(f"[FINALIZE] Cannot list directory: {e}")
                
                raise Exception(f"MP3 file was not created. Expected: {temp_mp3}")
        
        # Final verification
        if not os.path.exists(mp3_path):
            raise Exception(f"MP3 file does not exist at: {mp3_path}")
        
        file_size = os.path.getsize(mp3_path)
        if file_size < 1024:
            raise Exception(f"Generated MP3 file is too small: {file_size} bytes")
        
        # List final state
        try:
            final_files = os.listdir(settings.MEDIA_ROOT)
            logger.info(f"[FINALIZE] Final files in MEDIA_ROOT: {final_files}")
        except Exception as e:
            logger.error(f"[FINALIZE] Cannot list MEDIA_ROOT: {e}")
        
        logger.info("=" * 80)
        logger.info(f"[SUCCESS] File created successfully!")
        logger.info(f"[SUCCESS] Path: {mp3_path}")
        logger.info(f"[SUCCESS] Size: {file_size} bytes")
        logger.info(f"[SUCCESS] Filename: {final_filename}")
        logger.info("=" * 80)
        
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
        logger.error("=" * 80)
        logger.error(f"[TASK FAILED] Error: {error_message}")
        logger.error("=" * 80)
        
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
            logger.error(f"Cleanup error: {cleanup_error}")
        
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