# downloaders.py
# Contains download logic for different services, now including file splitting check.

import logging
import asyncio
import requests
import os

# Import framework types
from pyrogram import Client
from telegram import Update
from telegram.ext import ContextTypes

# Import local helpers/config
from upload import upload_file_pyrogram
# Import utils including the new splitting functions
from utils import clean_filename, split_if_needed, cleanup_split_parts
import config # To access DOWNLOAD_DIR and DELETE_AFTER_UPLOAD implicitly

logger = logging.getLogger(__name__)

# --- nzbCloud Downloader ---
async def download_files_nzbcloud(urls, filenames, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    """
    Downloads files from nzbCloud, checks size, splits if needed, then uploads parts.
    Returns a list of source URLs that failed at any stage (download, split, upload).
    """
    cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://app.nzbcloud.com/"}
    failed_sources = [] # Store URLs that failed download OR split OR upload
    chat_id = update.effective_chat.id

    for idx, (url, file_name) in enumerate(zip(urls, filenames)):
        url, file_name = url.strip(), file_name.strip()
        if not url or not file_name:
            logger.warning(f"Skip nzb: URL/FN missing pair {idx+1}."); await context.bot.send_message(chat_id, f"⚠️ Skip {idx+1}: URL/FN missing.")
            failed_sources.append(url or f"Missing URL for {file_name}"); continue

        file_name = clean_filename(file_name)
        full_file_path = os.path.join(config.DOWNLOAD_DIR, file_name)
        download_success = False

        # --- Download Step ---
        try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
        except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); await context.bot.send_message(chat_id, f"❌ Dir error '{file_name}'. Skip."); failed_sources.append(url); continue

        await context.bot.send_message(chat_id, f"⬇️ [{idx+1}/{len(urls)}] DL (nzb): {file_name}")
        logger.info(f"DL nzb: '{file_name}' from {url}")
        response = None
        try:
            response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
            response.raise_for_status()
            block_size = 1024 * 1024
            with open(full_file_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=block_size):
                    if chunk: file.write(chunk)
            logger.info(f"DL OK (nzb): '{file_name}'"); await context.bot.send_message(chat_id, f"✅ DL OK: {file_name}"); download_success = True
        except requests.exceptions.RequestException as e:
            logger.error(f"DL Fail (nzb) '{file_name}': {e}"); failed_sources.append(url)
            err_msg = f"❌ DL Fail (nzb): {file_name}\n{e}"; status = getattr(response, 'status_code', None)
            if status: err_msg += f"\nStatus: {status}"
            await context.bot.send_message(chat_id, err_msg)
        except Exception as e:
            logger.exception(f"DL Error (nzb) '{file_name}': {e}"); failed_sources.append(url); await context.bot.send_message(chat_id, f"❌ DL Error (nzb) '{file_name}': {e}")
        finally:
            if response: response.close()
        # --- End Download Step ---

        # --- Splitting & Upload Step ---
        if download_success:
            parts = await split_if_needed(full_file_path, context, chat_id) # Check size and split

            if parts: # Splitting check/process succeeded
                all_parts_uploaded = True
                total_parts = len(parts)
                original_source_failed = False # Track failure for reporting

                for i, part_path in enumerate(parts):
                    part_base_name = os.path.basename(part_path)
                    part_caption = f"{file_name} (Part {i+1}/{total_parts})" if total_parts > 1 else file_name
                    logger.info(f"Attempting upload for part {i+1}/{total_parts}: '{part_base_name}'")

                    # Upload the part (or original file if no split)
                    upload_ok = await upload_file_pyrogram(pyrogram_client, update, context, part_path, part_caption)

                    if not upload_ok:
                        logger.error(f"Upload failed for part {i+1}/{total_parts}: '{part_base_name}'")
                        all_parts_uploaded = False
                        original_source_failed = True # Mark original source as failed
                        break # Don't attempt remaining parts

                # Cleanup after attempting all parts
                if config.DELETE_AFTER_UPLOAD and parts:
                    if all_parts_uploaded:
                        # If all uploaded successfully, clean up parts and original
                        await cleanup_split_parts(full_file_path, parts)
                    else:
                        logger.warning(f"Upload failed for >=1 part of {file_name}, cleanup skipped.")

                # Mark original URL as failed if any part failed upload
                if original_source_failed and url not in failed_sources:
                    failed_sources.append(url)

            else: # split_if_needed returned None (error during split)
                logger.error(f"Splitting failed for '{file_name}', adding to failed sources.")
                if url not in failed_sources: failed_sources.append(url)
        # --- End Splitting & Upload Step ---

    return failed_sources # Return list of failed source URLs

# --- DeltaLeech Downloader ---
async def download_file_deltaleech(url, file_name, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    """
    Downloads a single file from DeltaLeech, splits if needed, uploads parts.
    Returns True on complete success (download + all uploads), False otherwise.
    """
    cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": url}
    chat_id = update.effective_chat.id
    file_name = clean_filename(file_name)
    full_file_path = os.path.join(config.DOWNLOAD_DIR, file_name)
    download_success = False

    # --- Download Step ---
    try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
    except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); await context.bot.send_message(chat_id, f"❌ Dir error '{file_name}'. Skip."); return False

    await context.bot.send_message(chat_id, f"⬇️ DL (delta): {file_name}"); logger.info(f"DL delta: '{file_name}' from {url}")
    response = None
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
        response.raise_for_status()
        block_size = 1024 * 1024
        with open(full_file_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=block_size):
                if chunk: file.write(chunk)
        logger.info(f"DL OK (delta): '{file_name}'"); await context.bot.send_message(chat_id, f"✅ DL OK: {file_name}"); download_success = True
    except requests.exceptions.RequestException as e:
        logger.error(f"DL Fail (delta) '{file_name}': {e}"); status = getattr(response, 'status_code', None)
        err_msg = f"❌ DL Fail (delta): {file_name}\n{e}" + (f"\nStatus: {status}" if status else "")
        await context.bot.send_message(chat_id, err_msg); return False
    except Exception as e: logger.exception(f"DL Error (delta) '{file_name}': {e}"); await context.bot.send_message(chat_id, f"❌ DL Error (delta) '{file_name}': {e}"); return False
    finally:
        if response: response.close()
    # --- End Download Step ---

    # --- Splitting & Upload Step ---
    if download_success:
        parts = await split_if_needed(full_file_path, context, chat_id)
        if parts:
            all_parts_uploaded = True; total_parts = len(parts)
            for i, part_path in enumerate(parts):
                part_base_name = os.path.basename(part_path)
                part_caption = f"{file_name} (Part {i+1}/{total_parts})" if total_parts > 1 else file_name
                logger.info(f"Attempting upload for part {i+1}/{total_parts}: '{part_base_name}'")
                upload_ok = await upload_file_pyrogram(pyrogram_client, update, context, part_path, part_caption)
                if not upload_ok: logger.error(f"Upload failed part {i+1}/{total_parts}: '{part_base_name}'"); all_parts_uploaded = False; break

            if config.DELETE_AFTER_UPLOAD and parts:
                if all_parts_uploaded: await cleanup_split_parts(full_file_path, parts)
                else: logger.warning(f"Upload failed >=1 part of {file_name}, cleanup skipped.")

            return all_parts_uploaded # Return True only if all parts uploaded
        else: # Splitting failed
            logger.error(f"Splitting failed for '{file_name}'.")
            return False
    else: # Download failed
        return False
    # --- End Splitting & Upload Step ---

async def download_multiple_files_deltaleech(urls, file_names, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    """Downloads multiple DeltaLeech files, calling single download/split/upload. Returns list of failed source URLs."""
    if len(urls) != len(file_names): logger.error("Delta URL/FN mismatch!"); await context.bot.send_message(update.effective_chat.id, "❌ URL/FN counts mismatch."); return urls
    failed_sources = []
    for idx, (url, file_name) in enumerate(zip(urls, file_names)):
        url, file_name = url.strip(), file_name.strip()
        if not file_name: await context.bot.send_message(update.effective_chat.id, f"⚠️ Skip [{idx+1}/{len(urls)}]: No FN for {url}"); logger.warning(f"Skip Delta: No FN for {url}"); failed_sources.append(url); continue
        # Call the single function which now returns True/False
        success = await download_file_deltaleech(url, file_name, cf_clearance, update, context, pyrogram_client)
        if not success:
            # Add original URL to failed list if download or any part upload failed
            if url not in failed_sources:
                 failed_sources.append(url)
    return failed_sources

# --- Bitso Downloader ---
async def download_file_bitso(url, file_name, referer_url, id_cookie, sess_cookie, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    """
    Downloads a single file from Bitso, splits if needed, uploads parts.
    Returns True on complete success (download + all uploads), False otherwise.
    """
    cookies = {}; headers = {"Referer": referer_url, "User-Agent": "Mozilla/5.0"}
    if id_cookie: cookies["_identity"] = id_cookie
    if sess_cookie: cookies["PHPSESSID"] = sess_cookie
    chat_id = update.effective_chat.id; file_name = clean_filename(file_name)
    full_file_path = os.path.join(config.DOWNLOAD_DIR, file_name); download_success = False

    # --- Download Step ---
    try: os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
    except OSError as e: logger.error(f"Dir error {full_file_path}: {e}"); await context.bot.send_message(chat_id, f"❌ Dir error '{file_name}'. Skip."); return False

    await context.bot.send_message(chat_id, f"⬇️ DL (bitso): {file_name}"); logger.info(f"DL bitso: '{file_name}' from {url}")
    response = None
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, stream=True, timeout=180)
        response.raise_for_status()
        block_size = 1024 * 1024
        with open(full_file_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=block_size):
                if chunk: file.write(chunk)
        logger.info(f"DL OK (bitso): '{file_name}'"); await context.bot.send_message(chat_id, f"✅ DL OK: {file_name}"); download_success = True
    except requests.exceptions.RequestException as e:
        logger.error(f"DL Fail (bitso) '{file_name}': {e}"); status = getattr(response, 'status_code', None)
        err_msg = f"❌ DL Fail (bitso): {file_name}\n{e}" + (f"\nStatus: {status}" if status else "")
        await context.bot.send_message(chat_id, err_msg); return False
    except Exception as e: logger.exception(f"DL Error (bitso) '{file_name}': {e}"); await context.bot.send_message(chat_id, f"❌ DL Error (bitso) '{file_name}': {e}"); return False
    finally:
        if response: response.close()
    # --- End Download Step ---

    # --- Splitting & Upload Step ---
    if download_success:
        parts = await split_if_needed(full_file_path, context, chat_id)
        if parts:
            all_parts_uploaded = True; total_parts = len(parts)
            for i, part_path in enumerate(parts):
                part_base_name = os.path.basename(part_path)
                part_caption = f"{file_name} (Part {i+1}/{total_parts})" if total_parts > 1 else file_name
                logger.info(f"Attempting upload for part {i+1}/{total_parts}: '{part_base_name}'")
                upload_ok = await upload_file_pyrogram(pyrogram_client, update, context, part_path, part_caption)
                if not upload_ok: logger.error(f"Upload failed part {i+1}/{total_parts}: '{part_base_name}'"); all_parts_uploaded = False; break

            if config.DELETE_AFTER_UPLOAD and parts:
                if all_parts_uploaded: await cleanup_split_parts(full_file_path, parts)
                else: logger.warning(f"Upload failed >=1 part of {file_name}, cleanup skipped.")

            return all_parts_uploaded # Return True only if all parts uploaded
        else: # Splitting failed
            logger.error(f"Splitting failed for '{file_name}'.")
            return False
    else: # Download failed
        return False
    # --- End Splitting & Upload Step ---

async def download_multiple_files_bitso(urls, file_names, referer_url, id_cookie, sess_cookie, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    """Downloads multiple Bitso files, calling single download/split/upload. Returns list of failed source URLs."""
    if len(urls) != len(file_names): logger.error("Bitso URL/FN mismatch!"); await context.bot.send_message(update.effective_chat.id, "❌ URL/FN counts mismatch."); return urls
    failed_sources = []
    for idx, (url, file_name) in enumerate(zip(urls, file_names)):
        url, file_name = url.strip(), file_name.strip()
        # Call the single function which now returns True/False
        success = await download_file_bitso(url, file_name, referer_url, id_cookie, sess_cookie, update, context, pyrogram_client)
        if not success:
             # Add original URL to failed list if download or any part upload failed
            if url not in failed_sources:
                 failed_sources.append(url)
    return failed_sources
