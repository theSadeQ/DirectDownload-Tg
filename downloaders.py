# downloaders.py
# MODIFIED: Accesses config (DOWNLOAD_DIR, DELETE_AFTER_UPLOAD) via context.bot_data

import logging
import asyncio
import requests
import os

from pyrogram import Client
from telegram import Update
from telegram.ext import ContextTypes

# Import local helpers/config
from upload import upload_file_pyrogram
# Import utils including the new splitting functions
from utils import clean_filename, split_if_needed, cleanup_split_parts

# NO 'import config' needed here anymore

logger = logging.getLogger(__name__)

# --- nzbCloud Downloader ---
async def download_files_nzbcloud(urls, filenames, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    """ Downloads nzb files, splits if needed, uploads parts. Accesses config via context. """
    cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://app.nzbcloud.com/"}
    failed_sources = []
    chat_id = update.effective_chat.id
    # Get config from context
    download_dir = context.bot_data.get('download_dir', '/content/downloads') # Provide fallback
    delete_after_upload = context.bot_data.get('delete_after_upload', True)

    for idx, (url, file_name) in enumerate(zip(urls, filenames)):
        url, file_name = url.strip(), file_name.strip()
        if not url or not file_name: logger.warning(f"Skip nzb: URL/FN missing {idx+1}."); failed_sources.append(url or f"No URL for {file_name}"); continue

        file_name = clean_filename(file_name)
        full_file_path = os.path.join(download_dir, file_name) # Use download_dir from context
        download_success = False
        try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
        except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); failed_sources.append(url); continue

        await context.bot.send_message(chat_id, f"⬇️ [{idx+1}/{len(urls)}] DL (nzb): {file_name}")
        logger.info(f"DL nzb: '{file_name}' -> {full_file_path}")
        response = None
        try:
            response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
            response.raise_for_status()
            with open(full_file_path, "wb") as file: [file.write(chunk) for chunk in response.iter_content(1024*1024) if chunk]
            logger.info(f"DL OK (nzb): '{file_name}'"); await context.bot.send_message(chat_id, f"✅ DL OK: {file_name}"); download_success = True
        except Exception as e: logger.error(f"DL Fail (nzb) '{file_name}': {e}", exc_info=True); failed_sources.append(url); await context.bot.send_message(chat_id, f"❌ DL Fail (nzb): {file_name}\n{e}")
        finally:
            if response: response.close()

        if download_success:
            # Pass context to splitter
            parts = await split_if_needed(full_file_path, context, chat_id)
            if parts:
                all_ok = True; total = len(parts)
                for i, part_path in enumerate(parts):
                    part_cap = f"{file_name} (Part {i+1}/{total})" if total > 1 else file_name
                    upload_ok = await upload_file_pyrogram(pyrogram_client, update, context, part_path, part_cap)
                    if not upload_ok: all_ok = False; break
                if delete_after_upload and parts and all_ok: await cleanup_split_parts(full_file_path, parts)
                if not all_ok and url not in failed_sources: failed_sources.append(url)
            else: # Splitting failed
                 if url not in failed_sources: failed_sources.append(url)

    return failed_sources

# --- DeltaLeech Downloader ---
async def download_file_deltaleech(url, file_name, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    """ Downloads single Delta file, splits, uploads. Accesses config via context. Returns bool success."""
    cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}; headers = {"User-Agent": "Mozilla/5.0", "Referer": url}
    chat_id = update.effective_chat.id; file_name = clean_filename(file_name)
    download_dir = context.bot_data.get('download_dir', '/content/downloads'); delete_after_upload = context.bot_data.get('delete_after_upload', True)
    full_file_path = os.path.join(download_dir, file_name); download_success = False
    try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
    except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); return False

    await context.bot.send_message(chat_id, f"⬇️ DL (delta): {file_name}"); logger.info(f"DL delta: '{file_name}' -> {full_file_path}")
    response = None
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
        response.raise_for_status();
        with open(full_file_path, "wb") as file: [file.write(chunk) for chunk in response.iter_content(1024*1024) if chunk]
        logger.info(f"DL OK (delta): '{file_name}'"); await context.bot.send_message(chat_id, f"✅ DL OK: {file_name}"); download_success = True
    except Exception as e: logger.error(f"DL Fail (delta) '{file_name}': {e}", exc_info=True); await context.bot.send_message(chat_id, f"❌ DL Fail (delta): {file_name}\n{e}"); return False
    finally:
        if response: response.close()

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
    """ Downloads multiple Delta files. Returns list of failed source URLs. """
    if len(urls) != len(file_names): logger.error("Delta URL/FN mismatch!"); await context.bot.send_message(update.effective_chat.id, "❌ URL/FN counts mismatch."); return urls
    failed_sources = []
    for idx, (url, file_name) in enumerate(zip(urls, file_names)):
        url, file_name = url.strip(), file_name.strip()
        if not file_name: logger.warning(f"Skip Delta: No FN for {url}"); failed_sources.append(url); continue
        success = await download_file_deltaleech(url, file_name, cf_clearance, update, context, pyrogram_client) # Calls single which accesses context config
        if not success and url not in failed_sources: failed_sources.append(url)
    return failed_sources

# --- Bitso Downloader ---
async def download_file_bitso(url, file_name, referer_url, id_cookie, sess_cookie, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    """ Downloads single Bitso file, splits, uploads. Accesses config via context. Returns bool success."""
    cookies = {}; headers = {"Referer": referer_url, "User-Agent": "Mozilla/5.0"}
    if id_cookie: cookies["_identity"] = id_cookie; if sess_cookie: cookies["PHPSESSID"] = sess_cookie
    chat_id = update.effective_chat.id; file_name = clean_filename(file_name)
    download_dir = context.bot_data.get('download_dir', '/content/downloads'); delete_after_upload = context.bot_data.get('delete_after_upload', True)
    full_file_path = os.path.join(download_dir, file_name); download_success = False
    try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
    except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); return False

    await context.bot.send_message(chat_id, f"⬇️ DL (bitso): {file_name}"); logger.info(f"DL bitso: '{file_name}' -> {full_file_path}")
    response = None
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
        response.raise_for_status()
        with open(full_file_path, "wb") as file: [file.write(chunk) for chunk in response.iter_content(1024*1024) if chunk]
        logger.info(f"DL OK (bitso): '{file_name}'"); await context.bot.send_message(chat_id, f"✅ DL OK: {file_name}"); download_success = True
    except Exception as e: logger.error(f"DL Fail (bitso) '{file_name}': {e}", exc_info=True); await context.bot.send_message(chat_id, f"❌ DL Fail (bitso): {file_name}\n{e}"); return False
    finally:
        if response: response.close()

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
    """ Downloads multiple Bitso files. Returns list of failed source URLs. """
    if len(urls) != len(file_names): logger.error("Bitso URL/FN mismatch!"); await context.bot.send_message(update.effective_chat.id, "❌ URL/FN counts mismatch."); return urls
    failed_sources = []
    for url, file_name in zip(urls, file_names):
        success = await download_file_bitso(url.strip(), file_name.strip(), referer_url, id_cookie, sess_cookie, update, context, pyrogram_client) # Calls single which accesses context config
        if not success and url.strip() not in failed_sources: failed_sources.append(url.strip())
    return failed_sources
