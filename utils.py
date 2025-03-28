# utils.py
# Contains general utility functions for the bot, including file operations and splitting.
# MODIFIED: Implements ffmpeg video splitting based on calculated duration aiming for ~1800MB parts.

import os
import re
import urllib.parse
import logging
import math
import asyncio # For sleep and subprocess
import subprocess # For running ffmpeg/ffprobe
import glob # To find created parts
import json # To parse ffprobe output

# Need ContextTypes for type hinting if modifying function signatures
from telegram.ext import ContextTypes # Add this import

logger = logging.getLogger(__name__)

# --- Constants ---
# Target size for parts (1800 MiB)
TARGET_SPLIT_SIZE_MB = 1800
TARGET_SPLIT_SIZE_BYTES = TARGET_SPLIT_SIZE_MB * 1024 * 1024
# Original check size limit (slightly larger, just to trigger splitting process)
SPLIT_CHECK_SIZE = 1950 * 1024 * 1024 # Approx 1.95 GiB

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".mpeg", ".mpg"}


# --- Helper to run FFmpeg/FFprobe commands ---
async def _run_command(cmd_list, command_name="command"):
    """Runs an external command asynchronously, logs output, returns success(bool), output(str)."""
    cmd_str = " ".join(map(str, cmd_list)) # Ensure all parts are strings for join
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
        return False, error_output # Return error output for potential debugging
    else:
        # Log stderr too even on success, as useful info might be there
        if error_output:
             logger.info(f"{command_name} stderr output:\n{error_output}")
        logger.info(f"{command_name} command finished successfully.")
        return True, output # Return stdout output on success


# --- Video Splitting using FFmpeg (Dynamic Duration) ---
async def _split_video_dynamic_duration(original_path, context: ContextTypes.DEFAULT_TYPE | None, chat_id: int | None):
    """Splits a video file into segments using calculated duration based on bitrate."""
    base_filename = os.path.basename(original_path)
    file_dir = os.path.dirname(original_path)
    _ , file_ext = os.path.splitext(base_filename)

    # Get config values from context
    download_dir = context.bot_data.get('download_dir', '/content/downloads') if context else '/content/downloads'

    # 1. Get Bitrate using ffprobe
    logger.info(f"Getting bitrate for {base_filename} using ffprobe...")
    ffprobe_cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", original_path # Added show_streams for more info if needed
    ]
    probe_success, probe_output = await _run_command(ffprobe_cmd, "ffprobe")

    bitrate = None
    duration_from_probe = None
    if probe_success and probe_output:
        try:
            video_info = json.loads(probe_output)
            if 'format' in video_info and 'bit_rate' in video_info['format']:
                bitrate = float(video_info["format"]["bit_rate"])
                logger.info(f"Detected bitrate: {bitrate} bps")
            if 'format' in video_info and 'duration' in video_info['format']:
                 duration_from_probe = float(video_info["format"]["duration"]) # Total duration
        except json.JSONDecodeError:
            logger.error("Failed to parse ffprobe JSON output.")
        except KeyError:
             logger.warning("Could not find bitrate or duration in ffprobe output.")

    if bitrate is None or bitrate <= 0:
        logger.warning(f"Could not determine bitrate for {base_filename}, cannot perform size-based split. Aborting split.")
        if context and chat_id: await context.bot.send_message(chat_id, f"⚠️ Could not determine video bitrate for '{base_filename}'. Cannot split by size.")
        return None # Cannot proceed without bitrate

    # 2. Calculate Segment Duration
    target_size_bits = TARGET_SPLIT_SIZE_BYTES * 8
    # Calculate target duration per segment
    calculated_duration = int(target_size_bits / bitrate)
    # Add a minimum duration (e.g., 10 seconds) to avoid tiny segments for huge bitrates
    segment_duration = max(10, calculated_duration)
    logger.info(f"Calculated target segment duration: {segment_duration} seconds (aiming for ~{TARGET_SPLIT_SIZE_MB}MB parts)")

    # Optional: Check if calculated duration is longer than total -> no split needed
    if duration_from_probe and segment_duration >= duration_from_probe:
        logger.info("Calculated segment duration >= total duration. No splitting needed based on size/bitrate.")
        return [original_path]

    # 3. Prepare for FFmpeg split command
    parts_dir = os.path.join(download_dir, base_filename + "_parts")
    os.makedirs(parts_dir, exist_ok=True)
    output_pattern = os.path.join(parts_dir, f"{base_filename}_part%03d{file_ext}")

    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'warning',
        '-i', original_path,
        '-c', 'copy', '-map', '0',
        '-segment_time', str(segment_duration), # Use calculated duration
        '-f', 'segment', '-reset_timestamps', '1',
        output_pattern
    ]

    logger.info(f"Starting ffmpeg video split for: {base_filename}")
    if context and chat_id:
        try: await context.bot.send_message(chat_id, f"✂️ Splitting video '{base_filename}' (aiming for ~{TARGET_SPLIT_SIZE_MB}MB parts)...")
        except Exception: pass

    # 4. Run FFmpeg
    split_success, _ = await _run_command(cmd, "ffmpeg")

    if not split_success:
        logger.error(f"ffmpeg splitting failed for {original_path}. Cleaning up partial parts dir.")
        try: import shutil; shutil.rmtree(parts_dir)
        except Exception as cleanup_err: logger.error(f"Failed cleanup {parts_dir}: {cleanup_err}")
        if context and chat_id: await context.bot.send_message(chat_id, f"❌ Error occurred during video splitting for '{base_filename}'.")
        return None # Indicate failure

    # 5. Find and return created parts
    part_pattern_glob = os.path.join(parts_dir, f"{base_filename}_part*{file_ext}")
    created_parts = sorted(glob.glob(part_pattern_glob))

    if not created_parts:
        logger.error(f"ffmpeg seemed to succeed but no part files found: {part_pattern_glob}")
        if context and chat_id: await context.bot.send_message(chat_id, f"❌ Splitting seemed finished, but no parts found for '{base_filename}'.")
        return None

    num_parts_found = len(created_parts)
    logger.info(f"ffmpeg split successful. Found {num_parts_found} parts.")
    # Check if only one part was created (might happen if calculation was off or video short)
    if num_parts_found == 1 and os.path.exists(original_path):
        original_size = os.path.getsize(original_path)
        part_size = os.path.getsize(created_parts[0])
        # If the single part is almost the same size as original, just use original
        if abs(original_size - part_size) < 1024 * 1024: # Tolerance of 1MB
             logger.info("Only one part created, similar size to original. Using original file.")
             try: # Cleanup the single part and dir
                  os.remove(created_parts[0])
                  os.rmdir(parts_dir)
             except Exception as cleanup_err: logger.warning(f"Could not cleanup single part/dir: {cleanup_err}")
             return [original_path]

    # Send completion message
    if context and chat_id:
        try: await context.bot.send_message(chat_id, f"✅ Video splitting complete ({num_parts_found} parts created).")
        except Exception: pass

    return created_parts


# --- Main Splitting Logic ---
async def split_if_needed(original_path, context: ContextTypes.DEFAULT_TYPE | None, chat_id: int | None):
    """
    Checks file size and splits it if needed. Uses ffmpeg dynamic duration for videos in Video mode.
    Returns a list of file paths (original file or parts), or None on error/unsupported.
    """
    try:
        if not os.path.exists(original_path): logger.error(f"Split check fail: Not found {original_path}"); return None
        file_size = os.path.getsize(original_path); base_filename = os.path.basename(original_path)
        _ , ext = os.path.splitext(base_filename); is_video = ext.lower() in VIDEO_EXTENSIONS
        # Get upload_mode from context (essential for deciding split method)
        upload_mode = context.bot_data.get('upload_mode', 'Document') if context else 'Document'

        logger.info(f"Checking '{base_filename}': Size={file_size}, IsVideo={is_video}, UploadMode={upload_mode}")
        if file_size <= SPLIT_CHECK_SIZE: # Check against slightly larger threshold first
            logger.info("File size within check limit, no splitting needed.")
            return [original_path]

        # --- File needs splitting ---
        if is_video and upload_mode == "Video":
            # Use ffmpeg dynamic duration splitting for videos if upload mode is Video
            logger.info("Attempting video split with dynamic duration ffmpeg...")
            return await _split_video_dynamic_duration(original_path, context, chat_id)
        else:
            # File is large, but not a video OR upload mode is not Video.
            d_dir = context.bot_data.get('download_dir', '/content') if context else '/content'
            logger.warning(f"Large file '{base_filename}' ({file_size/1024/1024:.1f}MB) in {d_dir} cannot be split (not video or mode is '{upload_mode}').")
            if context and chat_id:
                 try: await context.bot.send_message(chat_id, f"⚠️ File '{base_filename}' ({file_size/1024/1024:.1f} MB) is too large & cannot be split for mode '{upload_mode}'. Upload aborted.")
                 except Exception: pass
            return None # Indicate splitting is not supported/failed

    except Exception as e:
        logger.error(f"Error during splitting check {original_path}: {e}", exc_info=True)
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
        # Only delete original if splitting definitely occurred (more than 1 part generated)
        if os.path.exists(original_path) and len(parts) > 1:
             try: os.remove(original_path); logger.info(f"Deleted original: {original_path}")
             except Exception as e_orig: logger.error(f"Failed delete original {original_path}: {e_orig}")
    except Exception as e: logger.error(f"Error cleanup {original_path}: {e}", exc_info=True)


# --- Other existing utils (Unchanged) ---
def write_failed_downloads_to_file(failed_items, downloader_name, download_directory):
    # ... (function body unchanged) ...
    if not failed_items: return None
    file_path = os.path.join(download_directory, f"failed_downloads_{downloader_name}.txt")
    try:
        os.makedirs(download_directory, exist_ok=True)
        with open(file_path, "w") as f: f.write(f"# Failed URLs for {downloader_name}\n"); [f.write(f"{item}\n") for item in failed_items]
        logger.info(f"Failed list saved: {file_path}"); return file_path
    except Exception as e: logger.error(f"Error writing failed file: {e}"); return None

def clean_filename(filename):
    # ... (function body unchanged) ...
    try: filename = urllib.parse.unquote(filename, encoding='utf-8', errors='replace')
    except Exception: pass
    filename = filename.replace('%20', ' '); cleaned_filename = re.sub(r'[\\/:*?"<>|]', '_', filename)
    cleaned_filename = re.sub(r'_+', '_', cleaned_filename); cleaned_filename = cleaned_filename.strip('._ ')
    cleaned_filename = cleaned_filename[:250]; return cleaned_filename if cleaned_filename else "downloaded_file"

def extract_filename_from_url(url):
    # ... (function body unchanged) ...
    try:
        if not isinstance(url, str) or not url.lower().startswith(('http://', 'https://')): logger.warning(f"Skip invalid URL: {str(url)[:100]}"); return None
        parsed_url = urllib.parse.urlparse(url); path = parsed_url.path; filename_raw = os.path.basename(path)
        if not filename_raw and path != '/': segments = path.strip('/').split('/'); filename_raw = segments[-1] if segments else ''
        if not filename_raw: filename_raw = parsed_url.netloc.replace('.', '_') + "_file"
        decoded_filename = urllib.parse.unquote(filename_raw, encoding='utf-8', errors='replace'); return clean_filename(decoded_filename)
    except Exception as e: logger.warning(f"Error extracting FN from {url}: {e}"); return None
