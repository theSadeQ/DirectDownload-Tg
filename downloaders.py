# downloaders.py
import logging
import asyncio
import requests
import os

from pyrogram import Client
from telegram import Update
from telegram.ext import ContextTypes

# Import local helpers/config
from upload import upload_file_pyrogram
from utils import clean_filename
import config # To access DOWNLOAD_DIR, implicitly

logger = logging.getLogger(__name__)

# --- nzbCloud ---
async def download_files_nzbcloud(urls, filenames, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://app.nzbcloud.com/"}
    failed_sources = []
    chat_id = update.effective_chat.id

    for idx, (url, file_name) in enumerate(zip(urls, filenames)):
        url, file_name = url.strip(), file_name.strip()
        if not url or not file_name:
            logger.warning(f"Skip nzb: URL/FN missing pair {idx+1}."); await context.bot.send_message(chat_id, f"⚠️ Skip {idx+1}: URL/FN missing.")
            failed_sources.append(url or f"Missing URL for {file_name}"); continue

        file_name = clean_filename(file_name)
        full_file_path = os.path.join(config.DOWNLOAD_DIR, file_name)
        download_success = False
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

        if download_success:
            upload_success = await upload_file_pyrogram(pyrogram_client, update, context, full_file_path, file_name)
            if not upload_success: failed_sources.append(url)
    return failed_sources

# --- DeltaLeech ---
async def download_file_deltaleech(url, file_name, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": url}
    chat_id = update.effective_chat.id
    file_name = clean_filename(file_name)
    full_file_path = os.path.join(config.DOWNLOAD_DIR, file_name)
    download_success = False
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

    if download_success: return await upload_file_pyrogram(pyrogram_client, update, context, full_file_path, file_name)
    else: return False

async def download_multiple_files_deltaleech(urls, file_names, cf_clearance, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    if len(urls) != len(file_names): logger.error("Delta URL/FN mismatch!"); await context.bot.send_message(update.effective_chat.id, "❌ URL/FN counts mismatch."); return urls
    failed_sources = []
    for idx, (url, file_name) in enumerate(zip(urls, file_names)):
        url, file_name = url.strip(), file_name.strip()
        if not file_name: await context.bot.send_message(update.effective_chat.id, f"⚠️ Skip [{idx+1}/{len(urls)}]: No FN for {url}"); logger.warning(f"Skip Delta: No FN for {url}"); failed_sources.append(url); continue
        success = await download_file_deltaleech(url, file_name, cf_clearance, update, context, pyrogram_client)
        if not success: failed_sources.append(url)
    return failed_sources

# --- Bitso ---
async def download_file_bitso(url, file_name, referer_url, id_cookie, sess_cookie, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    cookies = {}; headers = {"Referer": referer_url, "User-Agent": "Mozilla/5.0"}
    if id_cookie: cookies["_identity"] = id_cookie
    if sess_cookie: cookies["PHPSESSID"] = sess_cookie
    chat_id = update.effective_chat.id; file_name = clean_filename(file_name)
    full_file_path = os.path.join(config.DOWNLOAD_DIR, file_name); download_success = False
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

    if download_success: return await upload_file_pyrogram(pyrogram_client, update, context, full_file_path, file_name)
    else: return False

async def download_multiple_files_bitso(urls, file_names, referer_url, id_cookie, sess_cookie, update: Update, context: ContextTypes.DEFAULT_TYPE, pyrogram_client: Client):
    if len(urls) != len(file_names): logger.error("Bitso URL/FN mismatch!"); await context.bot.send_message(update.effective_chat.id, "❌ URL/FN counts mismatch."); return urls
    failed_sources = []
    for url, file_name in zip(urls, file_names):
        success = await download_file_bitso(url.strip(), file_name.strip(), referer_url, id_cookie, sess_cookie, update, context, pyrogram_client)
        if not success: failed_sources.append(url.strip())
    return failed_sources
