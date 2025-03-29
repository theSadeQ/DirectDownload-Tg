# downloaders.py
# Contains download logic with progress reporting. Accesses config via context.

import logging
import asyncio
import requests
import os
import time
import math

# Import framework types
from pyrogram import Client
from telegram import Update
from telegram.ext import ContextTypes
# Corrected import for PTB exceptions
from telegram.error import BadRequest as PTBBadRequest, RetryAfter

# Import local helpers
try:
    from upload import upload_file_pyrogram
except ImportError as e:
    logging.basicConfig(level=logging.ERROR); logging.error(f"Failed import upload: {e}"); raise
try:
    from utils import clean_filename, split_if_needed, cleanup_split_parts
except ImportError as e:
    logging.basicConfig(level=logging.ERROR); logging.error(f"Failed import utils: {e}"); raise

logger = logging.getLogger(__name__)

# --- Download Progress Helper ---
async def _edit_download_progress(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int | None, current: int, total: int, start_time: float, file_name: str):
    """Helper to format and edit download progress messages, with throttling."""
    if not message_id or not context.chat_data.get(f'dl_status_msg_{message_id}'): return # Check if message exists/is valid

    now = time.time()
    last_edit = context.chat_data.get(f'dl_last_edit_{message_id}', 0)
    throttle_interval = 5.0 # seconds
    if now - last_edit < throttle_interval: return

    elapsed_time = now - start_time
    speed = current / elapsed_time if elapsed_time > 0 else 0; speed_str = f"{speed/1024/1024:.2f} MB/s" if speed > 0 else "N/A"
    progress_text = f"⬇️ Downloading: {file_name}\n"; size_str = f"{(current/1024/1024):.1f}MB"

    if total > 0:
        percent = round((current / total) * 100, 1); percent_str = f"{percent}%"; eta_str = "N/A"
        if speed > 0: eta = (total - current) / speed; eta_str = time.strftime("%H:%M:%S", time.gmtime(eta)) if eta >= 0 else "N/A"
        bar_len=10; filled_len = min(bar_len, int(bar_len*current/total)) if current >=0 else 0; bar = '█'*filled_len + '░'*(bar_len-filled_len)
        size_str += f" / {(total/1024/1024):.1f}MB"; progress_text += f"[{bar}] {percent_str}\n{size_str}\nSpeed: {speed_str} | ETA: {eta_str}"
    else: progress_text += f"{size_str} Downloaded\nSpeed: {speed_str}"

    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=progress_text)
        context.chat_data[f'dl_last_edit_{message_id}'] = now
    except PTBBadRequest as e:
        if "modified" in str(e): pass
        elif "not found" in str(e): logger.warning(f"DL Prog: Status msg {message_id} gone."); context.chat_data.pop(f'dl_status_msg_{message_id}', None) # Mark as gone
        else: logger.error(f"DL Prog Edit Error (PTB): {e}")
    except RetryAfter as e: logger.warning(f"DL Prog Edit Rate Limit: sleep {e.retry_after}s"); await asyncio.sleep(e.retry_after+1); context.chat_data[f'dl_last_edit_{message_id}'] = time.time()+e.retry_after
    except Exception as e: logger.error(f"Unexpected DL Prog Edit Error: {e}", exc_info=False)


# --- nzbCloud Downloader ---
async def download_files_nzbcloud(urls, filenames, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}; headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://app.nzbcloud.com/"}
    failed_sources = []; chat_id = update.effective_chat.id
    download_dir = context.bot_data.get('download_dir', '/content/downloads'); delete_after_upload = context.bot_data.get('delete_after_upload', True)
    for idx, (url, file_name) in enumerate(zip(urls, filenames)):
        url, file_name = url.strip(), file_name.strip()
        if not url or not file_name: logger.warning(f"Skip nzb: URL/FN missing {idx+1}."); failed_sources.append(url or f"No URL for {file_name}"); continue
        file_name = clean_filename(file_name); full_file_path = os.path.join(download_dir, file_name); download_success = False
        status_message = None; status_message_id = None
        try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
        except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); failed_sources.append(url); continue
        try: status_message = await context.bot.send_message(chat_id, f"⬇️ Prep DL [{idx+1}/{len(urls)}]: {file_name}..."); status_message_id = status_message.message_id; context.chat_data[f'dl_status_msg_{status_message_id}'] = True
        except Exception as e: logger.error(f"Failed initial DL status: {e}"); status_message_id = None
        logger.info(f"DL nzb: '{file_name}' -> {full_file_path}"); response = None
        try:
            response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
            response.raise_for_status(); total_size = int(response.headers.get('content-length', 0)); block_size = 1024*1024
            downloaded_size = 0; download_start_time = time.time(); context.chat_data[f'dl_last_edit_{status_message_id}'] = 0 if status_message_id else None # Reset timer
            with open(full_file_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=block_size):
                    if chunk: file.write(chunk); downloaded_size += len(chunk)
                    if status_message_id: await _edit_download_progress(context, chat_id, status_message_id, downloaded_size, total_size, download_start_time, file_name)
            final_dl_msg = f"✅ DL OK: {file_name}. Processing..."
            if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
                try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=final_dl_msg)
                except Exception: await context.bot.send_message(chat_id, final_dl_msg) # Send if edit fails
            elif status_message_id is None: await context.bot.send_message(chat_id, final_dl_msg) # Send if initial msg failed
            logger.info(f"DL OK (nzb): '{file_name}'"); download_success = True
        except Exception as e:
            logger.error(f"DL Fail/Error (nzb) '{file_name}': {e}", exc_info=True);
            if url not in failed_sources: failed_sources.append(url)
            err_msg = f"❌ DL Fail (nzb): {file_name}\n{e}"
            if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
                 try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=err_msg)
                 except Exception: await context.bot.send_message(chat_id, err_msg)
            else: await context.bot.send_message(chat_id, err_msg)
        finally:
            if response: response.close()
            if status_message_id: context.chat_data.pop(f'dl_status_msg_{status_message_id}', None); context.chat_data.pop(f'dl_last_edit_{status_message_id}', None)
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
    return failed_sources

# --- DeltaLeech Downloader ---
async def download_file_deltaleech(url, file_name, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}; headers = {"User-Agent": "Mozilla/5.0", "Referer": url}
    chat_id = update.effective_chat.id; file_name = clean_filename(file_name); download_dir = context.bot_data.get('download_dir', '/content/downloads'); delete_after_upload = context.bot_data.get('delete_after_upload', True)
    full_file_path = os.path.join(download_dir, file_name); download_success = False; status_message = None; status_message_id = None
    try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
    except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); return False
    try: status_message = await context.bot.send_message(chat_id, f"⬇️ Prep DL (delta): {file_name}..."); status_message_id = status_message.message_id; context.chat_data[f'dl_status_msg_{status_message_id}'] = True
    except Exception as e: logger.error(f"Failed initial DL status: {e}"); status_message_id = None
    logger.info(f"DL delta: '{file_name}' -> {full_file_path}"); response = None
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
        response.raise_for_status(); total_size = int(response.headers.get('content-length', 0)); block_size = 1024*1024
        downloaded_size = 0; download_start_time = time.time(); context.chat_data[f'dl_last_edit_{status_message_id}'] = 0 if status_message_id else None
        with open(full_file_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=block_size):
                if chunk: file.write(chunk); downloaded_size += len(chunk)
                if status_message_id: await _edit_download_progress(context, chat_id, status_message_id, downloaded_size, total_size, download_start_time, file_name)
        final_dl_msg = f"✅ DL OK: {file_name}. Processing..."
        if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
             try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=final_dl_msg)
             except Exception: await context.bot.send_message(chat_id, final_dl_msg)
        elif status_message_id is None: await context.bot.send_message(chat_id, final_dl_msg)
        logger.info(f"DL OK (delta): '{file_name}'"); download_success = True
    except Exception as e:
        logger.error(f"DL Fail/Error (delta) '{file_name}': {e}", exc_info=True)
        err_msg = f"❌ DL Fail (delta): {file_name}\n{e}"
        if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
             try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=err_msg)
             except Exception: await context.bot.send_message(chat_id, err_msg)
        else: await context.bot.send_message(chat_id, err_msg)
        download_success = False
    finally:
        if response: response.close()
        if status_message_id: context.chat_data.pop(f'dl_status_msg_{status_message_id}', None); context.chat_data.pop(f'dl_last_edit_{status_message_id}', None)
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
        else: return False
    else: return False

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
    # ... (Function logic remains the same, but relies on corrected _edit_download_progress) ...
    cookies = {}; headers = {"Referer": referer_url, "User-Agent": "Mozilla/5.0"}
    if id_cookie: cookies["_identity"] = id_cookie
    if sess_cookie: cookies["PHPSESSID"] = sess_cookie
    chat_id = update.effective_chat.id; file_name = clean_filename(file_name); download_dir = context.bot_data.get('download_dir', '/content/downloads'); delete_after_upload = context.bot_data.get('delete_after_upload', True)
    full_file_path = os.path.join(download_dir, file_name); download_success = False; status_message = None; status_message_id = None
    try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
    except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); return False
    try: status_message = await context.bot.send_message(chat_id, f"⬇️ Prep DL (bitso): {file_name}..."); status_message_id = status_message.message_id; context.chat_data[f'dl_status_msg_{status_message_id}'] = True
    except Exception as e: logger.error(f"Failed initial DL status: {e}"); status_message_id = None
    logger.info(f"DL bitso: '{file_name}' -> {full_file_path}"); response = None
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
        response.raise_for_status(); total_size = int(response.headers.get('content-length', 0)); block_size = 1024*1024
        downloaded_size = 0; download_start_time = time.time(); context.chat_data[f'dl_last_edit_{status_message_id}'] = 0 if status_message_id else None
        with open(full_file_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=block_size):
                if chunk: file.write(chunk); downloaded_size += len(chunk)
                if status_message_id: await _edit_download_progress(context, chat_id, status_message_id, downloaded_size, total_size, download_start_time, file_name)
        final_dl_msg = f"✅ DL OK: {file_name}. Processing..."
        if status_message_id and context.chat_data.get(f'dl_status_msg_{status_message_id}'):
             try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=final_dl_msg)
             except Exception: await context.bot.send_message(chat_id, final_dl_msg)
        elif status_message_id is None: await context.bot.send_message(chat_id, final_dl_msg)
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
        if status_message_id: context.chat_data.pop(f'dl_status_msg_{status_message_id}', None); context.chat_data.pop(f'dl_last_edit_{status_message_id}', None)
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
        else: return False
    else: return False

async def download_multiple_files_bitso(urls, file_names, referer_url, id_cookie, sess_cookie, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    # ... (function unchanged) ...
    if len(urls) != len(file_names): logger.error("Bitso URL/FN mismatch!"); await context.bot.send_message(update.effective_chat.id, "❌ URL/FN counts mismatch."); return urls
    failed_sources = [];
    for url, file_name in zip(urls, file_names):
        success = await download_file_bitso(url.strip(), file_name.strip(), referer_url, id_cookie, sess_cookie, update, context, pyrogram_client)
        if not success and url.strip() not in failed_sources: failed_sources.append(url.strip())
    return failed_sources
