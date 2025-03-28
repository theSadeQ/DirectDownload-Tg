# utils.py
# Contains general utility functions for the bot, including file operations and splitting.
# MODIFIED: Increased ffmpeg loglevel to 'info' for debugging split issues.

import os
import re
import urllib.parse
import logging
import math
import asyncio # For sleep and subprocess
import subprocess # For running ffmpeg/ffprobe
import glob # To find created parts
import json # To parse ffprobe output

# Need ContextTypes for type hinting
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# --- Constants ---
SPLIT_CHECK_SIZE = 1950 * 1024 * 1024 # Approx 1.95 GiB limit check before attempting split
TARGET_SPLIT_SIZE_MB = 1800 # Target for calculation (used by dynamic duration)
TARGET_SPLIT_SIZE_BYTES = TARGET_SPLIT_SIZE_MB * 1024 * 1024

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".mpeg", ".mpg"}

# --- Helper to run FFmpeg/FFprobe commands ---
async def _run_command(cmd_list, command_name="command"):
    """Runs an external command asynchronously, logs output, returns success(bool), output(str)."""
    cmd_str = " ".join(map(str, cmd_list))
    logger.info(f"Running {command_name}: {cmd_str}")
    process = await asyncio.create_subprocess_exec(
        *cmd_list,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return_code = process.returncode

    output = stdout.decode('utf-8', errors='replace').strip()
    error_output = stderr.decode('utf-8', errors='replace').strip()

    if return_code != 0:
        logger.error(f"{command_name} failed (code {return_code}):\n{cmd_str}\nError:\n{error_output}")
        return False, error_output
    else:
        # Log stderr too even on success
        if error_output:
             logger.info(f"{command_name} stderr output:\n{error_output}") # Log potential warnings/info
        logger.info(f"{command_name} command finished successfully.")
        return True, output


# --- Video Splitting using FFmpeg (Dynamic Duration) ---
async def _split_video_dynamic_duration(original_path, context: ContextTypes.DEFAULT_TYPE | None, chat_id: int | None):
    """Splits a video file into segments using calculated duration based on bitrate."""
    base_filename = os.path.basename(original_path)
    _ , file_ext = os.path.splitext(base_filename)
    download_dir = context.bot_data.get('download_dir', '/content/downloads') if context else '/content/downloads'

    # 1. Get Bitrate using ffprobe
    logger.info(f"Getting bitrate for {base_filename} using ffprobe...")
    ffprobe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", original_path]
    probe_success, probe_output = await _run_command(ffprobe_cmd, "ffprobe")

    bitrate = None; duration_from_probe = None
    if probe_success and probe_output:
        try:
            video_info = json.loads(probe_output)
            if 'format' in video_info and 'bit_rate' in video_info['format']: bitrate = float(video_info["format"]["bit_rate"])
            if 'format' in video_info and 'duration' in video_info['format']: duration_from_probe = float(video_info["format"]["duration"])
            if bitrate: logger.info(f"Detected bitrate: {bitrate} bps")
            else: logger.warning("Bitrate not found in ffprobe output.")
        except Exception as json_e: logger.error(f"Failed to parse ffprobe JSON: {json_e}")

    if bitrate is None or bitrate <= 0:
        logger.warning(f"Cannot perform size-based split for {base_filename} (bitrate={bitrate}). Aborting split.")
        if context and chat_id: await context.bot.send_message(chat_id, f"⚠️ Cannot determine video bitrate for '{base_filename}'. Split aborted.")
        return None

    # 2. Calculate Segment Duration
    target_size_bits = TARGET_SPLIT_SIZE_BYTES * 8
    calculated_duration = int(target_size_bits / bitrate)
    segment_duration = max(10, calculated_duration) # Min 10 sec duration
    logger.info(f"Calculated target segment duration: {segment_duration} seconds (aiming for ~{TARGET_SPLIT_SIZE_MB}MB)")

    if duration_from_probe and segment_duration >= duration_from_probe:
        logger.info("Calculated duration >= total duration. No split needed."); return [original_path]

    # 3. Prepare FFmpeg command
    parts_dir = os.path.join(download_dir, base_filename + "_parts")
    os.makedirs(parts_dir, exist_ok=True)
    output_pattern = os.path.join(parts_dir, f"{base_filename}_part%03d{file_ext}")

    # ***** CHANGE HERE: Increased log level *****
    cmd = [
        'ffmpeg', '-hide_banner',
        '-loglevel', 'info', # Use 'info' or 'debug' for more details
        '-i', original_path,
        '-c', 'copy', '-map', '0',
        '-segment_time', str(segment_duration),
        '-f', 'segment', '-reset_timestamps', '1',
        output_pattern
    ]
    # ***** END CHANGE *****

    logger.info(f"Starting ffmpeg video split for: {base_filename}")
    if context and chat_id:
        try: await context.bot.send_message(chat_id, f"✂️ Splitting video '{base_filename}'...")
        except Exception: pass

    # 4. Run FFmpeg
    split_success, _ = await _run_command(cmd, "ffmpeg") # Pass context if _run_command needs it

    if not split_success:
        logger.error(f"ffmpeg splitting failed: {original_path}. Cleanup partial dir.");
        try: import shutil; shutil.rmtree(parts_dir)
        except Exception as cl_err: logger.error(f"Failed cleanup {parts_dir}: {cl_err}")
        if context and chat_id: await context.bot.send_message(chat_id, f"❌ Error splitting '{base_filename}'.")
        return None

    # 5. Find and return created parts
    part_pattern_glob = os.path.join(parts_dir, f"{base_filename}_part*{file_ext}")
    created_parts = sorted(glob.glob(part_pattern_glob))
    if not created_parts:
        logger.error(f"No parts found: {part_pattern_glob}")
        if context and chat_id: await context.bot.send_message(chat_id, f"❌ Split OK but no parts found for '{base_filename}'.")
        return None

    num_parts_found = len(created_parts)
    logger.info(f"ffmpeg split OK. Found {num_parts_found} parts.")
    # Handle case where only one part is created (similar size to original)
    if num_parts_found == 1 and os.path.exists(original_path):
        try:
             orig_size = os.path.getsize(original_path); part_size = os.path.getsize(created_parts[0])
             if abs(orig_size - part_size) < 1024*1024: # ~1MB tolerance
                  logger.info("Only one part, similar size. Using original.");
                  os.remove(created_parts[0]); os.rmdir(parts_dir)
                  return [original_path]
        except Exception as single_part_err: logger.warning(f"Error checking/cleaning single part: {single_part_err}")

    if context and chat_id:
        try: await context.bot.send_message(chat_id, f"✅ Video splitting complete ({num_parts_found} parts).")
        except Exception: pass
    return created_parts

# --- Main Splitting Logic ---
async def split_if_needed(original_path, context: ContextTypes.DEFAULT_TYPE | None, chat_id: int | None):
    """ Checks size, splits video using ffmpeg if mode is Video, else fails large files. """
    try:
        if not os.path.exists(original_path): logger.error(f"Split check fail: Not found {original_path}"); return None
        file_size = os.path.getsize(original_path); base_filename = os.path.basename(original_path)
        _ , ext = os.path.splitext(base_filename); is_video = ext.lower() in VIDEO_EXTENSIONS
        upload_mode = context.bot_data.get('upload_mode', 'Document') if context else 'Document'

        logger.info(f"Check '{base_filename}': Size={file_size}, IsVideo={is_video}, Mode={upload_mode}")
        if file_size <= SPLIT_CHECK_SIZE: logger.info("Size OK."); return [original_path]

        if is_video and upload_mode == "Video":
            logger.info("Attempting dynamic duration ffmpeg video split...")
            return await _split_video_dynamic_duration(original_path, context, chat_id)
        else:
            d_dir = context.bot_data.get('download_dir', '/content') if context else '/content'
            logger.warning(f"Large file '{base_filename}' ({file_size/1024/1024:.1f}MB) in {d_dir} cannot split (not video or mode '{upload_mode}').")
            if context and chat_id:
                 try: await context.bot.send_message(chat_id, f"⚠️ File '{base_filename}' too large & cannot be split for mode '{upload_mode}'.")
                 except Exception: pass
            return None # Splitting not supported/failed
    except Exception as e:
        logger.error(f"Error splitting check {original_path}: {e}", exc_info=True)
        if context and chat_id:
            try: await context.bot.send_message(chat_id, f"❌ Error check/split '{os.path.basename(original_path)}': {e}")
            except Exception: pass
        return None


# --- Cleanup Utility (Unchanged) ---
async def cleanup_split_parts(original_path, parts):
    """Deletes split parts and their directory, and optionally the original file."""
    # ... (function body unchanged) ...
    if not parts or len(parts) <= 1: logger.debug(f"Cleanup skipped {original_path}."); return
    parts_dir = os.path.dirname(parts[0]); logger.info(f"Cleaning up {len(parts)} parts in {parts_dir}"); deleted_parts_count = 0
    try:
        for part_path in parts:
            if os.path.exists(part_path):
                try: os.remove(part_path); deleted_parts_count += 1
                except Exception as e_part: logger.error(f"Failed delete part {part_path}: {e_part}")
        logger.info(f"Deleted {deleted_parts_count} part files.")
        if os.path.isdir(parts_dir):
             try: os.rmdir(parts_dir); logger.info(f"Removed parts dir: {parts_dir}")
             except OSError as e_dir: logger.warning(f"Could not remove parts dir {parts_dir}: {e_dir}")
        # Only delete original if splitting definitely occurred (more than 1 part generated/returned)
        if os.path.exists(original_path) and len(parts) > 1:
             try: os.remove(original_path); logger.info(f"Deleted original: {original_path}")
             except Exception as e_orig: logger.error(f"Failed delete original {original_path}: {e_orig}")
    except Exception as e: logger.error(f"Error cleanup {original_path}: {e}", exc_info=True)


# --- Other existing utils (Unchanged) ---
def write_failed_downloads_to_file(failed_items, downloader_name, download_directory):
    # ... (function body unchanged) ...
    if not failed_items: return None
    file_path = os.path.join(download_directory, f"failed_downloads_{downloader_name}.txt")
    try: os.makedirs(download_directory, exist_ok=True); f=open(file_path, "w"); f.write(f"# Failed URLs for {downloader_name}\n"); [f.write(f"{item}\n") for item in failed_items]; f.close(); logger.info(f"Failed list saved: {file_path}"); return file_path
    except Exception as e: logger.error(f"Error writing failed file: {e}"); return None

def clean_filename(filename):
    # ... (function body unchanged) ...
    try: filename = urllib.parse.unquote(filename, encoding='utf-8', errors='replace')
    except Exception: pass
    filename = filename.replace('%20', ' '); cleaned_filename = re.sub(r'[\\/:*?"<>|]', '_', filename); cleaned_filename = re.sub(r'_+', '_', cleaned_filename); cleaned_filename = cleaned_filename.strip('._ '); cleaned_filename = cleaned_filename[:250]; return cleaned_filename if cleaned_filename else "downloaded_file"

def extract_filename_from_url(url):
    # ... (function body unchanged) ...
    try:
        if not isinstance(url, str) or not url.lower().startswith(('http://', 'https://')): logger.warning(f"Skip invalid URL: {str(url)[:100]}"); return None
        parsed_url = urllib.parse.urlparse(url); path = parsed_url.path; filename_raw = os.path.basename(path)
        if not filename_raw and path != '/': segments = path.strip('/').split('/'); filename_raw = segments[-1] if segments else ''
        if not filename_raw: filename_raw = parsed_url.netloc.replace('.', '_') + "_file"
        decoded_filename = urllib.parse.unquote(filename_raw, encoding='utf-8', errors='replace'); return clean_filename(decoded_filename)
    except Exception as e: logger.warning(f"Error extracting FN from {url}: {e}"); return None
