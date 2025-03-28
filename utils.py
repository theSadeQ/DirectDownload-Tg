# utils.py
# MODIFIED: Accesses config (like DOWNLOAD_DIR) via context.bot_data

import os
import re
import urllib.parse
import logging
import math
import asyncio
import subprocess
import glob

# NO 'import config' needed here anymore

# Need ContextTypes for type hinting if modifying function signatures
from telegram.ext import ContextTypes # Add this import

logger = logging.getLogger(__name__)

# --- Constants ---
SPLIT_SIZE = 1950 * 1024 * 1024
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".mpeg", ".mpg"}
FFMPEG_SEGMENT_DURATION = 1800

# --- Helper to run FFmpeg ---
async def _run_ffmpeg_command(cmd_list, chat_id, context: ContextTypes.DEFAULT_TYPE | None): # Added Context type hint
    # ... (function body unchanged) ...
    cmd_str = " ".join(cmd_list); logger.info(f"Running ffmpeg: {cmd_str}")
    process = await asyncio.create_subprocess_exec(*cmd_list, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate(); return_code = process.returncode
    if return_code != 0:
        err_out = stderr.decode('utf-8', 'replace').strip(); logger.error(f"ffmpeg failed ({return_code}):\n{cmd_str}\n{err_out}")
        if context and chat_id:
             try: await context.bot.send_message(chat_id, f"❌ ffmpeg failed (code {return_code}). Check logs.")
             except Exception: pass
        return False, None
    else:
        out = stdout.decode('utf-8', 'replace').strip(); err_out = stderr.decode('utf-8', 'replace').strip()
        if err_out: logger.info(f"ffmpeg stderr:\n{err_out}")
        logger.info("ffmpeg success."); return True, out

# --- Video Splitting using FFmpeg ---
# Modify to accept context to get DOWNLOAD_DIR and pass down to _run_ffmpeg
async def _split_video_ffmpeg(original_path, context: ContextTypes.DEFAULT_TYPE | None, chat_id: int | None):
    base_filename = os.path.basename(original_path)
    _ , file_ext = os.path.splitext(base_filename)
    # Get download_dir from context
    download_dir = context.bot_data.get('download_dir', '/content/fallback_dl_dir') # Use fallback

    parts_dir = os.path.join(download_dir, base_filename + "_parts")
    os.makedirs(parts_dir, exist_ok=True)
    output_pattern = os.path.join(parts_dir, f"{base_filename}_part%03d{file_ext}")
    cmd = ['ffmpeg','-hide_banner','-loglevel','warning','-i',original_path,'-c','copy','-map','0','-segment_time',str(FFMPEG_SEGMENT_DURATION),'-f','segment','-reset_timestamps','1',output_pattern]

    logger.info(f"Starting ffmpeg video split for: {base_filename}")
    if context and chat_id:
        try: await context.bot.send_message(chat_id, f"✂️ Splitting video '{base_filename}' using ffmpeg...")
        except Exception: pass

    # Pass context down to ffmpeg runner
    success, _ = await _run_ffmpeg_command(cmd, chat_id, context)

    if not success:
        logger.error(f"ffmpeg splitting failed: {original_path}. Cleanup partial dir.");
        try: import shutil; shutil.rmtree(parts_dir)
        except Exception as cleanup_err: logger.error(f"Failed cleanup {parts_dir}: {cleanup_err}")
        return None

    part_pattern_glob = os.path.join(parts_dir, f"{base_filename}_part*{file_ext}")
    created_parts = sorted(glob.glob(part_pattern_glob))
    if not created_parts: logger.error(f"No parts found: {part_pattern_glob}"); return None
    logger.info(f"ffmpeg split OK. Found {len(created_parts)} parts.")
    if context and chat_id:
        try: await context.bot.send_message(chat_id, f"✅ Video splitting complete ({len(created_parts)} parts).")
        except Exception: pass
    return created_parts

# --- Main Splitting Logic ---
# Modify to accept context to pass down and get UPLOAD_MODE
async def split_if_needed(original_path, context: ContextTypes.DEFAULT_TYPE | None, chat_id: int | None):
    try:
        if not os.path.exists(original_path): logger.error(f"Split check fail: Not found {original_path}"); return None
        file_size = os.path.getsize(original_path); base_filename = os.path.basename(original_path)
        _ , ext = os.path.splitext(base_filename); is_video = ext.lower() in VIDEO_EXTENSIONS
        # Get upload_mode from context
        upload_mode = context.bot_data.get('upload_mode', 'Document') if context else 'Document'

        logger.info(f"Checking '{base_filename}': Size={file_size}, IsVideo={is_video}, UploadMode={upload_mode}")
        if file_size <= SPLIT_SIZE: logger.info("Size OK, no split."); return [original_path]

        if is_video and upload_mode == "Video":
            logger.info("Attempting video split with ffmpeg...")
            # Pass context down
            return await _split_video_ffmpeg(original_path, context, chat_id)
        else:
            d_dir = context.bot_data.get('download_dir', '/content') if context else '/content'
            logger.warning(f"Large file '{base_filename}' ({file_size/1024/1024:.1f}MB) in {d_dir} cannot be split (not video or mode mismatch).")
            if context and chat_id:
                 try: await context.bot.send_message(chat_id, f"⚠️ File '{base_filename}' too large & cannot be split for mode '{upload_mode}'.")
                 except Exception: pass
            return None
    except Exception as e:
        logger.error(f"Error splitting check {original_path}: {e}", exc_info=True)
        if context and chat_id:
            try: await context.bot.send_message(chat_id, f"❌ Error check/split '{os.path.basename(original_path)}': {e}")
            except Exception: pass
        return None

# --- Cleanup Utility (Unchanged) ---
async def cleanup_split_parts(original_path, parts):
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
        if os.path.exists(original_path):
             try: os.remove(original_path); logger.info(f"Deleted original: {original_path}")
             except Exception as e_orig: logger.error(f"Failed delete original {original_path}: {e_orig}")
    except Exception as e: logger.error(f"Error cleanup {original_path}: {e}", exc_info=True)

# --- Other existing utils (write_failed, clean_filename, extract_filename - Unchanged) ---
def write_failed_downloads_to_file(failed_items, downloader_name, download_directory):
    # ... (function body unchanged) ...
    if not failed_items: return None
    file_path = os.path.join(download_directory, f"failed_downloads_{downloader_name}.txt")
    try:
        os.makedirs(download_directory, exist_ok=True)
        with open(file_path, "w") as f:
            f.write(f"# Failed URLs for {downloader_name}\n"); [f.write(f"{item}\n") for item in failed_items]
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
