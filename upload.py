# upload.py
# MODIFIED: Accesses ALL config via context.bot_data

import logging
import os
import time
import asyncio
from pyrogram import Client
from pyrogram.errors import FloodWait, MediaCaptionTooLong, BadRequest, BotMethodInvalid
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest as PTBBadRequest

# NO 'import config' needed here anymore

logger = logging.getLogger(__name__)

async def upload_file_pyrogram(
    pyrogram_client: Client,
    ptb_update: Update,
    ptb_context: ContextTypes.DEFAULT_TYPE, # Context contains bot_data
    file_path: str,
    caption: str
):
    """ Uploads file using Pyrogram, gets ALL config from context.bot_data. """

    # --- Get Config from Context ---
    upload_enabled = ptb_context.bot_data.get('upload_enabled', True) # Default to True if missing
    upload_mode = ptb_context.bot_data.get('upload_mode', 'Document') # Default to Document
    delete_after_upload = ptb_context.bot_data.get('delete_after_upload', True) # Default to True
    target_chat_id = ptb_context.bot_data.get('target_chat_id') # Default is None

    if not upload_enabled:
        logger.info(f"Upload skipped (disabled via config): {os.path.basename(file_path)}")
        await ptb_context.bot.send_message(ptb_update.effective_chat.id, f"ℹ️ Upload skipped (disabled): {caption}")
        return True

    original_chat_id = ptb_update.effective_chat.id
    if target_chat_id and isinstance(target_chat_id, int) and target_chat_id != 0:
        upload_destination_chat_id = target_chat_id
        logger.info(f"Uploading to TARGET_CHAT_ID: {upload_destination_chat_id}")
    else:
        upload_destination_chat_id = original_chat_id
        logger.info(f"Uploading to original chat: {upload_destination_chat_id}")

    base_filename = os.path.basename(file_path)
    upload_start_time = time.time(); last_update_time = 0
    status_message = None; status_message_id = None
    upload_mode_str = upload_mode # Use variable fetched from context

    # --- Caption Truncation (Unchanged) ---
    max_caption_length = 1024
    if len(caption) > max_caption_length: caption = caption[:max_caption_length - 4] + "..."

    try:
        # Send initial status message to ORIGINAL chat
        status_message = await ptb_context.bot.send_message(original_chat_id, f"⏫ Prep {upload_mode_str.lower()} upload: {caption}...")
        status_message_id = status_message.message_id
        logger.info(f"Pyro Start {upload_mode_str} upload '{base_filename}' -> {upload_destination_chat_id}")

        # --- Progress Callback (Unchanged - uses original_chat_id via context) ---
        async def progress(current, total):
            nonlocal last_update_time, status_message_id
            # ... (Progress logic exactly as before) ...
            try:
                now = time.time(); throttle_interval = 5 if total > 0 else 10
                if now - last_update_time < throttle_interval: return
                percent_str = f"{round((current / total) * 100, 1)}%" if total > 0 else "??%"
                elapsed_time = now - upload_start_time; speed = current / elapsed_time if elapsed_time > 0 else 0
                speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed > 0 else "N/A"; eta_str = "N/A"
                if total > 0 and speed > 0: eta = ((total - current) / speed); eta_str = time.strftime("%H:%M:%S", time.gmtime(eta)) if eta >= 0 else "N/A"
                bar_len=10; filled_len = min(bar_len, int(bar_len * current / total)) if total > 0 and current >=0 else 0
                bar = '█' * filled_len + '░' * (bar_len - filled_len); size_str = f"{(current/1024/1024):.1f}MB{' / ' + str(round(total/1024/1024, 1)) + 'MB' if total > 0 else ''}"
                progress_text = f"⏫ Upload ({upload_mode_str}): {caption}\n[{bar}] {percent_str}\n{size_str}\nSpeed: {speed_str}|ETA: {eta_str}"
                try:
                    if status_message_id: await ptb_context.bot.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=progress_text); last_update_time = now
                except PTBBadRequest as e:
                    if "modified" in str(e): pass
                    elif "not found" in str(e): logger.warning(f"Status msg gone."); status_message_id = None
                    else: logger.error(f"Edit progress err (PTB): {e}")
                except FloodWait as fw: logger.warning(f"Prog FloodWait: {fw.value}s"); await asyncio.sleep(fw.value+1); last_update_time=time.time()+fw.value
                except Exception as e: logger.error(f"Unexpected prog edit err: {e}", exc_info=False)
            except Exception as e: logger.error(f"Critical prog cb err: {e}", exc_info=True)

        # --- Determine Upload Function ---
        sent_message = None; upload_func = None
        kwargs = {'chat_id': upload_destination_chat_id, 'caption': caption, 'progress': progress}
        attempted_mode = upload_mode_str # Use mode from context

        if upload_mode_str == "Video": upload_func = pyrogram_client.send_video; kwargs['video'] = file_path; kwargs['supports_streaming'] = True
        elif upload_mode_str == "Audio": upload_func = pyrogram_client.send_audio; kwargs['audio'] = file_path
        else: attempted_mode = "Document"; upload_func = pyrogram_client.send_document; kwargs['document'] = file_path; kwargs['force_document'] = True

        # --- Execute Upload Attempt (Unchanged - uses kwargs)---
        try:
            logger.info(f"Attempt {attempted_mode} upload -> {upload_destination_chat_id}...")
            if status_message_id: await ptb_context.bot.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⏫ Upload ({attempted_mode})...")
            sent_message = await upload_func(**kwargs)
            logger.info(f"Success upload as {attempted_mode} -> {upload_destination_chat_id}.")
            upload_mode_str = attempted_mode
        except (MediaCaptionTooLong, BadRequest, BotMethodInvalid, TimeoutError, ValueError) as e:
            # --- Fallback Logic (Unchanged - edits status in original_chat_id) ---
            logger.error(f"Pyro err {attempted_mode} -> {upload_destination_chat_id}: {e}. Fallback...")
            if status_message_id:
                 try: await ptb_context.bot.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⚠️ {attempted_mode} fail: {str(e)[:100]}. Fallback...")
                 except Exception: pass # Ignore edit error
            if attempted_mode != "Document":
                try:
                    logger.info("Attempt fallback Document..."); kwargs.pop('video', None); kwargs.pop('audio', None); kwargs.pop('supports_streaming', None); kwargs['document'] = file_path; kwargs['force_document'] = True
                    if status_message_id: await ptb_context.bot.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⏫ Upload (Fallback)...")
                    sent_message = await pyrogram_client.send_document(**kwargs) # Fallback uses upload_destination_chat_id
                    upload_mode_str = "Document (Fallback)"; logger.info("Fallback success.")
                except Exception as fallback_e: logger.error(f"Fallback failed: {fallback_e}", exc_info=True); sent_message = None
            else: logger.error(f"Doc upload failed: {e}", exc_info=True); sent_message = None

        # --- Check Success (Unchanged - edits/sends status to original_chat_id) ---
        if not sent_message:
            logger.error(f"Upload failed {base_filename}. Mode: {upload_mode_str}")
            final_error_text = f"❌ Upload failed ({upload_mode_str}): {caption}"
            # ... (send/edit logic for original_chat_id remains same) ...
            if status_message_id:
                 try: await ptb_context.bot.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=final_error_text)
                 except Exception: pass
            elif original_chat_id: await ptb_context.bot.send_message(chat_id=original_chat_id, text=final_error_text)
            return False

        # --- Final Success Message (to ORIGINAL chat) ---
        # ... (logic remains same - uses original_chat_id) ...
        final_message = f"✅ Upload OK ({upload_mode_str}): {caption}"
        if upload_destination_chat_id != original_chat_id: final_message += f"\n(Sent -> ID: {upload_destination_chat_id})"
        logger.info(f"Upload finish: '{base_filename}' ({upload_mode_str}) -> {upload_destination_chat_id}")
        if status_message_id:
            try: await ptb_context.bot.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=final_message)
            except Exception: await ptb_context.bot.send_message(original_chat_id, final_message)
        elif original_chat_id: await ptb_context.bot.send_message(original_chat_id, final_message)

        # --- Delete Local File ---
        # Use delete_after_upload from context
        if delete_after_upload:
            # ... (Deletion logic unchanged - confirmation sent to ORIGINAL chat) ...
             try: os.remove(file_path); logger.info(f"Deleted local: {file_path}"); await ptb_context.bot.send_message(original_chat_id, f"🗑️ Local deleted: {caption}", disable_notification=True)
             except OSError as e: logger.error(f"Failed delete {file_path}: {e}"); await ptb_context.bot.send_message(original_chat_id, f"⚠️ Failed delete local: {caption}\n{e}")
        return True

    # --- Error Handling (Unchanged - uses original_chat_id) ---
    except FloodWait as fw:
        # ... (logic unchanged - reports to original_chat_id) ...
        logger.warning(f"Upload FloodWait: {fw.value}s"); wait_time = fw.value + 2; error_text=f"⏳ Flood wait {wait_time}s..."; final_error_text=f"❌ Upload failed (FloodWait): {caption}"
        if status_message_id:
            try: await ptb_context.bot.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=error_text); await asyncio.sleep(wait_time); await ptb_context.bot.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=final_error_text)
            except Exception: await ptb_context.bot.send_message(original_chat_id, final_error_text)
        elif original_chat_id: await ptb_context.bot.send_message(original_chat_id, error_text); await asyncio.sleep(wait_time); await ptb_context.bot.send_message(original_chat_id, final_error_text)
        logger.error(f"Upload stopped (FloodWait): {base_filename}."); return False
    except Exception as e:
        # ... (logic unchanged - reports to original_chat_id) ...
        logger.error(f"Unexpected upload error '{base_filename}': {e}", exc_info=True); error_message = f"❌ Upload failed ({upload_mode_str}): {caption}\nError: {str(e)[:200]}"
        if status_message_id:
            try: await ptb_context.bot.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=error_message)
            except Exception: await ptb_context.bot.send_message(original_chat_id, error_message)
        elif original_chat_id: await ptb_context.bot.send_message(original_chat_id, error_message)
        return False
