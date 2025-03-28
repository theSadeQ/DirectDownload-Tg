# handlers.py
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
from telegram.constants import ParseMode

# Local imports
import config # Import non-sensitive config
from utils import extract_filename_from_url, clean_filename, run_and_report_process
from downloaders import (
    download_files_nzbcloud,
    download_multiple_files_deltaleech,
    download_multiple_files_bitso
)

logger = logging.getLogger(__name__)

# --- Conversation States ---
CHOOSE_DOWNLOADER, GET_URLS, GET_FILENAMES_NZB, GET_CF_CLEARANCE_NZB, \
GET_FILENAMES_DELTA, GET_CF_CLEARANCE_DELTA, CONFIRM_DELTA_FN, \
GET_URLS_BITSO, GET_FILENAMES_BITSO, GET_BITSO_COOKIES = range(10)


# --- Simple Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message with current settings."""
    user = update.effective_user
    # Read non-sensitive config directly
    upload_status = f"✅ Uploads ENABLED (Mode: {config.UPLOAD_MODE})." if config.UPLOAD_ENABLED else "ℹ️ Uploads DISABLED."
    delete_status = f"🗑️ Files DELETED after upload." if config.UPLOAD_ENABLED and config.DELETE_AFTER_UPLOAD else "💾 Files KEPT after upload."
    # Removed IS_CONFIG_VALID check here - bot.py handles startup validation

    await update.message.reply_html(
        rf"Hi {user.mention_html()}! I can download files."
        # f"\n\n{cred_status}" # Removed cred status check from here
        f"\n\n{upload_status}"
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

# --- Conversation Handlers ---
async def start_download_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
     """Starts the conversation when /download is used."""
     # Removed IS_CONFIG_VALID check here - bot.py handles startup validation
     keyboard = [
        [InlineKeyboardButton("☁️ nzbCloud", callback_data='nzbcloud')],
        [InlineKeyboardButton("💧 DeltaLeech", callback_data='deltaleech')],
        [InlineKeyboardButton("🪙 Bitso", callback_data='bitso')],
        [InlineKeyboardButton("❌ Cancel", callback_data='cancel')],
     ]
     await update.message.reply_text("Please choose the downloader service:", reply_markup=InlineKeyboardMarkup(keyboard))
     return CHOOSE_DOWNLOADER

# [ Rest of the conversation handlers (choose_downloader, get_urls, confirm_delta_filenames, etc.) remain unchanged from the previous full code response ]
# ... (Keep the rest of the functions in handlers.py the same) ...

async def choose_downloader(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    downloader = query.data; context.user_data['downloader'] = downloader
    logger.info(f"User {update.effective_user.id} chose: {downloader}")
    if downloader == 'cancel': return await cancel(update, context)
    message_text = f"Selected: <b>{downloader}</b>\nSend URL(s) (one per line):"
    await query.edit_message_text(message_text, parse_mode=ParseMode.HTML)
    if downloader in ['nzbcloud', 'deltaleech']: return GET_URLS
    elif downloader == 'bitso': return GET_URLS_BITSO
    else: logger.error(f"Unexpected choice: {downloader}"); await query.edit_message_text("Error."); return ConversationHandler.END # Should not happen

async def get_urls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
    query = update.callback_query; await query.answer(); choice = query.data; urls = context.user_data['urls']
    if choice == 'cancel': return await cancel(update, context)
    if choice == 'delta_use_url_fn':
        logger.info("Extracting FNs for Delta.")
        filenames = [extract_filename_from_url(url) for url in urls]; failed = [u for u, f in zip(urls, filenames) if f is None]; valid = [f for f in filenames if f]
        if not valid: await query.edit_message_text("⚠️ No FNs extracted. Provide manually:", parse_mode=ParseMode.HTML); context.user_data['use_url_filenames'] = False; return GET_FILENAMES_DELTA
        elif failed: await query.edit_message_text(f"⚠️ Failed {len(failed)} FNs (e.g., <pre>{failed[0]}</pre>). Provide ALL manually:", parse_mode=ParseMode.HTML); context.user_data['use_url_filenames'] = False; return GET_FILENAMES_DELTA
        else: context.user_data['filenames'] = valid; logger.info(f"Using {len(valid)} extracted FNs."); await query.edit_message_text("✅ Using extracted FNs.\n\nSend <code>cf_clearance</code> cookie (or <code>none</code>):", parse_mode=ParseMode.HTML); return GET_CF_CLEARANCE_DELTA
    elif choice == 'delta_manual_fn': context.user_data['use_url_filenames'] = False; await query.edit_message_text(f"Send {len(urls)} FN(s) (one per line):"); return GET_FILENAMES_DELTA
    else: return ConversationHandler.END

async def get_filenames_nzb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    fns_raw=[fn.strip() for fn in update.message.text.splitlines() if fn.strip()]; urls=context.user_data['urls']
    if not fns_raw: await update.message.reply_text("⚠️ No FNs. Send again or /cancel."); return GET_FILENAMES_NZB
    if len(urls) != len(fns_raw): await update.message.reply_text(f"❌ Error: {len(fns_raw)} FNs, expected {len(urls)}. Send again or /cancel."); return GET_FILENAMES_NZB
    fns=[clean_filename(fn) for fn in fns_raw]; context.user_data['filenames']=fns; logger.info(f"Got {len(fns)} FNs for nzb.")
    await update.message.reply_text("✅ Got FNs.\n\nSend <code>cf_clearance</code> cookie (or <code>none</code>):", parse_mode=ParseMode.HTML); return GET_CF_CLEARANCE_NZB

async def get_filenames_delta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    fns_raw=[fn.strip() for fn in update.message.text.splitlines() if fn.strip()]; urls=context.user_data['urls']
    if not fns_raw: await update.message.reply_text("⚠️ No FNs. Send again or /cancel."); return GET_FILENAMES_DELTA
    if len(urls) != len(fns_raw): await update.message.reply_text(f"❌ Error: {len(fns_raw)} FNs, expected {len(urls)}. Send again or /cancel."); return GET_FILENAMES_DELTA
    fns=[clean_filename(fn) for fn in fns_raw]; context.user_data['filenames']=fns; logger.info(f"Got {len(fns)} manual FNs for delta.")
    await update.message.reply_text("✅ Got FNs.\n\nSend <code>cf_clearance</code> cookie (or <code>none</code>):", parse_mode=ParseMode.HTML); return GET_CF_CLEARANCE_DELTA

async def get_cf_clearance_nzb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cf=update.message.text.strip(); cf=None if cf.lower()=='none' else cf; context.user_data['cf_clearance']=cf; logger.info(f"Got cf_clearance for nzb.")
    await update.message.reply_text("⏳ Starting nzbCloud process...")
    urls=context.user_data['urls']; fns=context.user_data['filenames']; pyro_client=context.bot_data.get('pyrogram_client')
    if not pyro_client: logger.error("No pyro client!"); await update.message.reply_text("🚨 Error: Cannot upload.")
    else: asyncio.create_task(run_and_report_process(update, context, download_files_nzbcloud(urls, fns, cf, update, context, pyro_client), "nzbcloud", config.DOWNLOAD_DIR))
    context.user_data.clear(); return ConversationHandler.END

async def get_cf_clearance_delta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cf=update.message.text.strip(); cf=None if cf.lower()=='none' else cf; context.user_data['cf_clearance']=cf; logger.info(f"Got cf_clearance for delta.")
    await update.message.reply_text("⏳ Starting DeltaLeech process...")
    urls=context.user_data['urls']; fns=context.user_data['filenames']; pyro_client=context.bot_data.get('pyrogram_client')
    if not pyro_client: logger.error("No pyro client!"); await update.message.reply_text("🚨 Error: Cannot upload.")
    else: asyncio.create_task(run_and_report_process(update, context, download_multiple_files_deltaleech(urls, fns, cf, update, context, pyro_client), "deltaleech", config.DOWNLOAD_DIR))
    context.user_data.clear(); return ConversationHandler.END

async def get_urls_bitso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    urls = [url for url in update.message.text.splitlines() if url.strip().lower().startswith(('http://', 'https://'))]
    if not urls: await update.message.reply_text("⚠️ No valid URLs. Send again or /cancel."); return GET_URLS_BITSO
    context.user_data['urls'] = urls; logger.info(f"Got {len(urls)} URLs for bitso.")
    await update.message.reply_text(f"{len(urls)} URL(s). Send FN(s) (one per line, matching order):"); return GET_FILENAMES_BITSO

async def get_filenames_bitso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    fns_raw=[fn.strip() for fn in update.message.text.splitlines() if fn.strip()]; urls=context.user_data['urls']
    if not fns_raw: await update.message.reply_text("⚠️ No FNs. Send again or /cancel."); return GET_FILENAMES_BITSO
    if len(urls) != len(fns_raw): await update.message.reply_text(f"❌ Error: {len(fns_raw)} FNs, expected {len(urls)}. Send again or /cancel."); return GET_FILENAMES_BITSO
    fns=[clean_filename(fn) for fn in fns_raw]; context.user_data['filenames']=fns; logger.info(f"Got {len(fns)} FNs for bitso.")
    await update.message.reply_text("✅ Got FNs.\n\nSend cookies (<code>_identity=...</code>\n<code>PHPSESSID=...</code>, use <code>none</code> if needed):", parse_mode=ParseMode.HTML); return GET_BITSO_COOKIES

async def get_bitso_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lines=update.message.text.strip().splitlines(); id_cookie=None; sess_cookie=None
    for line in lines:
        if '=' in line: k,v = line.split("=", 1); kl=k.strip().lower(); vs=v.strip()
        if vs.lower() != 'none':
            if kl=="_identity": id_cookie=vs
            elif kl=="phpsessid": sess_cookie=vs
    context.user_data['_identity']=id_cookie; context.user_data['phpsessid']=sess_cookie
    logger.info(f"Got bitso cookies: id:{'Y' if id_cookie else 'N'}, sess:{'Y' if sess_cookie else 'N'}")
    await update.message.reply_text("⏳ Starting Bitso process...")
    urls=context.user_data['urls']; fns=context.user_data['filenames']; pyro_client=context.bot_data.get('pyrogram_client')
    ref_url = "https://panel.bitso.ir/"
    if not pyro_client: logger.error("No pyro client!"); await update.message.reply_text("🚨 Error: Cannot upload.")
    else: asyncio.create_task(run_and_report_process(update, context, download_multiple_files_bitso(urls, fns, ref_url, id_cookie, sess_cookie, update, context, pyro_client), "bitso", config.DOWNLOAD_DIR))
    context.user_data.clear(); return ConversationHandler.END


# --- Build Conversation Handler ---
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("download", start_download_conv)],
    states={
        CHOOSE_DOWNLOADER: [CallbackQueryHandler(choose_downloader, pattern='^(nzbcloud|deltaleech|bitso|cancel)$')],
        GET_URLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_urls)],
        CONFIRM_DELTA_FN: [CallbackQueryHandler(confirm_delta_filenames, pattern='^(delta_use_url_fn|delta_manual_fn|cancel)$')],
        GET_FILENAMES_NZB: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_filenames_nzb)],
        GET_FILENAMES_DELTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_filenames_delta)],
        GET_CF_CLEARANCE_NZB: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_cf_clearance_nzb)],
        GET_CF_CLEARANCE_DELTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_cf_clearance_delta)],
        GET_URLS_BITSO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_urls_bitso)],
        GET_FILENAMES_BITSO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_filenames_bitso)],
        GET_BITSO_COOKIES: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_bitso_cookies)],
    },
    fallbacks=[
        CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern='^cancel$'),
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda update, context: update.message.reply_text("Unexpected input. Use /cancel or provide info.")),
        MessageHandler(filters.COMMAND, lambda update, context: update.message.reply_text("Please finish current process or /cancel before new command.")),
    ],
)
