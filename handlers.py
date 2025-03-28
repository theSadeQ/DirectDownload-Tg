# handlers.py
# Contains PTB handlers, conversation logic, and the reporting helper.
# MODIFIED: Added filename styling for manual inputs.

import logging
import asyncio
import io

# PTB imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters
)
from telegram.constants import ParseMode

# Import utils
from utils import (
    extract_filename_from_url,
    clean_filename,
    write_failed_downloads_to_file,
    apply_dot_style # <-- Import the new styling function
)
# Import downloaders
from downloaders import (
    download_files_nzbcloud,
    download_multiple_files_deltaleech,
    download_multiple_files_bitso
)

logger = logging.getLogger(__name__)

# --- Conversation States ---
CHOOSE_DOWNLOADER, GET_URLS, GET_FILENAMES_NZB, \
GET_FILENAMES_DELTA, CONFIRM_DELTA_FN, \
GET_URLS_BITSO, CONFIRM_BITSO_FN, GET_FILENAMES_BITSO = range(8)


# --- Helper for Running and Reporting ---
async def run_and_report_process(update: Update, context: ContextTypes.DEFAULT_TYPE, download_upload_task, service_name: str):
    # ... (function body unchanged) ...
    chat_id = update.effective_chat.id; failed_sources = []
    download_dir = context.bot_data.get('download_dir', '/content/downloads')
    try:
        failed_sources = await download_upload_task
        final_msg = f"🏁 <b>{service_name}</b> process finished."
        logger.info(f"{service_name} process OK for chat {chat_id}.")
        if failed_sources:
            final_msg += f"\n⚠️ Failed {len(failed_sources)} item(s)."
            logger.warning(f"{len(failed_sources)} failure(s) for {service_name} in {chat_id}.")
            failed_file_path = write_failed_downloads_to_file(failed_sources, service_name, download_dir)
            if failed_file_path:
                 escaped_path = failed_file_path.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                 final_msg += f"\nList saved to <pre>{escaped_path}</pre>"
        else:
            final_msg += f"\n✅🎉 All items OK!"
            logger.info(f"All {service_name} OK for {chat_id}.")
        await context.bot.send_message(chat_id, final_msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error run/report {service_name}: {e}", exc_info=True)
        try:
            error_text = str(e).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            await context.bot.send_message(chat_id, f"🚨 Error after <b>{service_name}</b>.\nError: <pre>{error_text[:500]}</pre>", parse_mode=ParseMode.HTML)
        except Exception as send_error: logger.error(f"Failed send final error msg {chat_id}: {send_error}")

# --- Simple Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (function unchanged) ...
    user = update.effective_user; upload_enabled = context.bot_data.get('upload_enabled', True); upload_mode = context.bot_data.get('upload_mode', 'N/A'); delete_after_upload = context.bot_data.get('delete_after_upload', True)
    cred_status = "API/Token Configured." if context.bot_data.get('pyrogram_client') else "⚠️ API/Token Error!"
    upload_status = f"✅ Uploads ON (Mode: {upload_mode})." if upload_enabled else "ℹ️ Uploads OFF."; delete_status = f"🗑️ Delete ON." if upload_enabled and delete_after_upload else "💾 Delete OFF."
    await update.message.reply_html(rf"Hi {user.mention_html()}!\n\n{cred_status}\n{upload_status}\n{delete_status}\n\nUse /download to start.",)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged, includes previous fix) ...
    user_id = update.effective_user.id if update.effective_user else "Unknown"; logger.info(f"User {user_id} cancelled.")
    message_text = "Download process cancelled."; query = update.callback_query
    try:
        if query: await query.answer(); await query.edit_message_text(message_text)
        elif update.message: await update.message.reply_text(message_text)
    except Exception as e:
        logger.warning(f"Failed send/edit cancel confirmation: {e}")
        try: await context.bot.send_message(update.effective_chat.id, message_text)
        except Exception as send_e: logger.error(f"Failed sending cancel confirmation fallback: {send_e}")
    context.user_data.clear(); return ConversationHandler.END

# --- Conversation Handlers ---

# Helper Function to process URLs (Unchanged)
async def _process_urls_and_proceed(urls: list[str], update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged) ...
    if not urls: await update.message.reply_text("⚠️ No valid HTTP(S) URLs found. Send again or /cancel."); current_downloader = context.user_data.get('downloader'); return GET_URLS_BITSO if current_downloader == 'bitso' else GET_URLS
    context.user_data['urls'] = urls; downloader = context.user_data.get('downloader', 'N/A'); logger.info(f"Processed {len(urls)} URLs for {downloader}.")
    if downloader == 'nzbcloud': await update.message.reply_text(f"✅ Got {len(urls)} URL(s).\nSend FN(s):"); return GET_FILENAMES_NZB
    elif downloader == 'deltaleech': kb = [[InlineKeyboardButton("Extract FNs", callback_data='delta_use_url_fn')],[InlineKeyboardButton("Provide FNs", callback_data='delta_manual_fn')],[InlineKeyboardButton("Cancel", callback_data='cancel')]]; await update.message.reply_text(f"✅ Got {len(urls)} URL(s).\nFilenames?", reply_markup=InlineKeyboardMarkup(kb)); return CONFIRM_DELTA_FN
    elif downloader == 'bitso': kb = [[InlineKeyboardButton("Yes, from URLs", callback_data='bitso_use_url_fn')],[InlineKeyboardButton("No, provide", callback_data='bitso_manual_fn')],[InlineKeyboardButton("Cancel", callback_data='cancel')]]; await update.message.reply_text(f"✅ Got {len(urls)} URL(s).\nExtract filenames?", reply_markup=InlineKeyboardMarkup(kb)); return CONFIRM_BITSO_FN
    else: logger.error(f"Unknown downloader '{downloader}' in _process_urls."); await update.message.reply_text("Internal error. /cancel"); return ConversationHandler.END

async def start_download_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
     # ... (function unchanged, includes previous fix) ...
     if 'pyrogram_client' not in context.bot_data: await update.message.reply_text("⚠️ Bot init error."); return ConversationHandler.END
     kb = [[InlineKeyboardButton(s, callback_data=s.lower().split()[1]) for s in ["☁️ nzbCloud", "💧 DeltaLeech", "🪙 Bitso"]], [InlineKeyboardButton("❌ Cancel", callback_data='cancel')]]
     await update.message.reply_text("Choose service:", reply_markup=InlineKeyboardMarkup(kb)); return CHOOSE_DOWNLOADER

async def choose_downloader(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged) ...
    query = update.callback_query; await query.answer(); downloader = query.data; context.user_data['downloader'] = downloader
    logger.info(f"User {update.effective_user.id} chose: {downloader}");
    if downloader == 'cancel': return await cancel(update, context)
    message_text = f"Selected: <b>{downloader}</b>\nSend URL(s) (one per line or upload .txt):"; await query.edit_message_text(message_text, parse_mode=ParseMode.HTML)
    if downloader in ['nzbcloud', 'deltaleech']: return GET_URLS
    elif downloader == 'bitso': return GET_URLS_BITSO
    else: await query.edit_message_text("Error."); return ConversationHandler.END

# Handles text input for URLs (nzb/delta) (Unchanged)
async def get_urls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Handling text in GET_URLS state."); urls = [url for url in update.message.text.splitlines() if url.strip().lower().startswith(('http://', 'https://'))]; return await _process_urls_and_proceed(urls, update, context)

# Handles text input for URLs (bitso) (Unchanged)
async def get_urls_bitso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Handling text in GET_URLS_BITSO state."); urls = [url for url in update.message.text.splitlines() if url.strip().lower().startswith(('http://', 'https://'))]; return await _process_urls_and_proceed(urls, update, context)

# Handles .txt file uploads containing URLs (Unchanged)
async def handle_url_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged) ...
    logger.info("Handling document while expecting URLs."); doc = update.message.document; current_downloader = context.user_data.get('downloader'); current_state = GET_URLS_BITSO if current_downloader == 'bitso' else GET_URLS
    if not doc or not doc.file_name or not doc.file_name.lower().endswith(".txt"): await update.message.reply_text("Please upload `.txt` file."); return current_state
    MAX_TXT_SIZE = 1*1024*1024;
    if doc.file_size > MAX_TXT_SIZE: await update.message.reply_text(f"❌ File >{MAX_TXT_SIZE/1024/1024:.0f}MB."); return current_state
    try:
        txt_file = await context.bot.get_file(doc.file_id); file_content_bytes = await txt_file.download_as_bytearray()
        try: file_content_str = file_content_bytes.decode('utf-8')
        except UnicodeDecodeError: await update.message.reply_text("❌ Error decoding file as UTF-8."); return current_state
        urls = [url for url in file_content_str.splitlines() if url.strip().lower().startswith(('http://', 'https://'))]; logger.info(f"Extracted {len(urls)} URLs from '{doc.file_name}'.")
        return await _process_urls_and_proceed(urls, update, context)
    except Exception as e: logger.error(f"Error processing URL file: {e}", exc_info=True); await update.message.reply_text(f"❌ Error processing file: {e}"); return current_state

# --- DeltaLeech Specific Handlers ---
async def confirm_delta_filenames(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged) ...
    query = update.callback_query; await query.answer(); choice = query.data; urls = context.user_data['urls']
    if choice == 'cancel': return await cancel(update, context)
    if choice == 'delta_use_url_fn':
        logger.info("Extracting FNs for Delta."); fns = [extract_filename_from_url(url) for url in urls]; failed = [u for u,f in zip(urls,fns) if f is None]; valid = [f for f in fns if f]
        if not valid: await query.edit_message_text("⚠️ No FNs extracted. Provide manually:", parse_mode=ParseMode.HTML); context.user_data['use_url_filenames'] = False; return GET_FILENAMES_DELTA
        elif failed: await query.edit_message_text(f"⚠️ Failed {len(failed)} FNs (e.g., <pre>{failed[0]}</pre>). Provide ALL manually:", parse_mode=ParseMode.HTML); context.user_data['use_url_filenames'] = False; return GET_FILENAMES_DELTA
        else:
            # NOTE: We do NOT apply dot style to auto-extracted names here
            context.user_data['filenames'] = valid; logger.info(f"Using {len(valid)} extracted FNs."); await query.edit_message_text("✅ Using extracted FNs.\n⏳ Starting Delta (cf=None)...", parse_mode=ParseMode.HTML)
            cf = None; pyro_client = context.bot_data.get('pyrogram_client')
            if not pyro_client: await context.bot.send_message(update.effective_chat.id, "🚨 Error: Cannot upload.")
            else: asyncio.create_task(run_and_report_process(update, context, download_multiple_files_deltaleech(urls, valid, cf, update, context, pyro_client), "deltaleech"))
            context.user_data.clear(); return ConversationHandler.END
    elif choice == 'delta_manual_fn': context.user_data['use_url_filenames'] = False; await query.edit_message_text(f"Send {len(urls)} FN(s) (one per line):"); return GET_FILENAMES_DELTA
    else: return ConversationHandler.END

# MODIFIED: Apply styling to manual filenames
async def get_filenames_delta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    fns_raw=[fn.strip() for fn in update.message.text.splitlines() if fn.strip()]; urls=context.user_data['urls']
    if not fns_raw: await update.message.reply_text("⚠️ No FNs. Send again or /cancel."); return GET_FILENAMES_DELTA
    if len(urls)!=len(fns_raw): await update.message.reply_text(f"❌ Error: {len(fns_raw)} FNs != {len(urls)} URLs."); return GET_FILENAMES_DELTA

    cleaned_fns = [clean_filename(fn) for fn in fns_raw]
    styled_fns = [apply_dot_style(fn) for fn in cleaned_fns] # Apply style

    context.user_data['filenames']=styled_fns # Store styled names
    logger.info(f"Got {len(styled_fns)} manual styled FNs for delta: {styled_fns[:3]}...")

    await update.message.reply_text("✅ Got styled filenames.\n⏳ Starting Delta (cf=None)...")
    cf=None; pyro_client=context.bot_data.get('pyrogram_client')
    if not pyro_client: await update.message.reply_text("🚨 Error: Cannot upload.")
    else: asyncio.create_task(run_and_report_process(update, context, download_multiple_files_deltaleech(urls, styled_fns, cf, update, context, pyro_client), "deltaleech")) # Use styled_fns
    context.user_data.clear(); return ConversationHandler.END

# --- nzbCloud Specific Handlers ---
# MODIFIED: Apply styling to manual filenames
async def get_filenames_nzb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    fns_raw=[fn.strip() for fn in update.message.text.splitlines() if fn.strip()]; urls=context.user_data['urls']
    if not fns_raw: await update.message.reply_text("⚠️ No FNs. Send again or /cancel."); return GET_FILENAMES_NZB
    if len(urls)!=len(fns_raw): await update.message.reply_text(f"❌ Error: {len(fns_raw)} FNs != {len(urls)} URLs."); return GET_FILENAMES_NZB

    cleaned_fns = [clean_filename(fn) for fn in fns_raw]
    styled_fns = [apply_dot_style(fn) for fn in cleaned_fns] # Apply style

    context.user_data['filenames']=styled_fns # Store styled names
    logger.info(f"Got {len(styled_fns)} styled FNs for nzb: {styled_fns[:3]}...")

    await update.message.reply_text("✅ Got styled filenames.\n⏳ Starting nzbCloud (cf=None)...")
    cf=None; pyro_client=context.bot_data.get('pyrogram_client')
    if not pyro_client: await update.message.reply_text("🚨 Error: Cannot upload.")
    else: asyncio.create_task(run_and_report_process(update, context, download_files_nzbcloud(urls, styled_fns, cf, update, context, pyro_client), "nzbcloud")) # Use styled_fns
    context.user_data.clear(); return ConversationHandler.END


# --- Bitso Specific Handlers ---

# Unchanged - confirms choice, starts download if extracting OK
async def confirm_bitso_filenames(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); choice = query.data; urls = context.user_data['urls']
    if choice == 'cancel': return await cancel(update, context)
    if choice == 'bitso_use_url_fn':
        logger.info("Attempting to extract filenames from URLs for Bitso.")
        filenames = [extract_filename_from_url(url) for url in urls]; failed = [u for u,f in zip(urls,filenames) if f is None]; valid = [f for f in filenames if f is not None]
        if not valid: await query.edit_message_text("⚠️ Could not extract FNs. Provide manually:", parse_mode=ParseMode.HTML); return GET_FILENAMES_BITSO
        elif failed: await query.edit_message_text(f"⚠️ Failed {len(failed)} FNs (e.g.,<pre>{failed[0]}</pre>). Provide ALL manually:", parse_mode=ParseMode.HTML); return GET_FILENAMES_BITSO
        else:
            # NOTE: We do NOT apply dot style to auto-extracted names here
            context.user_data['filenames'] = valid; logger.info(f"Using {len(valid)} extracted FNs for Bitso.")
            await query.edit_message_text("✅ Using extracted FNs.\n⏳ Starting Bitso (cookies=None)...", parse_mode=ParseMode.HTML)
            id_c=None; sess_c=None; ref_url="https://panel.bitso.ir/"; pyro_client = context.bot_data.get('pyrogram_client')
            if not pyro_client: await context.bot.send_message(update.effective_chat.id, "🚨 Error: Cannot upload.")
            else: asyncio.create_task(run_and_report_process(update, context, download_multiple_files_bitso(urls, valid, ref_url, id_c, sess_c, update, context, pyro_client), "bitso"))
            context.user_data.clear(); return ConversationHandler.END
    elif choice == 'bitso_manual_fn': await query.edit_message_text(f"OK. Send the {len(urls)} filename(s), one per
