"""
Celery tasks for video conversion with multi-format support.
Enhanced with YouTube bot detection bypass and accurate progress tracking.
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


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def update_progress(task_id, state, percent, message, **extra_data):
    """
    Update task progress in Redis for real-time WebSocket updates.
    
    Args:
        task_id: Celery task ID
        state: Current state (VALIDATING, DOWNLOADING, CONVERTING, etc.)
        percent: Progress percentage (0-100)
        message: Human-readable progress message
        **extra_data: Additional data to include in progress update
    """
    percent = max(0, min(100, int(percent)))
    
    progress_data = {
        'state': state,
        'percent': percent,
        'message': message,
        **extra_data
    }
    
    logger.info(f"[PROGRESS] {percent}% - {state} - {message}")
    
    redis_client.setex(
        f'conversion_progress:{task_id}',
        3600,  # 1 hour TTL
        json.dumps(progress_data)
    )


def sanitize_filename(filename, max_length=80):
    """
    Sanitize filename to be safe for filesystem and URLs.
    
    Args:
        filename: Original filename
        max_length: Maximum filename length
        
    Returns:
        Sanitized filename string or None if empty
    """
    # Remove invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = re.sub(r'[\x00-\x1f\x80-\x9f]', '', filename)
    
    # Replace special characters
    replacements = {
        ',': '', ';': '', '&': 'and', "'": '', '"': ''
    }
    for old, new in replacements.items():
        filename = filename.replace(old, new)
    
    # Normalize whitespace and underscores
    filename = re.sub(r'\s+', ' ', filename)
    filename = filename.replace(' ', '_')
    filename = re.sub(r'_+', '_', filename)
    filename = filename.strip('. _-')
    
    # Truncate if too long
    if len(filename) > max_length:
        filename = filename[:max_length].strip('_-')
    
    return filename if filename else None


def generate_unique_filename(title, file_id, extension):
    """
    Generate a unique filename with timestamp and short ID.
    
    Args:
        title: Video title
        file_id: Unique file identifier
        extension: File extension (mp3, m4a, mp4, etc.)
        
    Returns:
        Unique filename string
    """
    clean_title = sanitize_filename(title)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = file_id.split('-')[0]
    
    if clean_title:
        return f"{clean_title}_{timestamp}_{short_id}.{extension}"
    else:
        return f"media_{file_id}.{extension}"


def format_file_size(bytes_size):
    """
    Format bytes to human-readable size.
    
    Args:
        bytes_size: Size in bytes
        
    Returns:
        Formatted string (e.g., "3.5 MB") or None
    """
    if not bytes_size:
        return None
    
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f} KB"
    else:
        return f"{bytes_size / (1024 * 1024):.1f} MB"


def parse_available_formats(info_dict):
    """
    Parse yt-dlp info dict and return user-friendly format options.
    
    Args:
        info_dict: yt-dlp video info dictionary
        
    Returns:
        Dictionary with 'audio' and 'video' format lists
    """
    formats = info_dict.get('formats', [])
    audio_formats = []
    video_formats = []
    seen_audio = set()
    seen_video = set()
    
    # Parse available formats from yt-dlp
    for fmt in formats:
        ext = fmt.get('ext', '')
        acodec = fmt.get('acodec', 'none')
        vcodec = fmt.get('vcodec', 'none')
        abr = fmt.get('abr', 0)
        filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
        height = fmt.get('height', 0)
        
        # Audio-only formats (M4A - native, no conversion)
        if acodec != 'none' and vcodec == 'none' and ext == 'm4a' and 'm4a' not in seen_audio:
            audio_formats.append({
                'id': 'm4a',
                'label': f'M4A ({int(abr)}kbps)' if abr else 'M4A',
                'ext': 'm4a',
                'quality': int(abr) if abr else 128,
                'size_estimate': filesize,
                'speed_badge': '⚡ Fastest',
                'description': 'Direct download, no conversion',
                'conversion_time': '~8 sec'
            })
            seen_audio.add('m4a')
        
        # Video formats
        if vcodec != 'none' and height > 0:
            quality_key = f'video-{height}p'
            if quality_key not in seen_video and height in [360, 480, 720, 1080]:
                video_formats.append({
                    'id': quality_key,
                    'label': f'MP4 {height}p',
                    'ext': 'mp4',
                    'quality': height,
                    'size_estimate': filesize,
                    'speed_badge': '🎥 Video',
                    'description': f'{height}p video quality',
                    'conversion_time': '~30-60 sec'
                })
                seen_video.add(quality_key)
    
    # Always add standard MP3 options (require FFmpeg conversion)
    mp3_options = [
        {
            'id': 'mp3-128',
            'label': 'MP3 (128kbps)',
            'ext': 'mp3',
            'quality': 128,
            'size_estimate': None,
            'speed_badge': '💾 Small',
            'description': 'Standard quality, smaller file',
            'conversion_time': '~20 sec'
        },
        {
            'id': 'mp3-192',
            'label': 'MP3 (192kbps)',
            'ext': 'mp3',
            'quality': 192,
            'size_estimate': None,
            'speed_badge': '⭐ Recommended',
            'description': 'Good balance of quality and size',
            'conversion_time': '~25 sec'
        },
        {
            'id': 'mp3-320',
            'label': 'MP3 (320kbps)',
            'ext': 'mp3',
            'quality': 320,
            'size_estimate': None,
            'speed_badge': '🎵 Best Quality',
            'description': 'Maximum MP3 quality',
            'conversion_time': '~30 sec'
        }
    ]
    
    return {
        'audio': audio_formats + mp3_options,
        'video': sorted(video_formats, key=lambda x: x['quality'])
    }


def get_youtube_options(for_validation=False):
    """
    Get optimized yt-dlp options with YouTube bot detection bypass.
    
    Args:
        for_validation: If True, use quiet mode for fast metadata extraction
        
    Returns:
        Dictionary of yt-dlp options
    """
    return {
        "quiet": for_validation,
        "no_warnings": for_validation,
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


def build_ytdlp_options(format_choice, base_opts=None):
    """
    Build yt-dlp options based on user's format choice.
    
    Args:
        format_choice: Format ID like "m4a", "mp3-192", "video-720p"
        base_opts: Optional base options to extend
        
    Returns:
        Tuple of (yt-dlp options dict, file extension string)
    """
    if base_opts is None:
        base_opts = get_youtube_options(for_validation=False)
    
    opts = base_opts.copy()
    
    # M4A: Direct download, NO conversion ⚡
    if format_choice == 'm4a':
        opts.update({
            "format": "bestaudio[ext=m4a]/bestaudio",
            "postprocessors": [],
        })
        extension = 'm4a'
    
    # MP3: Download + FFmpeg conversion
    elif format_choice.startswith('mp3-'):
        quality = format_choice.split('-')[1]
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": quality,
            }],
        })
        extension = 'mp3'
    
    # Video: Download video with audio
    elif format_choice.startswith('video-'):
        quality = format_choice.split('-')[1]
        height = quality.replace('p', '')
        opts.update({
            "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
            "postprocessors": [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }],
            "merge_output_format": "mp4",
        })
        extension = 'mp4'
    
    # Default fallback: MP3 192kbps
    else:
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
        extension = 'mp3'
    
    return opts, extension


def handle_download_error(error_message):
    """
    Convert technical errors to user-friendly messages.
    
    Args:
        error_message: Original error message
        
    Returns:
        User-friendly error message string
    """
    error_lower = error_message.lower()
    
    if "sign in to confirm" in error_message or "bot" in error_lower:
        return "YouTube bot detection triggered. Try again in a few minutes or use a different video."
    elif "video unavailable" in error_message or "private video" in error_message:
        return "This video is unavailable, private, or region-restricted."
    elif "unsupported url" in error_message:
        return "The URL is not supported. Please use a valid video URL."
    elif "timeout" in error_lower:
        return "Connection timeout. Please try again."
    
    return error_message


def is_transient_error(error_message):
    """
    Check if error is transient and should be retried.
    
    Args:
        error_message: Error message string
        
    Returns:
        Boolean indicating if error is transient
    """
    transient_keywords = ["fragment", "network", "timeout", "connection"]
    return any(keyword in error_message.lower() for keyword in transient_keywords)


# ============================================================================
# PROGRESS HOOK
# ============================================================================

class ProgressHook:
    """Custom progress hook for yt-dlp with smooth progress tracking."""
    
    def __init__(self, task_id, format_choice='mp3-192'):
        self.task_id = task_id
        self.format_choice = format_choice
        self.last_update = 0
        self.last_percent = 15
    
    def __call__(self, d):
        current_time = time.time()
        
        # Throttle updates to every 0.3 seconds
        if current_time - self.last_update < 0.3:
            return
        
        self.last_update = current_time
        status = d.get('status')
        
        if status == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            
            if total > 0:
                download_percent = (downloaded / total) * 100
                # Map to 15-75% range (60% for download phase)
                percent = int(15 + (download_percent * 0.6))
                percent = max(self.last_percent, percent)  # Never go backwards
                self.last_percent = percent
            else:
                percent = 15
            
            # Format-specific messages
            if self.format_choice == 'm4a':
                message = f'Downloading M4A... {int((downloaded / total) * 100) if total > 0 else 0}%'
            else:
                message = f'Downloading... {int((downloaded / total) * 100) if total > 0 else 0}%'
            
            update_progress(
                self.task_id,
                'DOWNLOADING',
                percent,
                message
            )
        
        elif status == 'finished':
            # Download finished
            if self.format_choice == 'm4a':
                update_progress(self.task_id, 'FINALIZING', 75, 'Download complete!')
            else:
                update_progress(self.task_id, 'CONVERTING', 75, 'Download complete, converting...')


# ============================================================================
# CELERY TASKS
# ============================================================================

@shared_task(bind=True, name='converter.get_video_info', max_retries=2)
def get_video_info(self, url):
    """
    Extract video information and available formats WITHOUT downloading.
    Fast operation (2-3 seconds) for metadata only.
    
    Args:
        url: Video URL
        
    Returns:
        Dictionary with video info and available formats
    """
    task_id = self.request.id
    
    logger.info("=" * 80)
    logger.info(f"[GET INFO] Task ID: {task_id}")
    logger.info(f"[GET INFO] URL: {url}")
    logger.info("=" * 80)
    
    try:
        validation_opts = get_youtube_options(for_validation=True)
        
        with YoutubeDL(validation_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if info is None:
                raise Exception("Could not extract video information")
        
        # Extract metadata
        video_id = info.get('id', '')
        title = info.get('title', 'Unknown')
        duration = info.get('duration', 0)
        uploader = info.get('uploader', 'Unknown')
        thumbnail = info.get('thumbnail', '')
        description = info.get('description', '')[:200]
        
        # Validate duration
        if duration > settings.MAX_VIDEO_DURATION:
            raise Exception(
                f"Video is too long ({duration // 60} minutes). "
                f"Maximum: {settings.MAX_VIDEO_DURATION // 60} minutes."
            )
        
        # Parse formats
        available_formats = parse_available_formats(info)
        
        # Add size displays
        for fmt in available_formats['audio'] + available_formats['video']:
            if fmt.get('size_estimate'):
                fmt['size_display'] = format_file_size(fmt['size_estimate'])
        
        result = {
            'status': 'success',
            'video_id': video_id,
            'title': title,
            'duration': duration,
            'duration_formatted': f"{duration // 60}:{duration % 60:02d}",
            'uploader': uploader,
            'thumbnail': thumbnail,
            'description': description,
            'formats': available_formats,
            'url': url
        }
        
        logger.info("=" * 80)
        logger.info(f"[GET INFO SUCCESS] Title: {title}")
        logger.info(f"[GET INFO SUCCESS] Duration: {duration}s")
        logger.info(f"[GET INFO SUCCESS] Audio formats: {len(available_formats['audio'])}")
        logger.info(f"[GET INFO SUCCESS] Video formats: {len(available_formats['video'])}")
        logger.info("=" * 80)
        
        return result
        
    except Exception as e:
        error_message = handle_download_error(str(e))
        logger.error(f"[GET INFO FAILED] {error_message}")
        
        # Retry transient errors
        if is_transient_error(error_message):
            retry_count = self.request.retries
            if retry_count < self.max_retries:
                countdown = 3 * (2 ** retry_count)
                raise self.retry(exc=e, countdown=countdown)
        
        raise Exception(error_message)


@shared_task(bind=True, name='converter.convert_video_to_format', max_retries=3)
def convert_video_to_format(self, url, file_id, format_choice='mp3-192', video_title=None):
    """
    Convert video from URL to specified format with progress tracking.
    
    Args:
        url: Video URL
        file_id: Unique file identifier
        format_choice: Format ID (e.g., "m4a", "mp3-192", "video-720p")
        video_title: Optional pre-fetched video title
        
    Returns:
        Dictionary with conversion result and file info
    """
    task_id = self.request.id
    
    logger.info("=" * 80)
    logger.info(f"[CONVERT] Task ID: {task_id}")
    logger.info(f"[CONVERT] URL: {url}")
    logger.info(f"[CONVERT] Format: {format_choice}")
    logger.info("=" * 80)
    
    final_path = None
    temp_path = None
    
    try:
        # Stage 1: Validation (0-10%)
        update_progress(task_id, 'VALIDATING', 0, 'Validating video URL...')
        
        with YoutubeDL(get_youtube_options(for_validation=True)) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if info is None:
                raise Exception("Could not extract video information")
            
            duration = info.get("duration", 0)
            title = video_title or info.get("title", "Unknown")
            uploader = info.get("uploader", "Unknown")
        
        if duration > settings.MAX_VIDEO_DURATION:
            raise Exception(
                f"Video is too long ({duration // 60} minutes). "
                f"Maximum: {settings.MAX_VIDEO_DURATION // 60} minutes."
            )
        
        update_progress(
            task_id, 'VALIDATED', 10, 'Video validated',
            title=title, duration=duration, uploader=uploader, format=format_choice
        )
        
        # Stage 2: Prepare (10-15%)
        update_progress(task_id, 'PREPARING', 12, 'Preparing download...')
        
        download_opts, extension = build_ytdlp_options(format_choice)
        final_filename = generate_unique_filename(title, file_id, extension)
        final_path = os.path.join(settings.MEDIA_ROOT, final_filename)
        temp_path = os.path.join(settings.MEDIA_ROOT, f"temp_{file_id}")
        
        logger.info(f"[PATHS] Format: {format_choice}, Extension: {extension}")
        logger.info(f"[PATHS] Final: {final_filename}")
        
        # Format-specific messages
        if format_choice == 'm4a':
            msg = 'Downloading M4A (no conversion needed)...'
        elif format_choice.startswith('mp3-'):
            msg = 'Downloading audio for MP3 conversion...'
        elif format_choice.startswith('video-'):
            msg = 'Downloading video...'
        else:
            msg = 'Starting download...'
        
        update_progress(task_id, 'PREPARING', 15, msg)
        
        # Stage 3: Download (15-75%)
        download_opts.update({
            "outtmpl": temp_path + ".%(ext)s",
            "progress_hooks": [ProgressHook(task_id, format_choice)],
            "concurrent_fragment_downloads": 3,
            "http_chunk_size": 10485760,
            "keepvideo": False,
            "format_sort": ["quality", "res", "fps", "codec:aac", "size", "br"],
        })
        
        logger.info(f"[DOWNLOAD] Starting...")
        
        with YoutubeDL(download_opts) as ydl:
            ydl.download([url])
        
        logger.info(f"[DOWNLOAD] Completed")
        
        # Stage 4: Post-processing (75-95%)
        if format_choice == 'm4a':
            update_progress(task_id, 'FINALIZING', 80, 'M4A ready!')
        elif format_choice.startswith('mp3-'):
            update_progress(task_id, 'CONVERTING', 80, 'Converting to MP3...')
            time.sleep(0.5)
        elif format_choice.startswith('video-'):
            update_progress(task_id, 'CONVERTING', 80, 'Processing video...')
            time.sleep(1.0)
        
        update_progress(task_id, 'FINALIZING', 85, 'Finalizing...')
        
        # Find output file
        temp_file = temp_path + f".{extension}"
        
        # Wait for filesystem
        max_wait, elapsed = 5, 0
        while not os.path.exists(temp_file) and elapsed < max_wait:
            time.sleep(0.5)
            elapsed += 0.5
        
        update_progress(task_id, 'FINALIZING', 90, 'Verifying file...')
        
        # Move to final location
        if os.path.exists(temp_file):
            os.rename(temp_file, final_path)
            logger.info(f"[FINALIZE] File moved to: {final_path}")
        elif not os.path.exists(final_path):
            raise Exception(f"File was not created: {temp_file}")
        
        # Verify file
        if not os.path.exists(final_path):
            raise Exception(f"File does not exist: {final_path}")
        
        file_size = os.path.getsize(final_path)
        if file_size < 1024:
            raise Exception(f"File too small: {file_size} bytes")
        
        logger.info("=" * 80)
        logger.info(f"[SUCCESS] File: {final_filename}")
        logger.info(f"[SUCCESS] Size: {format_file_size(file_size)}")
        logger.info(f"[SUCCESS] Format: {extension.upper()}")
        logger.info("=" * 80)
        
        # Success!
        update_progress(
            task_id, 'SUCCESS', 100, 'Complete!',
            file_id=file_id, filename=final_filename,
            file_size=file_size, title=title, format=extension
        )
        
        return {
            'status': 'success',
            'file_id': file_id,
            'filename': final_filename,
            'file_size': file_size,
            'title': title,
            'format': extension,
            'message': f'Conversion to {extension.upper()} completed'
        }
        
    except Exception as e:
        error_message = handle_download_error(str(e))
        logger.error(f"[CONVERT FAILED] {error_message}")
        
        update_progress(task_id, 'FAILURE', 0, f'Failed: {error_message}')
        
        # Cleanup
        try:
            if final_path and os.path.exists(final_path):
                os.remove(final_path)
            if temp_path:
                for ext in [".mp3", ".m4a", ".mp4", ".webm", ".part"]:
                    temp_file = temp_path + ext
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
        except Exception as cleanup_error:
            logger.error(f"[CLEANUP] Error: {cleanup_error}")
        
        # Retry transient errors
        if is_transient_error(error_message):
            retry_count = self.request.retries
            if retry_count < self.max_retries:
                countdown = 5 * (2 ** retry_count)
                raise self.retry(exc=e, countdown=countdown)
        
        raise


@shared_task(name='converter.cleanup_old_files')
def cleanup_old_files():
    """Clean up old media files across all formats."""
    media_root = settings.MEDIA_ROOT
    max_age = settings.FILE_CLEANUP_AFTER
    current_time = time.time()
    cleaned_count = 0
    extensions = ['.mp3', '.m4a', '.mp4', '.webm']
    
    try:
        for filename in os.listdir(media_root):
            if not any(filename.endswith(ext) for ext in extensions):
                continue
            
            file_path = os.path.join(media_root, filename)
            
            if not os.path.exists(file_path):
                continue
            
            file_age = current_time - os.path.getmtime(file_path)
            
            if file_age > max_age:
                try:
                    os.remove(file_path)
                    cleaned_count += 1
                    logger.info(f"[CLEANUP] Removed: {filename}")
                except Exception as e:
                    logger.error(f"[CLEANUP] Error removing {filename}: {e}")
    except Exception as e:
        logger.error(f"[CLEANUP] Task error: {e}")
    
    return f"Cleaned up {cleaned_count} old files"


@shared_task(name='converter.update_ytdlp')
def update_ytdlp():
    """Update yt-dlp to latest version."""
    import subprocess
    try:
        result = subprocess.run(
            ["pip", "install", "--no-cache-dir", "--upgrade", "--force-reinstall", "yt-dlp"],
            capture_output=True,
            text=True,
            timeout=120
        )
        success = result.returncode == 0
        logger.info(f"[UPDATE] yt-dlp update: {'success' if success else 'failed'}")
        return f"yt-dlp updated: {success}"
    except Exception as e:
        logger.error(f"[UPDATE] Failed: {e}")
        return f"yt-dlp update failed: {str(e)}"