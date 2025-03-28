# upload.py
# Handles the file upload using Pyrogram.
# MODIFIED: Uses pyrogram_client to edit progress messages instead of ptb_context.bot.

import logging
import os
import time
import asyncio

# Import Pyrogram types/errors
from pyrogram import Client
from pyrogram.errors import FloodWait, MediaCaptionTooLong, BadRequest, BotMethodInvalid

# Import PTB types/errors (only needed for type hints and initial message)
from telegram import Update
from telegram.ext import ContextTypes
# We still use PTB's BadRequest for the *initial* status message edit check,
# but Pyrogram's FloodWait inside the progress loop.
from telegram.error import BadRequest as PTBBadRequest

# NO 'import config' needed here anymore

logger = logging.getLogger(__name__)

async def upload_file_pyrogram(
    pyrogram_client: Client, # We will use this for edits now
    ptb_update: Update,
    ptb_context: ContextTypes.DEFAULT_TYPE, # Still needed for bot_data and initial message
    file_path: str,
    caption: str
):
    """
    Uploads file using Pyrogram. Edits progress using pyrogram_client.
    Gets config from context.bot_data.
    """
    # --- Get Config from Context ---
    upload_enabled = ptb_context.bot_data.get('upload_enabled', True)
    upload_mode = ptb_context.bot_data.get('upload_mode', 'Document')
    delete_after_upload = ptb_context.bot_data.get('delete_after_upload', True)
    target_chat_id = ptb_context.bot_data.get('target_chat_id')

    if not upload_enabled:
        # ... (skip logic unchanged) ...
        logger.info(f"Upload skipped (disabled): {os.path.basename(file_path)}")
        await ptb_context.bot.send_message(ptb_update.effective_chat.id, f"ℹ️ Upload skipped (disabled): {caption}")
        return True

    # Determine destination chat ID
    original_chat_id = ptb_update.effective_chat.id
    if target_chat_id and isinstance(target_chat_id, int) and target_chat_id != 0:
        upload_destination_chat_id = target_chat_id
    else:
        upload_destination_chat_id = original_chat_id
    logger.info(f"Upload destination: {upload_destination_chat_id}")

    base_filename = os.path.basename(file_path)
    upload_start_time = time.time(); last_update_time = 0
    status_message = None; status_message_id = None
    upload_mode_str = upload_mode

    # --- Caption Truncation (Unchanged) ---
    max_caption_length = 1024
    if len(caption) > max_caption_length: caption = caption[:max_caption_length - 4] + "..."

    try:
        # Send initial status message to the ORIGINAL chat using PTB
        # We need this message_id to edit later
        try:
            status_message = await ptb_context.bot.send_message(original_chat_id, f"⏫ Preparing {upload_mode_str.lower()} upload: {caption}...")
            status_message_id = status_message.message_id
            logger.info(f"Pyrogram: Starting {upload_mode_str} upload '{base_filename}' -> {upload_destination_chat_id}. Status msg ID: {status_message_id} in chat {original_chat_id}")
        except Exception as initial_msg_err:
             logger.error(f"Failed to send initial status message to {original_chat_id}: {initial_msg_err}")
             status_message_id = None # Cannot edit progress if initial send failed

        # --- Progress Callback Function (Nested) ---
        async def progress(current, total):
            # Use nonlocal to modify variables in the outer scope
            nonlocal last_update_time, status_message_id
            # Use pyrogram_client passed to the outer function
            nonlocal pyrogram_client

            # Check if we have a message to edit
            if not status_message_id:
                return # Cannot show progress if initial message failed

            try:
                now = time.time()
                throttle_interval = 6 # Slightly increase throttle maybe
                if now - last_update_time < throttle_interval: return

                # ... (progress calculation logic remains the same) ...
                percent_str = f"{round((current / total) * 100, 1)}%" if total > 0 else "??%"
                elapsed_time = now - upload_start_time; speed = current / elapsed_time if elapsed_time > 0 else 0
                speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed > 0 else "N/A"; eta_str = "N/A"
                if total > 0 and speed > 0: eta = ((total - current) / speed); eta_str = time.strftime("%H:%M:%S", time.gmtime(eta)) if eta >= 0 else "N/A"
                bar_len=10; filled_len = min(bar_len, int(bar_len * current / total)) if total > 0 and current >=0 else 0
                bar = '█' * filled_len + '░' * (bar_len - filled_len); size_str = f"{(current/1024/1024):.1f}MB{' / ' + str(round(total/1024/1024, 1)) + 'MB' if total > 0 else ''}"
                progress_text = f"⏫ Upload ({upload_mode_str}): {caption}\n[{bar}] {percent_str}\n{size_str}\nSpeed: {speed_str}|ETA: {eta_str}"

                # ***** CHANGE: Edit status message using PYROGRAM client *****
                try:
                    await pyrogram_client.edit_message_text(
                         chat_id=original_chat_id, # Still edit in original chat
                         message_id=status_message_id,
                         text=progress_text
                    )
                    last_update_time = now
                # Catch Pyrogram's specific FloodWait here if needed
                except FloodWait as fw:
                    logger.warning(f"Pyrogram progress edit FloodWait: sleeping {fw.value}s")
                    await asyncio.sleep(fw.value + 1)
                    last_update_time = time.time() + fw.value # Adjust last update time
                except BadRequest as pyrogram_edit_err: # Catch Pyrogram's BadRequest
                    # Common issue: MESSAGE_NOT_MODIFIED or MESSAGE_ID_INVALID
                    if "MESSAGE_NOT_MODIFIED" in str(pyrogram_edit_err):
                        pass # Ignore if text is same
                    elif "MESSAGE_ID_INVALID" in str(pyrogram_edit_err) or "message to edit not found" in str(pyrogram_edit_err).lower():
                        logger.warning(f"Status message {status_message_id} gone (Pyrogram). Stop edits.")
                        status_message_id = None # Stop trying to edit
                    else:
                        # Log other Pyrogram edit errors
                        logger.error(f"Error editing progress (Pyrogram): {pyrogram_edit_err}")
                except Exception as e_pyro_edit: # Catch any other unexpected error
                     logger.error(f"Unexpected error editing progress (Pyrogram): {e_pyro_edit}", exc_info=False)
                # ***** END CHANGE *****

            except Exception as e: logger.error(f"Critical prog cb err: {e}", exc_info=True)
        # --- End Progress Callback ---

        sent_message = None; upload_func = None
        # Use the determined upload_destination_chat_id here
        kwargs = {'chat_id': upload_destination_chat_id, 'caption': caption, 'progress': progress}
        attempted_mode = upload_mode_str

        # ... (Determine upload_func based on upload_mode_str - unchanged) ...
        if upload_mode_str == "Video": upload_func = pyrogram_client.send_video; kwargs['video'] = file_path; kwargs['supports_streaming'] = True
        elif upload_mode_str == "Audio": upload_func = pyrogram_client.send_audio; kwargs['audio'] = file_path
        else: attempted_mode = "Document"; upload_func = pyrogram_client.send_document; kwargs['document'] = file_path; kwargs['force_document'] = True

        try: # Inner try for upload/fallback
            logger.info(f"Attempt {attempted_mode} upload -> {upload_destination_chat_id}...")
            # Edit status in ORIGINAL chat using PYROGRAM
            if status_message_id:
                 try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⏫ Upload ({attempted_mode})...")
                 except Exception: pass # Ignore if this edit fails

            # --- Actual Upload Call (unchanged) ---
            sent_message = await upload_func(**kwargs)
            logger.info(f"Success upload as {attempted_mode} -> {upload_destination_chat_id}.")
            upload_mode_str = attempted_mode
        except (MediaCaptionTooLong, BadRequest, BotMethodInvalid, TimeoutError, ValueError) as e:
            # --- Fallback Logic ---
            logger.error(f"Pyro err {attempted_mode} -> {upload_destination_chat_id}: {e}. Fallback...")
            # Edit status in ORIGINAL chat using PYROGRAM
            if status_message_id:
                 try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⚠️ {attempted_mode} fail: {str(e)[:100]}. Fallback...")
                 except Exception: pass # Ignore edit error

            if attempted_mode != "Document":
                try:
                    logger.info("Attempt fallback Document..."); kwargs.pop('video', None); kwargs.pop('audio', None); kwargs.pop('supports_streaming', None); kwargs['document'] = file_path; kwargs['force_document'] = True
                    # Edit status in ORIGINAL chat using PYROGRAM
                    if status_message_id: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⏫ Upload (Fallback)...")
                    sent_message = await pyrogram_client.send_document(**kwargs) # Fallback uses upload_destination_chat_id
                    upload_mode_str = "Document (Fallback)"; logger.info("Fallback success.")
                except Exception as fallback_e: logger.error(f"Fallback failed: {fallback_e}", exc_info=True); sent_message = None
            else: logger.error(f"Doc upload failed: {e}", exc_info=True); sent_message = None

            # If fallback also failed, edit status message using PYROGRAM
            if not sent_message and status_message_id:
                 final_fallback_error = fallback_e if 'fallback_e' in locals() else e
                 try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"❌ Fallback/Doc fail: {str(final_fallback_error)[:100]}")
                 except Exception: pass # Ignore final edit error

        # --- Check Success ---
        if not sent_message:
            logger.error(f"Upload failed {base_filename}. Mode: {upload_mode_str}")
            final_error_text = f"❌ Upload failed ({upload_mode_str}): {caption}"
            # Edit/send failure message to ORIGINAL chat using PYROGRAM
            if status_message_id:
                 try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=final_error_text)
                 except Exception: # If edit fails, try sending
                      try: await pyrogram_client.send_message(chat_id=original_chat_id, text=final_error_text)
                      except Exception as send_err: logger.error(f"Failed final Pyro upload fail msg: {send_err}")
            elif original_chat_id: # If initial status failed, send new message
                 try: await pyrogram_client.send_message(chat_id=original_chat_id, text=final_error_text)
                 except Exception as send_err: logger.error(f"Failed final Pyro upload fail msg: {send_err}")
            return False

        # --- Final Success Message (Edit/Send using PYROGRAM) ---
        final_message = f"✅ Upload OK ({upload_mode_str}): {caption}"; logger.info(f"Upload finish: '{base_filename}' ({upload_mode_str}) -> {upload_destination_chat_id}")
        if upload_destination_chat_id != original_chat_id: final_message += f"\n(Sent -> ID: {upload_destination_chat_id})"
        if status_message_id:
            try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=final_message)
            except Exception as final_edit_err: logger.error(f"Failed Pyro edit final success msg: {final_edit_err}"); await pyrogram_client.send_message(original_chat_id, final_message) # Send if edit fails
        elif original_chat_id: await pyrogram_client.send_message(original_chat_id, final_message)


        # --- Delete Local File (Confirmation sent via PTB - less critical) ---
        if delete_after_upload:
             try: os.remove(file_path); logger.info(f"Deleted local: {file_path}"); await ptb_context.bot.send_message(original_chat_id, f"🗑️ Local deleted: {caption}", disable_notification=True) # Keep PTB for this minor msg
             except OSError as e: logger.error(f"Failed delete {file_path}: {e}"); await ptb_context.bot.send_message(original_chat_id, f"⚠️ Failed delete local: {caption}\n{e}")
        return True

    # --- Error Handling (FloodWait, General - Edit/Send final error using PYROGRAM) ---
    except FloodWait as fw:
        logger.warning(f"Upload FloodWait: {fw.value}s"); wait_time = fw.value + 2; error_text=f"⏳ Flood wait {wait_time}s..."; final_error_text=f"❌ Upload failed (FloodWait): {caption}"
        if status_message_id:
            try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=error_text); await asyncio.sleep(wait_time); await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=final_error_text)
            except Exception as edit_err: logger.error(f"Failed Pyro edit during FloodWait: {edit_err}"); await pyrogram_client.send_message(original_chat_id, final_error_text)
        elif original_chat_id: await pyrogram_client.send_message(original_chat_id, error_text); await asyncio.sleep(wait_time); await pyrogram_client.send_message(original_chat_id, final_error_text)
        logger.error(f"Upload stopped (FloodWait): {base_filename}."); return False
    except Exception as e:
        logger.error(f"Unexpected upload error '{base_filename}': {e}", exc_info=True); error_message = f"❌ Upload failed ({upload_mode_str}): {caption}\nError: {str(e)[:200]}"
        if status_message_id:
            try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=error_message)
            except Exception as edit_err: logger.error(f"Failed Pyro edit final error msg: {edit_err}"); await pyrogram_client.send_message(original_chat_id, error_message)
        elif original_chat_id: await pyrogram_client.send_message(original_chat_id, error_message)
        return False
