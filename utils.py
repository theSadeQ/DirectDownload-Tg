# utils.py
# Contains general utility functions for the bot, including file operations and splitting.
# MODIFIED: Includes ffmpeg-based video splitting.

import os
import re
import urllib.parse
import logging
import math
import asyncio # For sleep and subprocess
import subprocess # For running ffmpeg (alternative way)
import glob # To find created parts

# Import config to access DOWNLOAD_DIR and UPLOAD_MODE
try:
    import config
except ImportError:
    logging.critical("CRITICAL: config.py not found or cannot be imported by utils.py!")
    raise ImportError("Essential configuration file 'config.py' not found.") from None

logger = logging.getLogger(__name__)

# --- Constants ---
SPLIT_SIZE = 1950 * 1024 * 1024 # Approx 1.95 GiB limit before splitting
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".mpeg", ".mpg"}
# Choose a segment duration for ffmpeg splitting (in seconds).
# 1800s = 30 minutes. Adjust if needed, but this often keeps parts under 2GB.
FFMPEG_SEGMENT_DURATION = 1800


# --- Helper to run FFmpeg command ---
async def _run_ffmpeg_command(cmd_list, chat_id, context):
    """Runs an ffmpeg command asynchronously, logs output."""
    cmd_str = " ".join(cmd_list) # For logging
    logger.info(f"Running ffmpeg command: {cmd_str}")
    process = await asyncio.create_subprocess_exec(
        *cmd_list,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return_code = process.returncode

    if return_code != 0:
        error_output = stderr.decode('utf-8', errors='replace').strip()
        logger.error(f"ffmpeg command failed with code {return_code}:\n{cmd_str}\nError:\n{error_output}")
        if context and chat_id:
             try: await context.bot.send_message(chat_id, f"❌ ffmpeg failed (code {return_code}). Check logs.")
             except Exception: pass # Ignore if sending fails
        return False, None # Indicate failure
    else:
        output = stdout.decode('utf-8', errors='replace').strip()
        # Log stderr too even on success, as ffmpeg often prints useful info there
        stderr_output = stderr.decode('utf-8', errors='replace').strip()
        if stderr_output:
             logger.info(f"ffmpeg stderr (might contain info):\n{stderr_output}")
        logger.info("ffmpeg command finished successfully.")
        return True, output # Indicate success
    

# --- Video Splitting using FFmpeg ---
async def _split_video_ffmpeg(original_path, context=None, chat_id=None):
    """Splits a video file into segments using ffmpeg."""
    base_filename = os.path.basename(original_path)
    file_dir = os.path.dirname(original_path)
    _ , file_ext = os.path.splitext(base_filename)

    parts_dir = os.path.join(config.DOWNLOAD_DIR, base_filename + "_parts")
    os.makedirs(parts_dir, exist_ok=True)

    # Output pattern for ffmpeg segment muxer
    output_pattern = os.path.join(parts_dir, f"{base_filename}_part%03d{file_ext}")

    # Build the ffmpeg command
    cmd = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'warning', # Or 'error' for less noise, 'info' for more
        '-i', original_path,    # Input file
        '-c', 'copy',           # Copy streams without re-encoding (fast!)
        '-map', '0',            # Map all streams (video, audio, subtitles)
        '-segment_time', str(FFMPEG_SEGMENT_DURATION), # Split duration
        '-f', 'segment',        # Use segment muxer
        '-reset_timestamps', '1',# Reset timestamps for each part
        output_pattern          # Output file pattern
    ]

    logger.info(f"Starting ffmpeg video split for: {base_filename}")
    if context and chat_id:
        try: await context.bot.send_message(chat_id, f"✂️ Splitting video '{base_filename}' using ffmpeg...")
        except Exception: pass

    success, _ = await _run_ffmpeg_command(cmd, chat_id, context)

    if not success:
        logger.error(f"ffmpeg splitting failed for {original_path}. Cleaning up partial parts dir.")
        try: # Attempt cleanup
            if os.path.isdir(parts_dir):
                import shutil
                shutil.rmtree(parts_dir)
        except Exception as cleanup_err:
             logger.error(f"Failed to cleanup parts dir {parts_dir} after ffmpeg fail: {cleanup_err}")
        return None # Indicate failure

    # Find the created part files
    # Need to sort them correctly based on the number in the filename
    part_pattern_glob = os.path.join(parts_dir, f"{base_filename}_part*{file_ext}")
    created_parts = sorted(glob.glob(part_pattern_glob))

    if not created_parts:
        logger.error(f"ffmpeg seemed to succeed but no part files found matching pattern: {part_pattern_glob}")
        return None

    logger.info(f"ffmpeg split successful. Found {len(created_parts)} parts.")
    if context and chat_id:
        try: await context.bot.send_message(chat_id, f"✅ Video splitting complete ({len(created_parts)} parts).")
        except Exception: pass

    return created_parts

# --- Main Splitting Logic ---
async def split_if_needed(original_path, context=None, chat_id=None):
    """
    Checks file size and splits it if needed. Uses ffmpeg for videos in Video mode.
    Returns a list of file paths (original file or parts), or None on error/unsupported.
    """
    try:
        if not os.path.exists(original_path):
            logger.error(f"Splitting check error: File not found at {original_path}")
            return None

        file_size = os.path.getsize(original_path)
        base_filename = os.path.basename(original_path)
        _ , ext = os.path.splitext(base_filename)
        is_video = ext.lower() in VIDEO_EXTENSIONS

        logger.info(f"Checking file '{base_filename}': Size={file_size}, IsVideo={is_video}, UploadMode={config.UPLOAD_MODE}")

        if file_size <= SPLIT_SIZE:
            logger.info("File size within limit, no splitting needed.")
            return [original_path]

        # --- File needs splitting ---
        if is_video and config.UPLOAD_MODE == "Video":
            # Use ffmpeg for videos if upload mode is Video
            return await _split_video_ffmpeg(original_path, context, chat_id)
        else:
            # File is large, but not a video OR upload mode is not Video.
            # We are NOT using the generic byte splitter for these cases as it corrupts formats.
            logger.warning(f"File '{base_filename}' ({file_size/1024/1024:.1f} MB) is over the size limit ({SPLIT_SIZE/1024/1024:.0f} MB), "
                           f"but it's not a video or UPLOAD_MODE is '{config.UPLOAD_MODE}'. Cannot split this type/mode.")
            if context and chat_id:
                 try:
                    await context.bot.send_message(chat_id, f"⚠️ File '{base_filename}' ({file_size/1024/1024:.1f} MB) is too large "
                                                             f"and cannot be split for the current upload mode ('{config.UPLOAD_MODE}'). Upload aborted.")
                 except Exception: pass
            return None # Indicate splitting is not supported/failed

    except Exception as e:
        logger.error(f"Error during file splitting check for {original_path}: {e}", exc_info=True)
        if context and chat_id:
            try: await context.bot.send_message(chat_id, f"❌ Error checking/splitting file '{os.path.basename(original_path)}': {e}")
            except Exception: pass
        return None # Indicate failure


# --- Cleanup Utility (Unchanged) ---
async def cleanup_split_parts(original_path, parts):
    """Deletes split parts and their directory, and optionally the original file."""
    if not parts or len(parts) <= 1: # Only cleanup if actual splitting occurred (more than 1 part)
        logger.debug(f"Cleanup skipped for {original_path} as no splitting occurred or only one part.")
        return

    parts_dir = os.path.dirname(parts[0]) # Get directory from first part
    logger.info(f"Cleaning up {len(parts)} split parts in {parts_dir}")
    deleted_parts_count = 0
    try:
        for part_path in parts:
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                    deleted_parts_count += 1
                except Exception as e_part:
                    logger.error(f"Failed to delete part {part_path}: {e_part}")
        logger.info(f"Deleted {deleted_parts_count} part files.")

        # Attempt to remove the directory if it exists and is empty
        if os.path.isdir(parts_dir):
             try:
                 os.rmdir(parts_dir)
                 logger.info(f"Removed parts directory: {parts_dir}")
             except OSError as e_dir: # Directory might not be empty if a part failed deletion
                  logger.warning(f"Could not remove parts directory {parts_dir} (maybe not empty?): {e_dir}")

        # Delete original large file only if splitting occurred (len(parts)>1)
        if os.path.exists(original_path):
             try:
                 os.remove(original_path)
                 logger.info(f"Deleted original large file: {original_path}")
             except Exception as e_orig:
                  logger.error(f"Failed to delete original large file {original_path}: {e_orig}")

    except Exception as e:
        logger.error(f"Error during cleanup of split parts for {original_path}: {e}", exc_info=True)


# --- Other existing utils (Unchanged) ---

def write_failed_downloads_to_file(failed_items, downloader_name, download_directory):
    """Writes the list of failed source URLs to a text file."""
    if not failed_items:
        return None
    # Use the download_directory passed as argument (should come from config)
    file_path = os.path.join(download_directory, f"failed_downloads_{downloader_name}.txt")
    try:
        os.makedirs(download_directory, exist_ok=True)
        with open(file_path, "w") as f:
            f.write(f"# Failed source URLs for {downloader_name}\n")
            for item in failed_items:
                f.write(f"{item}\n")
        logger.info(f"List of failed {downloader_name} source URLs saved to: {file_path}")
        return file_path
    except Exception as e:
        logger.error(f"Error writing failed items to file: {e}")
        return None

def clean_filename(filename):
    """Cleans filenames by removing/replacing invalid characters."""
    try:
        filename = urllib.parse.unquote(filename, encoding='utf-8', errors='replace')
    except Exception:
        pass # Ignore decoding errors if any
    filename = filename.replace('%20', ' ') # Specific fix before general cleaning
    cleaned_filename = re.sub(r'[\\/:*?"<>|]', '_', filename)
    cleaned_filename = re.sub(r'_+', '_', cleaned_filename)
    cleaned_filename = cleaned_filename.strip('._ ')
    cleaned_filename = cleaned_filename[:250]
    return cleaned_filename if cleaned_filename else "downloaded_file"

def extract_filename_from_url(url):
    """Extracts and cleans the filename from a given URL."""
    try:
        if not isinstance(url, str) or not url.lower().startswith(('http://', 'https://')):
             logger.warning(f"Skipping potentially invalid URL: {str(url)[:100]}")
             return None
        parsed_url = urllib.parse.urlparse(url)
        path = parsed_url.path
        filename_raw = os.path.basename(path)
        if not filename_raw and path != '/':
             segments = path.strip('/').split('/')
             if segments:
                 filename_raw = segments[-1]
        if not filename_raw:
             filename_raw = parsed_url.netloc.replace('.', '_') + "_file"
        decoded_filename = urllib.parse.unquote(filename_raw, encoding='utf-8', errors='replace')
        return clean_filename(decoded_filename)
    except Exception as e:
        logger.warning(f"Error extracting filename from URL {url}: {e}")
        return None
