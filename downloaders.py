# downloaders.py
# Contains download logic.
# MODIFIED: Added download progress reporting using ptb context edits.

import logging
import asyncio
import requests
import os
import time # Added for progress reporting
import math # Added for progress reporting

# Import framework types
from pyrogram import Client
from telegram import Update
from telegram.ext import ContextTypes
# Import specific PTB errors for message editing
# Corrected import
from telegram.error import BadRequest as PTBBadRequest, RetryAfter

# Import local helpers/config
from upload import upload_file_pyrogram
from utils import clean_filename, split_if_needed, cleanup_split_parts

logger = logging.getLogger(__name__)

# --- Download Progress Helper ---
# (Similar to upload progress, but uses PTB context for edits)
async def _edit_download_progress(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, current: int, total: int, start_time: float, file_name: str):
    """Helper to format and edit download progress messages, with throttling."""
    now = time.time()
    # Basic throttle: minimum 5 seconds between edits
    if 'last_download_edit_time' not in context.chat_data:
        context.chat_data['last_download_edit_time'] = 0
    if now - context.chat_data['last_download_edit_time'] < 5:
        return

    elapsed_time = now - start_time
    speed = current / elapsed_time if elapsed_time > 0 else 0
    speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed > 0 else "N/A"

    progress_text = f"⬇️ Downloading: {file_name}\n"
    size_str = f"{(current / 1024 / 1024):.1f}MB"

    if total > 0:
        percent = round((current / total) * 100, 1)
        percent_str = f"{percent}%"
        eta_str = "N/A"
        if speed > 0:
            eta = (total - current) / speed
            eta_str = time.strftime("%H:%M:%S", time.gmtime(eta)) if eta >= 0 else "N/A"

        bar_len=10
        filled_len = min(bar_len, int(bar_len * current / total)) if current >=0 else 0
        bar = '█' * filled_len + '░' * (bar_len - filled_len)
        size_str += f" / {(total / 1024 / 1024):.1f}MB"

        progress_text += f"[{bar}] {percent_str}\n{size_str}\nSpeed: {speed_str} | ETA: {eta_str}"
    else:
        # If total size is unknown
        progress_text += f"{size_str} Downloaded\nSpeed: {speed_str}"

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=progress_text
        )
        context.chat_data['last_download_edit_time'] = now # Update last edit time
    except PTBBadRequest as e:
        if "Message is not modified" in str(e): pass # Ignore if same text
        elif "Message to edit not found" in str(e):
             logger.warning(f"DL Prog: Status message {message_id} gone.")
             # Signal to stop trying to edit this message? Maybe by setting message_id to None?
             # For now, just log and continue download. Caller needs robust message_id check.
             # Or we could modify context.chat_data to indicate message is gone.
             context.chat_data.pop(f'dl_status_msg_{message_id}', None) # Indicate message gone
        else: logger.error(f"DL Prog Edit Error (PTB): {e}")
    except PTBFlood as e: # Catch PTB Flood (might need import from telegram.error)
        logger.warning(f"DL Prog Edit FloodWait: sleeping {e.retry_after}s")
        await asyncio.sleep(e.retry_after + 1)
        context.chat_data['last_download_edit_time'] = time.time() + e.retry_after # Prevent immediate retry
    except Exception as e: logger.error(f"Unexpected DL Prog Edit Error: {e}", exc_info=False)


# --- nzbCloud Downloader ---
async def download_files_nzbcloud(urls, filenames, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    """ Downloads nzb files with progress, splits, uploads. """
    cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://app.nzbcloud.com/"}
    failed_sources = []
    chat_id = update.effective_chat.id
    download_dir = context.bot_data.get('download_dir', '/content/downloads')
    delete_after_upload = context.bot_data.get('delete_after_upload', True)

    for idx, (url, file_name) in enumerate(zip(urls, filenames)):
        url, file_name = url.strip(), file_name.strip()
        if not url or not file_name: logger.warning(f"Skip nzb: URL/FN missing {idx+1}."); failed_sources.append(url or f"No URL for {file_name}"); continue

        file_name = clean_filename(file_name)
        full_file_path = os.path.join(download_dir, file_name)
        download_success = False
        status_message = None
        status_message_id = None

        try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
        except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); failed_sources.append(url); continue

        # Send initial status message
        try:
            status_message = await context.bot.send_message(chat_id, f"⬇️ Preparing download [{idx+1}/{len(urls)}]: {file_name}...")
            status_message_id = status_message.message_id
            context.chat_data[f'dl_status_msg_{status_message_id}'] = True # Mark message as active
        except Exception as initial_msg_err:
             logger.error(f"Failed to send initial DL status msg: {initial_msg_err}")
             status_message_id = None # Cannot show progress

        logger.info(f"DL nzb: '{file_name}' -> {full_file_path}")
        response = None
        try:
            response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            block_size = 1024 * 1024 # 1MB read chunks
            downloaded_size = 0
            download_start_time = time.time()
            context.chat_data['last_download_edit_time'] = 0 # Reset timer for this file

            with open(full_file_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=block_size):
                    if chunk:
                        file.write(chunk)
                        downloaded_size += len(chunk)
                        # Update progress if status message exists
                        if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
                            await _edit_download_progress(context, chat_id, status_message_id, downloaded_size, total_size, download_start_time, file_name)

            # Final success status edit
            if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
                try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=f"✅ DL OK: {file_name}. Processing...")
                except Exception: pass # Ignore final edit error
            else: # If initial msg failed or status msg gone, send new confirm
                await context.bot.send_message(chat_id, f"✅ DL OK: {file_name}")

            logger.info(f"DL OK (nzb): '{file_name}'"); download_success = True

        except Exception as e:
            logger.error(f"DL Fail/Error (nzb) '{file_name}': {e}", exc_info=True);
            if url not in failed_sources: failed_sources.append(url)
            err_msg = f"❌ DL Fail (nzb): {file_name}\n{e}"
            # Try to edit status message to show failure
            if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
                 try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=err_msg)
                 except Exception: await context.bot.send_message(chat_id, err_msg) # Send if edit fails
            else: await context.bot.send_message(chat_id, err_msg) # Send if no initial status msg

        finally:
            if response: response.close()
            # Clean up chat_data marker
            if status_message_id: context.chat_data.pop(f'dl_status_msg_{status_message_id}', None)

        # --- Splitting & Upload Step (Unchanged from previous version) ---
        if download_success:
            parts = await split_if_needed(full_file_path, context, chat_id)
            if parts:
                all_ok = True; total = len(parts)
                for i, part_path in enumerate(parts):
                    part_cap = f"{file_name} (Part {i+1}/{total})" if total > 1 else file_name
                    upload_ok = await upload_file_pyrogram(pyrogram_client, update, context, part_path, part_cap)
                    if not upload_ok: all_ok = False; break
                if delete_after_upload and parts and all_ok: await cleanup_split_parts(full_file_path, parts)
                if not all_ok and url not in failed_sources: failed_sources.append(url)
            else:
                 if url not in failed_sources: failed_sources.append(url)
        # --- End Splitting & Upload Step ---

    return failed_sources

# --- DeltaLeech Downloader ---
async def download_file_deltaleech(url, file_name, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    """ Downloads single Delta file with progress, splits, uploads. Returns bool success."""
    cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}; headers = {"User-Agent": "Mozilla/5.0", "Referer": url}
    chat_id = update.effective_chat.id; file_name = clean_filename(file_name)
    download_dir = context.bot_data.get('download_dir', '/content/downloads'); delete_after_upload = context.bot_data.get('delete_after_upload', True)
    full_file_path = os.path.join(download_dir, file_name); download_success = False
    status_message = None; status_message_id = None

    try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
    except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); return False

    # Send initial status message
    try: status_message = await context.bot.send_message(chat_id, f"⬇️ Preparing DL (delta): {file_name}..."); status_message_id = status_message.message_id; context.chat_data[f'dl_status_msg_{status_message_id}'] = True
    except Exception as e: logger.error(f"Failed initial DL status: {e}"); status_message_id = None

    logger.info(f"DL delta: '{file_name}' -> {full_file_path}")
    response = None
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
        response.raise_for_status();
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024 * 1024; downloaded_size = 0; download_start_time = time.time()
        context.chat_data['last_download_edit_time'] = 0

        with open(full_file_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=block_size):
                if chunk:
                    file.write(chunk); downloaded_size += len(chunk)
                    if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
                         await _edit_download_progress(context, chat_id, status_message_id, downloaded_size, total_size, download_start_time, file_name)

        if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
             try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=f"✅ DL OK: {file_name}. Processing...")
             except Exception: pass
        else: await context.bot.send_message(chat_id, f"✅ DL OK: {file_name}")
        logger.info(f"DL OK (delta): '{file_name}'"); download_success = True

    except Exception as e:
        logger.error(f"DL Fail/Error (delta) '{file_name}': {e}", exc_info=True)
        err_msg = f"❌ DL Fail (delta): {file_name}\n{e}"
        if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
             try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=err_msg)
             except Exception: await context.bot.send_message(chat_id, err_msg)
        else: await context.bot.send_message(chat_id, err_msg)
        download_success = False # Ensure flag is False
    finally:
        if response: response.close()
        if status_message_id: context.chat_data.pop(f'dl_status_msg_{status_message_id}', None)

    # --- Splitting & Upload Step (Unchanged) ---
    if download_success:
        parts = await split_if_needed(full_file_path, context, chat_id)
        if parts:
            all_ok = True; total = len(parts)
            for i, part_path in enumerate(parts):
                part_cap = f"{file_name} (Part {i+1}/{total})" if total > 1 else file_name
                upload_ok = await upload_file_pyrogram(pyrogram_client, update, context, part_path, part_cap)
                if not upload_ok: all_ok = False; break
            if delete_after_upload and parts and all_ok: await cleanup_split_parts(full_file_path, parts)
            return all_ok
        else: return False # Splitting failed
    else: return False # Download failed

async def download_multiple_files_deltaleech(urls, file_names, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    # ... (function unchanged) ...
    if len(urls) != len(file_names): logger.error("Delta URL/FN mismatch!"); await context.bot.send_message(update.effective_chat.id, "❌ URL/FN counts mismatch."); return urls
    failed_sources = [];
    for idx, (url, file_name) in enumerate(zip(urls, file_names)):
        url, file_name = url.strip(), file_name.strip()
        if not file_name: logger.warning(f"Skip Delta: No FN for {url}"); failed_sources.append(url); continue
        success = await download_file_deltaleech(url, file_name, cf_clearance, update, context, pyrogram_client)
        if not success and url not in failed_sources: failed_sources.append(url)
    return failed_sources

# --- Bitso Downloader ---
async def download_file_bitso(url, file_name, referer_url, id_cookie, sess_cookie, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    """ Downloads single Bitso file with progress, splits, uploads. Returns bool success."""
    cookies = {}; headers = {"Referer": referer_url, "User-Agent": "Mozilla/5.0"}
    if id_cookie: cookies["_identity"] = id_cookie
    if sess_cookie: cookies["PHPSESSID"] = sess_cookie
    chat_id = update.effective_chat.id; file_name = clean_filename(file_name)
    download_dir = context.bot_data.get('download_dir', '/content/downloads'); delete_after_upload = context.bot_data.get('delete_after_upload', True)
    full_file_path = os.path.join(download_dir, file_name); download_success = False
    status_message = None; status_message_id = None

    try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
    except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); return False

    # Send initial status message
    try: status_message = await context.bot.send_message(chat_id, f"⬇️ Preparing DL (bitso): {file_name}..."); status_message_id = status_message.message_id; context.chat_data[f'dl_status_msg_{status_message_id}'] = True
    except Exception as e: logger.error(f"Failed initial DL status: {e}"); status_message_id = None

    logger.info(f"DL bitso: '{file_name}' -> {full_file_path}")
    response = None
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024 * 1024; downloaded_size = 0; download_start_time = time.time()
        context.chat_data['last_download_edit_time'] = 0

        with open(full_file_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=block_size):
                if chunk:
                    file.write(chunk); downloaded_size += len(chunk)
                    if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
                         await _edit_download_progress(context, chat_id, status_message_id, downloaded_size, total_size, download_start_time, file_name)

        if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
             try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=f"✅ DL OK: {file_name}. Processing...")
             except Exception: pass
        else: await context.bot.send_message(chat_id, f"✅ DL OK: {file_name}")
        logger.info(f"DL OK (bitso): '{file_name}'"); download_success = True

    except Exception as e:
        logger.error(f"DL Fail/Error (bitso) '{file_name}': {e}", exc_info=True)
        err_msg = f"❌ DL Fail (bitso): {file_name}\n{e}"
        if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
             try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=err_msg)
             except Exception: await context.bot.send_message(chat_id, err_msg)
        else: await context.bot.send_message(chat_id, err_msg)
        download_success = False
    finally:
        if response: response.close()
        if status_message_id: context.chat_data.pop(f'dl_status_msg_{status_message_id}', None)

    # --- Splitting & Upload Step (Unchanged) ---
    if download_success:
        parts = await split_if_needed(full_file_path, context, chat_id)
        if parts:
            all_ok = True; total = len(parts)
            for i, part_path in enumerate(parts):
                part_cap = f"{file_name} (Part {i+1}/{total})" if total > 1 else file_name
                upload_ok = await upload_file_pyrogram(pyrogram_client, update, context, part_path, part_cap)
                if not upload_ok: all_ok = False; break
            if delete_after_upload and parts and all_ok: await cleanup_split_parts(full_file_path, parts)
            return all_ok
        else: return False # Splitting failed
    else: return False # Download failed

async def download_multiple_files_bitso(urls, file_names, referer_url, id_cookie, sess_cookie, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    # ... (function unchanged) ...
    if len(urls) != len(file_names): logger.error("Bitso URL/FN mismatch!"); await context.bot.send_message(update.effective_chat.id, "❌ URL/FN counts mismatch."); return urls
    failed_sources = [];
    for url, file_name in zip(urls, file_names):
        success = await download_file_bitso(url.strip(), file_name.strip(), referer_url, id_cookie, sess_cookie, update, context, pyrogram_client)
        if not success and url.strip() not in failed_sources: failed_sources.append(url.strip())
    return failed_sources
