# handlers.py
# Contains PTB handlers, conversation logic, and the reporting helper.
# MODIFIED: Removed cf_clearance and cookie prompts, defaults to None.

import logging
import asyncio

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
from telegram.constants import ParseMode # Needed for run_and_report_process

# Local imports
import config # Import non-sensitive config
from utils import (
    extract_filename_from_url,
    clean_filename,
    write_failed_downloads_to_file # Needed by run_and_report_process
)
# Import specific downloaders needed by the conversation states
from downloaders import (
    download_files_nzbcloud,
    download_multiple_files_deltaleech,
    download_multiple_files_bitso
)

logger = logging.getLogger(__name__)

# --- Conversation States ---
# REMOVED: GET_CF_CLEARANCE_NZB, GET_CF_CLEARANCE_DELTA, GET_BITSO_COOKIES
CHOOSE_DOWNLOADER, GET_URLS, GET_FILENAMES_NZB, \
GET_FILENAMES_DELTA, CONFIRM_DELTA_FN, \
GET_URLS_BITSO, GET_FILENAMES_BITSO = range(7) # Adjusted range


# --- Helper for Running and Reporting (Unchanged from previous version) ---
async def run_and_report_process(update: Update, context: ContextTypes.DEFAULT_TYPE, download_upload_task, service_name: str):
    """
    Awaits the download/upload task (which returns failed sources)
    and reports final status/failed list via PTB context.
    Uses config.DOWNLOAD_DIR implicitly via write_failed_downloads_to_file.
    """
    chat_id = update.effective_chat.id
    failed_sources = [] # List to store source URLs that failed

    try:
        failed_sources = await download_upload_task
        final_msg = f"🏁 <b>{service_name}</b> process finished."
        logger.info(f"{service_name} process finished for chat {chat_id}.")

        if failed_sources:
            final_msg += f"\n⚠️ Encountered {len(failed_sources)} failure(s)."
            logger.warning(f"{len(failed_sources)} failure(s) for {service_name} in chat {chat_id}.")
            failed_file_path = write_failed_downloads_to_file(failed_sources, service_name, config.DOWNLOAD_DIR)
            if failed_file_path:
                 escaped_path = failed_file_path.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;') # Basic HTML escape
                 final_msg += f"\nList saved to <pre>{escaped_path}</pre>"
        else:
            final_msg += f"\n✅🎉 All items seem to have completed successfully!"
            logger.info(f"All {service_name} items completed successfully for chat {chat_id}.")

        await context.bot.send_message(chat_id, final_msg, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Error during the {service_name} run/report phase: {e}", exc_info=True)
        try:
            error_text = str(e).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;') # Basic HTML escape
            await context.bot.send_message(chat_id, f"🚨 Unexpected error after <b>{service_name}</b> process finished.\nError: <pre>{error_text[:500]}</pre>", parse_mode=ParseMode.HTML)
        except Exception as send_error:
             logger.error(f"Failed to send final error message to user {chat_id}: {send_error}")


# --- Simple Command Handlers (Unchanged) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message with current settings."""
    user = update.effective_user
    upload_status = f"✅ Uploads ENABLED (Mode: {config.UPLOAD_MODE})." if config.UPLOAD_ENABLED else "ℹ️ Uploads DISABLED."
    delete_status = f"🗑️ Files DELETED after upload." if config.UPLOAD_ENABLED and config.DELETE_AFTER_UPLOAD else "💾 Files KEPT after upload."
    cred_status = "API ID/Hash configured." # Assumes bot.py validated before start

    await update.message.reply_html(
        rf"Hi {user.mention_html()}! I can download files."
        f"\n\n{cred_status}"
        f"\n{upload_status}"
        f"\n{delete_status}"
        f"\n\nUse /download to start.",
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    user_id = update.effective_user.id if update.effective_user else "Unknown"
    logger.info(f"User {user_id} cancelled.")
    message_text = "Download process cancelled."
    query = update.callback_query
    try:
        if query: await query.answer(); await query.edit_message_text(message_text)
        elif update.message: await update.message.reply_text(message_text)
    except Exception as e:
        logger.warning(f"Failed to send/edit cancel confirmation: {e}")
        try: await context.bot.send_message(update.effective_chat.id, message_text)
        except Exception as send_e: logger.error(f"Failed sending cancel confirm: {send_e}")
    context.user_data.clear(); return ConversationHandler.END

# --- Conversation Handlers (Modified) ---
async def start_download_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
     """Starts the conversation when /download is used."""
     if 'pyrogram_client' not in context.bot_data:
          await update.message.reply_text("⚠️ Bot initialization error. Please check logs or restart.")
          return ConversationHandler.END
     keyboard = [
        [InlineKeyboardButton("☁️ nzbCloud", callback_data='nzbcloud')],
        [InlineKeyboardButton("💧 DeltaLeech", callback_data='deltaleech')],
        [InlineKeyboardButton("🪙 Bitso", callback_data='bitso')],
        [InlineKeyboardButton("❌ Cancel", callback_data='cancel')],
     ]
     await update.message.reply_text("Please choose the downloader service:", reply_markup=InlineKeyboardMarkup(keyboard))
     return CHOOSE_DOWNLOADER

async def choose_downloader(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # (Unchanged)
    query = update.callback_query; await query.answer()
    downloader = query.data; context.user_data['downloader'] = downloader
    logger.info(f"User {update.effective_user.id} chose: {downloader}")
    if downloader == 'cancel': return await cancel(update, context)
    message_text = f"Selected: <b>{downloader}</b>\nSend URL(s) (one per line):"
    await query.edit_message_text(message_text, parse_mode=ParseMode.HTML)
    if downloader in ['nzbcloud', 'deltaleech']: return GET_URLS
    elif downloader == 'bitso': return GET_URLS_BITSO
    else: logger.error(f"Unexpected choice: {downloader}"); await query.edit_message_text("Error."); return ConversationHandler.END

async def get_urls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # (Unchanged)
    urls = [url for url in update.message.text.splitlines() if url.strip().lower().startswith(('http://', 'https://'))]
    if not urls: await update.message.reply_text("⚠️ No valid HTTP(S) URLs. Send again or /cancel."); return GET_URLS
    context.user_data['urls'] = urls; downloader = context.user_data['downloader']
    logger.info(f"Got {len(urls)} URLs for {downloader}.")
    if downloader == 'nzbcloud':
        await update.message.reply_text(f"{len(urls)} URL(s). Send filename(s) (one per line, matching order):"); return GET_FILENAMES_NZB
    elif downloader == 'deltaleech':
        kb = [[InlineKeyboardButton("Yes, from URLs", callback_data='delta_use_url_fn')], [InlineKeyboardButton("No, provide", callback_data='delta_manual_fn')], [InlineKeyboardButton("Cancel", callback_data='cancel')]]
        await update.message.reply_text(f"{len(urls)} URL(s). Extract filenames?", reply_markup=InlineKeyboardMarkup(kb)); return CONFIRM_DELTA_FN
    else: return ConversationHandler.END

async def confirm_delta_filenames(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles DeltaLeech filename choice. If extracting, proceeds directly to download."""
    query = update.callback_query; await query.answer(); choice = query.data; urls = context.user_data['urls']
    if choice == 'cancel': return await cancel(update, context)

    if choice == 'delta_use_url_fn':
        logger.info("Extracting FNs for Delta.")
        filenames = [extract_filename_from_url(url) for url in urls]; failed = [u for u, f in zip(urls, filenames) if f is None]; valid = [f for f in filenames if f]
        if not valid: await query.edit_message_text("⚠️ No FNs extracted. Provide manually:", parse_mode=ParseMode.HTML); context.user_data['use_url_filenames'] = False; return GET_FILENAMES_DELTA
        elif failed: await query.edit_message_text(f"⚠️ Failed {len(failed)} FNs (e.g., <pre>{failed[0]}</pre>). Provide ALL manually:", parse_mode=ParseMode.HTML); context.user_data['use_url_filenames'] = False; return GET_FILENAMES_DELTA
        else:
            # --- Filenames extracted successfully, now start download ---
            context.user_data['filenames'] = valid; logger.info(f"Using {len(valid)} extracted FNs.")
            await query.edit_message_text("✅ Using extracted FNs.\n⏳ Starting DeltaLeech process (cf_clearance=None)...", parse_mode=ParseMode.HTML) # Updated message

            # Set cf_clearance to None
            cf_clearance = None
            # Get needed data
            fns=context.user_data['filenames']; pyro_client=context.bot_data.get('pyrogram_client')
            # Trigger download/upload
            if not pyro_client: logger.error("No pyro client!"); await context.bot.send_message(update.effective_chat.id, "🚨 Error: Cannot upload.") # Use send_message as query might be gone
            else: asyncio.create_task(run_and_report_process(update, context, download_multiple_files_deltaleech(urls, fns, cf_clearance, update, context, pyro_client), "deltaleech"))
            context.user_data.clear(); return ConversationHandler.END
            # --- End direct download start ---

    elif choice == 'delta_manual_fn':
        # User will provide filenames manually, proceed to GET_FILENAMES_DELTA state
        context.user_data['use_url_filenames'] = False; await query.edit_message_text(f"Send {len(urls)} FN(s) (one per line):"); return GET_FILENAMES_DELTA
    else: return ConversationHandler.END

async def get_filenames_nzb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets nzbCloud filenames and proceeds directly to download."""
    fns_raw=[fn.strip() for fn in update.message.text.splitlines() if fn.strip()]; urls=context.user_data['urls']
    if not fns_raw: await update.message.reply_text("⚠️ No FNs. Send again or /cancel."); return GET_FILENAMES_NZB
    if len(urls) != len(fns_raw): await update.message.reply_text(f"❌ Error: {len(fns_raw)} FNs, expected {len(urls)}. Send again or /cancel."); return GET_FILENAMES_NZB

    fns=[clean_filename(fn) for fn in fns_raw]; context.user_data['filenames']=fns; logger.info(f"Got {len(fns)} FNs for nzb.")
    await update.message.reply_text("✅ Got filenames.\n⏳ Starting nzbCloud process (cf_clearance=None)...") # Updated message

    # Set cf_clearance to None
    cf_clearance = None
    # Get needed data
    pyro_client=context.bot_data.get('pyrogram_client')
    # Trigger download/upload
    if not pyro_client: logger.error("No pyro client!"); await update.message.reply_text("🚨 Error: Cannot upload.")
    else: asyncio.create_task(run_and_report_process(update, context, download_files_nzbcloud(urls, fns, cf_clearance, update, context, pyro_client), "nzbcloud"))
    context.user_data.clear(); return ConversationHandler.END

async def get_filenames_delta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets manual DeltaLeech filenames and proceeds directly to download."""
    fns_raw=[fn.strip() for fn in update.message.text.splitlines() if fn.strip()]; urls=context.user_data['urls']
    if not fns_raw: await update.message.reply_text("⚠️ No FNs. Send again or /cancel."); return GET_FILENAMES_DELTA
    if len(urls) != len(fns_raw): await update.message.reply_text(f"❌ Error: {len(fns_raw)} FNs, expected {len(urls)}. Send again or /cancel."); return GET_FILENAMES_DELTA

    fns=[clean_filename(fn) for fn in fns_raw]; context.user_data['filenames']=fns; logger.info(f"Got {len(fns)} manual FNs for delta.")
    await update.message.reply_text("✅ Got filenames.\n⏳ Starting DeltaLeech process (cf_clearance=None)...") # Updated message

    # Set cf_clearance to None
    cf_clearance = None
    # Get needed data
    pyro_client=context.bot_data.get('pyrogram_client')
    # Trigger download/upload
    if not pyro_client: logger.error("No pyro client!"); await update.message.reply_text("🚨 Error: Cannot upload.")
    else: asyncio.create_task(run_and_report_process(update, context, download_multiple_files_deltaleech(urls, fns, cf_clearance, update, context, pyro_client), "deltaleech"))
    context.user_data.clear(); return ConversationHandler.END

async def get_urls_bitso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # (Unchanged)
    urls = [url for url in update.message.text.splitlines() if url.strip().lower().startswith(('http://', 'https://'))]
    if not urls: await update.message.reply_text("⚠️ No valid URLs. Send again or /cancel."); return GET_URLS_BITSO
    context.user_data['urls'] = urls; logger.info(f"Got {len(urls)} URLs for bitso.")
    await update.message.reply_text(f"{len(urls)} URL(s). Send FN(s) (one per line, matching order):"); return GET_FILENAMES_BITSO

async def get_filenames_bitso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets Bitso filenames and proceeds directly to download."""
    fns_raw=[fn.strip() for fn in update.message.text.splitlines() if fn.strip()]; urls=context.user_data['urls']
    if not fns_raw: await update.message.reply_text("⚠️ No FNs. Send again or /cancel."); return GET_FILENAMES_BITSO
    if len(urls) != len(fns_raw): await update.message.reply_text(f"❌ Error: {len(fns_raw)} FNs, expected {len(urls)}. Send again or /cancel."); return GET_FILENAMES_BITSO

    fns=[clean_filename(fn) for fn in fns_raw]; context.user_data['filenames']=fns; logger.info(f"Got {len(fns)} FNs for bitso.")
    await update.message.reply_text("✅ Got filenames.\n⏳ Starting Bitso process (cookies=None)...") # Updated message

    # Set cookies to None
    id_cookie=None; sess_cookie=None
    # Get needed data
    ref_url = "https://panel.bitso.ir/"
    pyro_client=context.bot_data.get('pyrogram_client')
    # Trigger download/upload
    if not pyro_client: logger.error("No pyro client!"); await update.message.reply_text("🚨 Error: Cannot upload.")
    else: asyncio.create_task(run_and_report_process(update, context, download_multiple_files_bitso(urls, fns, ref_url, id_cookie, sess_cookie, update, context, pyro_client), "bitso"))
    context.user_data.clear(); return ConversationHandler.END


# --- Build Conversation Handler (Updated) ---
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("download", start_download_conv)],
    states={
        CHOOSE_DOWNLOADER: [CallbackQueryHandler(choose_downloader, pattern='^(nzbcloud|deltaleech|bitso|cancel)$')],
        GET_URLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_urls)],
        CONFIRM_DELTA_FN: [CallbackQueryHandler(confirm_delta_filenames, pattern='^(delta_use_url_fn|delta_manual_fn|cancel)$')],
        GET_FILENAMES_NZB: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_filenames_nzb)],
        GET_FILENAMES_DELTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_filenames_delta)],
        # Removed CF clearance states
        GET_URLS_BITSO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_urls_bitso)],
        GET_FILENAMES_BITSO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_filenames_bitso)],
        # Removed Bitso cookies state
    },
    fallbacks=[
        CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern='^cancel$'),
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda update, context: update.message.reply_text("Unexpected input. /cancel?")),
        MessageHandler(filters.COMMAND, lambda update, context: update.message.reply_text("Finish current process or /cancel first.")),
    ],
)
