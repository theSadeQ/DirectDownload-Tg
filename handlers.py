# handlers.py
# MODIFIED: Accesses config (UPLOAD_MODE) via context.bot_data

import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler,
    CommandHandler, MessageHandler, filters
)
from telegram.constants import ParseMode

# NO 'import config' needed here anymore

# Import utils (doesn't need config directly anymore, gets context passed)
from utils import (
    extract_filename_from_url, clean_filename,
    write_failed_downloads_to_file # Needed by run_and_report_process
)
# Import downloaders
from downloaders import (
    download_files_nzbcloud, download_multiple_files_deltaleech,
    download_multiple_files_bitso
)

logger = logging.getLogger(__name__)

# --- Conversation States (Unchanged) ---
CHOOSE_DOWNLOADER, GET_URLS, GET_FILENAMES_NZB, \
GET_FILENAMES_DELTA, CONFIRM_DELTA_FN, \
GET_URLS_BITSO, GET_FILENAMES_BITSO = range(7)

# --- Helper for Running and Reporting ---
# MODIFIED: Accepts context to get DOWNLOAD_DIR for write_failed_downloads_to_file
async def run_and_report_process(update: Update, context: ContextTypes.DEFAULT_TYPE, download_upload_task, service_name: str):
    """ Awaits task and reports status/failures via PTB context. """
    chat_id = update.effective_chat.id; failed_sources = []
    download_dir = context.bot_data.get('download_dir', '/content/downloads') # Get download dir
    try:
        failed_sources = await download_upload_task
        final_msg = f"🏁 <b>{service_name}</b> process finished."
        logger.info(f"{service_name} process OK for chat {chat_id}.")
        if failed_sources:
            final_msg += f"\n⚠️ Failed {len(failed_sources)} item(s)."
            logger.warning(f"{len(failed_sources)} failure(s) for {service_name} in {chat_id}.")
            failed_file_path = write_failed_downloads_to_file(failed_sources, service_name, download_dir) # Use fetched dir
            if failed_file_path:
                 escaped_path = failed_file_path.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                 final_msg += f"\nList: <pre>{escaped_path}</pre>"
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
    """ Sends welcome message, gets config from context. """
    user = update.effective_user
    # Get config from context
    upload_enabled = context.bot_data.get('upload_enabled', True)
    upload_mode = context.bot_data.get('upload_mode', 'N/A')
    delete_after_upload = context.bot_data.get('delete_after_upload', True)
    # Basic check only
    cred_status = "API/Token Configured." if context.bot_data.get('pyrogram_client') else "⚠️ API/Token Error!"

    upload_status = f"✅ Uploads ON (Mode: {upload_mode})." if upload_enabled else "ℹ️ Uploads OFF."
    delete_status = f"🗑️ Delete ON." if upload_enabled and delete_after_upload else "💾 Delete OFF."

    await update.message.reply_html(
        rf"Hi {user.mention_html()}!"
        f"\n\n{cred_status}"
        f"\n{upload_status}"
        f"\n{delete_status}"
        f"\n\nUse /download to start.",
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (cancel function unchanged) ...
    user_id = update.effective_user.id if update.effective_user else "Unknown"; logger.info(f"User {user_id} cancelled.")
    message_text = "Download process cancelled."; query = update.callback_query
    try:
        if query: await query.answer(); await query.edit_message_text(message_text)
        elif update.message: await update.message.reply_text(message_text)
    except Exception as e:
        logger.warning(f"Failed send/edit cancel confirm: {e}")
        try: await context.bot.send_message(update.effective_chat.id, message_text)
        except Exception as send_e: logger.error(f"Failed sending cancel confirm: {send_e}")
    context.user_data.clear(); return ConversationHandler.END

# --- Conversation Handlers ---
async def start_download_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
     if 'pyrogram_client' not in context.bot_data: await update.message.reply_text("⚠️ Bot init error."); return ConversationHandler.END
     kb = [[InlineKeyboardButton(s, callback_data=s.lower().split()[1])) for s in ["☁️ nzbCloud", "💧 DeltaLeech", "🪙 Bitso"]] + [[InlineKeyboardButton("❌ Cancel", callback_data='cancel')]]
     await update.message.reply_text("Choose service:", reply_markup=InlineKeyboardMarkup(kb)); return CHOOSE_DOWNLOADER

async def choose_downloader(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged) ...
    query = update.callback_query; await query.answer(); downloader = query.data; context.user_data['downloader'] = downloader
    logger.info(f"User {update.effective_user.id} chose: {downloader}");
    if downloader == 'cancel': return await cancel(update, context)
    message_text = f"Selected: <b>{downloader}</b>\nSend URL(s):"; await query.edit_message_text(message_text, parse_mode=ParseMode.HTML)
    if downloader in ['nzbcloud', 'deltaleech']: return GET_URLS
    elif downloader == 'bitso': return GET_URLS_BITSO
    else: await query.edit_message_text("Error."); return ConversationHandler.END

async def get_urls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged) ...
    urls = [url for url in update.message.text.splitlines() if url.strip().lower().startswith(('http://', 'https://'))]
    if not urls: await update.message.reply_text("⚠️ No valid URLs. Send again or /cancel."); return GET_URLS
    context.user_data['urls'] = urls; downloader = context.user_data['downloader']; logger.info(f"Got {len(urls)} URLs for {downloader}.")
    if downloader == 'nzbcloud': await update.message.reply_text(f"{len(urls)} URL(s). Send FN(s):"); return GET_FILENAMES_NZB
    elif downloader == 'deltaleech': kb = [[InlineKeyboardButton("Extract FNs", cd='delta_use_url_fn')],[InlineKeyboardButton("Provide FNs", cd='delta_manual_fn')],[InlineKeyboardButton("Cancel", cd='cancel')]]; await update.message.reply_text(f"{len(urls)} URL(s). Filenames?", reply_markup=InlineKeyboardMarkup(kb)); return CONFIRM_DELTA_FN
    else: return ConversationHandler.END

async def confirm_delta_filenames(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged - calls run_and_report_process which now gets context) ...
    query = update.callback_query; await query.answer(); choice = query.data; urls = context.user_data['urls']
    if choice == 'cancel': return await cancel(update, context)
    if choice == 'delta_use_url_fn':
        logger.info("Extracting FNs for Delta."); fns = [extract_filename_from_url(url) for url in urls]; failed = [u for u,f in zip(urls,fns) if f is None]; valid = [f for f in fns if f]
        if not valid: await query.edit_message_text("⚠️ No FNs extracted. Provide manually:"); context.user_data['use_url_filenames'] = False; return GET_FILENAMES_DELTA
        elif failed: await query.edit_message_text(f"⚠️ Failed {len(failed)} FNs (e.g., <pre>{failed[0]}</pre>). Provide ALL manually:", parse_mode=ParseMode.HTML); context.user_data['use_url_filenames'] = False; return GET_FILENAMES_DELTA
        else:
            context.user_data['filenames'] = valid; logger.info(f"Using {len(valid)} extracted FNs."); await query.edit_message_text("✅ Using extracted FNs.\n⏳ Starting Delta (cf=None)...", parse_mode=ParseMode.HTML)
            cf = None; pyro_client = context.bot_data.get('pyrogram_client')
            if not pyro_client: await context.bot.send_message(update.effective_chat.id, "🚨 Error: Cannot upload.")
            else: asyncio.create_task(run_and_report_process(update, context, download_multiple_files_deltaleech(urls, valid, cf, update, context, pyro_client), "deltaleech")) # Pass context
            context.user_data.clear(); return ConversationHandler.END
    elif choice == 'delta_manual_fn': context.user_data['use_url_filenames'] = False; await query.edit_message_text(f"Send {len(urls)} FN(s):"); return GET_FILENAMES_DELTA
    else: return ConversationHandler.END

async def get_filenames_nzb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged - calls run_and_report_process which now gets context) ...
    fns_raw=[fn.strip() for fn in update.message.text.splitlines() if fn.strip()]; urls=context.user_data['urls']
    if not fns_raw: await update.message.reply_text("⚠️ No FNs. Send again or /cancel."); return GET_FILENAMES_NZB
    if len(urls)!=len(fns_raw): await update.message.reply_text(f"❌ Error: {len(fns_raw)} FNs != {len(urls)} URLs."); return GET_FILENAMES_NZB
    fns=[clean_filename(fn) for fn in fns_raw]; context.user_data['filenames']=fns; logger.info(f"Got {len(fns)} FNs for nzb.")
    await update.message.reply_text("✅ Got FNs.\n⏳ Starting nzbCloud (cf=None)...")
    cf=None; pyro_client=context.bot_data.get('pyrogram_client')
    if not pyro_client: await update.message.reply_text("🚨 Error: Cannot upload.")
    else: asyncio.create_task(run_and_report_process(update, context, download_files_nzbcloud(urls, fns, cf, update, context, pyro_client), "nzbcloud")) # Pass context
    context.user_data.clear(); return ConversationHandler.END

async def get_filenames_delta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged - calls run_and_report_process which now gets context) ...
    fns_raw=[fn.strip() for fn in update.message.text.splitlines() if fn.strip()]; urls=context.user_data['urls']
    if not fns_raw: await update.message.reply_text("⚠️ No FNs. Send again or /cancel."); return GET_FILENAMES_DELTA
    if len(urls)!=len(fns_raw): await update.message.reply_text(f"❌ Error: {len(fns_raw)} FNs != {len(urls)} URLs."); return GET_FILENAMES_DELTA
    fns=[clean_filename(fn) for fn in fns_raw]; context.user_data['filenames']=fns; logger.info(f"Got {len(fns)} manual FNs for delta.")
    await update.message.reply_text("✅ Got FNs.\n⏳ Starting Delta (cf=None)...")
    cf=None; pyro_client=context.bot_data.get('pyrogram_client')
    if not pyro_client: await update.message.reply_text("🚨 Error: Cannot upload.")
    else: asyncio.create_task(run_and_report_process(update, context, download_multiple_files_deltaleech(urls, fns, cf, update, context, pyro_client), "deltaleech")) # Pass context
    context.user_data.clear(); return ConversationHandler.END

async def get_urls_bitso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged) ...
    urls = [url for url in update.message.text.splitlines() if url.strip().lower().startswith(('http://', 'https://'))]
    if not urls: await update.message.reply_text("⚠️ No valid URLs. Send again or /cancel."); return GET_URLS_BITSO
    context.user_data['urls'] = urls; logger.info(f"Got {len(urls)} URLs for bitso.")
    await update.message.reply_text(f"{len(urls)} URL(s). Send FN(s):"); return GET_FILENAMES_BITSO

async def get_filenames_bitso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function unchanged - calls run_and_report_process which now gets context) ...
    fns_raw=[fn.strip() for fn in update.message.text.splitlines() if fn.strip()]; urls=context.user_data['urls']
    if not fns_raw: await update.message.reply_text("⚠️ No FNs. Send again or /cancel."); return GET_FILENAMES_BITSO
    if len(urls)!=len(fns_raw): await update.message.reply_text(f"❌ Error: {len(fns_raw)} FNs != {len(urls)} URLs."); return GET_FILENAMES_BITSO
    fns=[clean_filename(fn) for fn in fns_raw]; context.user_data['filenames']=fns; logger.info(f"Got {len(fns)} FNs for bitso.")
    await update.message.reply_text("✅ Got FNs.\n⏳ Starting Bitso (cookies=None)...")
    id_c=None; sess_c=None; ref_url="https://panel.bitso.ir/"; pyro_client=context.bot_data.get('pyrogram_client')
    if not pyro_client: await update.message.reply_text("🚨 Error: Cannot upload.")
    else: asyncio.create_task(run_and_report_process(update, context, download_multiple_files_bitso(urls, fns, ref_url, id_c, sess_c, update, context, pyro_client), "bitso")) # Pass context
    context.user_data.clear(); return ConversationHandler.END

# --- Build Conversation Handler (Unchanged from previous) ---
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("download", start_download_conv)],
    states={
        CHOOSE_DOWNLOADER: [CallbackQueryHandler(choose_downloader, pattern='^(nzbcloud|deltaleech|bitso|cancel)$')],
        GET_URLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_urls)],
        CONFIRM_DELTA_FN: [CallbackQueryHandler(confirm_delta_filenames, pattern='^(delta_use_url_fn|delta_manual_fn|cancel)$')],
        GET_FILENAMES_NZB: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_filenames_nzb)],
        GET_FILENAMES_DELTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_filenames_delta)],
        GET_URLS_BITSO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_urls_bitso)],
        GET_FILENAMES_BITSO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_filenames_bitso)],
    },
    fallbacks=[
        CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern='^cancel$'),
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("Unexpected input. /cancel?")),
        MessageHandler(filters.COMMAND, lambda u,c: u.message.reply_text("Finish or /cancel first.")),
    ],
)
