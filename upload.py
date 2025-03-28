# upload.py
import logging
import os
import time
import asyncio

# Import Pyrogram types/errors
from pyrogram import Client
from pyrogram.errors import FloodWait, MediaCaptionTooLong, BadRequest, BotMethodInvalid

# Import PTB types/errors
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest as PTBBadRequest

# Import config settings
import config

logger = logging.getLogger(__name__)

async def upload_file_pyrogram(
    pyrogram_client: Client,
    ptb_update: Update,
    ptb_context: ContextTypes.DEFAULT_TYPE,
    file_path: str,
    caption: str
):
    """Uploads a file using Pyrogram with progress reporting via PTB, allowing different upload modes."""
    if not config.UPLOAD_ENABLED:
        logger.info(f"Upload skipped (disabled): {os.path.basename(file_path)}")
        await ptb_context.bot.send_message(ptb_update.effective_chat.id, f"ℹ️ Upload skipped (disabled): {caption}")
        return True

    chat_id = ptb_update.effective_chat.id
    base_filename = os.path.basename(file_path)
    upload_start_time = time.time()
    last_update_time = 0
    status_message = None
    status_message_id = None
    upload_mode_str = config.UPLOAD_MODE

    max_caption_length = 1024
    if len(caption) > max_caption_length:
        original_caption = caption
        caption = caption[:max_caption_length - 4] + "..."
        logger.warning(f"Caption truncated for {base_filename}. Original length: {len(original_caption)}")

    try:
        status_message = await ptb_context.bot.send_message(chat_id, f"⏫ Preparing {upload_mode_str.lower()} upload: {caption}...")
        status_message_id = status_message.message_id
        logger.info(f"Pyrogram: Starting {upload_mode_str} upload for '{base_filename}' to chat {chat_id}")

        # --- Progress Callback ---
        async def progress(current, total):
            nonlocal last_update_time, status_message_id
            try:
                now = time.time()
                throttle_interval = 5 if total > 0 else 10
                if now - last_update_time < throttle_interval: return

                percent_str = f"{round((current / total) * 100, 1)}%" if total > 0 else "??%"
                elapsed_time = now - upload_start_time
                speed = current / elapsed_time if elapsed_time > 0 else 0
                speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed > 0 else "N/A"
                eta_str = "N/A"
                if total > 0 and speed > 0:
                    eta = ((total - current) / speed)
                    eta_str = time.strftime("%H:%M:%S", time.gmtime(eta)) if eta >= 0 else "N/A"

                bar_len=10; filled_len = min(bar_len, int(bar_len * current / total)) if total > 0 and current >=0 else 0
                bar = '█' * filled_len + '░' * (bar_len - filled_len)
                size_str = f"{(current/1024/1024):.1f}MB{' / ' + str(round(total/1024/1024, 1)) + 'MB' if total > 0 else ''}"
                progress_text = f"⏫ Uploading ({upload_mode_str}): {caption}\n[{bar}] {percent_str}\n{size_str}\nSpeed: {speed_str} | ETA: {eta_str}"

                try:
                    if status_message_id:
                         await ptb_context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=progress_text)
                         last_update_time = now
                except PTBBadRequest as e:
                    if "Message is not modified" in str(e): pass
                    elif "Message to edit not found" in str(e): logger.warning(f"Status message {status_message_id} gone."); status_message_id = None
                    else: logger.error(f"Error editing progress (PTB): {e}")
                except FloodWait as fw: logger.warning(f"Progress FloodWait: {fw.value}s"); await asyncio.sleep(fw.value+1); last_update_time=time.time()+fw.value
                except Exception as e: logger.error(f"Unexpected progress edit error: {e}", exc_info=False)
            except Exception as e: logger.error(f"Critical progress callback error: {e}", exc_info=True)
        # --- End Progress Callback ---

        sent_message = None; upload_func = None
        kwargs = {'chat_id': chat_id, 'caption': caption, 'progress': progress}
        attempted_mode = upload_mode_str

        if upload_mode_str == "Video": upload_func = pyrogram_client.send_video; kwargs['video'] = file_path; kwargs['supports_streaming'] = True
        elif upload_mode_str == "Audio": upload_func = pyrogram_client.send_audio; kwargs['audio'] = file_path
        else: attempted_mode = "Document"; upload_func = pyrogram_client.send_document; kwargs['document'] = file_path; kwargs['force_document'] = True

        try: # Inner try for upload/fallback
            logger.info(f"Attempting upload as {attempted_mode}...")
            if status_message_id: await ptb_context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=f"⏫ Uploading ({attempted_mode})...")
            sent_message = await upload_func(**kwargs)
            logger.info(f"Successfully uploaded as {attempted_mode}.")
            upload_mode_str = attempted_mode
        except (MediaCaptionTooLong, BadRequest, BotMethodInvalid, TimeoutError, ValueError) as e:
            logger.error(f"Pyrogram: Error during {attempted_mode} upload: {e}. Trying fallback.")
            if status_message_id:
                 try: await ptb_context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=f"⚠️ {attempted_mode} fail: {str(e)[:100]}. Fallback...")
                 except Exception as edit_err: logger.error(f"Failed edit during fallback notify: {edit_err}")
            if attempted_mode != "Document":
                try:
                    logger.info("Attempting fallback as Document...")
                    kwargs.pop('video', None); kwargs.pop('audio', None); kwargs.pop('supports_streaming', None)
                    kwargs['document'] = file_path; kwargs['force_document'] = True
                    if status_message_id: await ptb_context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=f"⏫ Uploading (Fallback)...")
                    sent_message = await pyrogram_client.send_document(**kwargs)
                    upload_mode_str = "Document (Fallback)"; logger.info("Fallback success.")
                except Exception as fallback_e:
                    logger.error(f"Fallback Document failed: {fallback_e}", exc_info=True)
                    if status_message_id:
                         try: await ptb_context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=f"❌ Fallback fail: {str(fallback_e)[:100]}")
                         except Exception as fe_edit_err: logger.error(f"Failed edit on fallback failure: {fe_edit_err}")
                    sent_message = None
            else:
                 logger.error(f"Document upload failed: {e}", exc_info=True)
                 if status_message_id:
                      try: await ptb_context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=f"❌ Document fail: {str(e)[:100]}")
                      except Exception as df_edit_err: logger.error(f"Failed edit on document failure: {df_edit_err}")
                 sent_message = None

        if not sent_message:
            logger.error(f"Upload failed for {base_filename}. Mode: {upload_mode_str}")
            if not status_message_id: await ptb_context.bot.send_message(chat_id=chat_id, text=f"❌ Upload failed ({upload_mode_str}): {caption}")
            return False

        final_message = f"✅ Upload complete ({upload_mode_str}): {caption}"; logger.info(f"Upload finished: '{base_filename}' (Mode: {upload_mode_str})")
        if status_message_id:
            try: await ptb_context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=final_message)
            except Exception as final_edit_err: logger.error(f"Failed edit final success msg: {final_edit_err}"); await ptb_context.bot.send_message(chat_id, final_message)
        else: await ptb_context.bot.send_message(chat_id, final_message)

        if config.DELETE_AFTER_UPLOAD:
            try: os.remove(file_path); logger.info(f"Deleted local: {file_path}"); await ptb_context.bot.send_message(chat_id, f"🗑️ Local deleted: {caption}", disable_notification=True)
            except OSError as e: logger.error(f"Failed delete {file_path}: {e}"); await ptb_context.bot.send_message(chat_id, f"⚠️ Failed delete local: {caption}\n{e}")
        return True

    except FloodWait as fw:
        logger.warning(f"Upload FloodWait: {fw.value}s"); wait_time = fw.value + 2
        error_text=f"⏳ Flood wait: pausing {wait_time}s..."; final_error_text=f"❌ Upload failed (FloodWait): {caption}"
        if status_message_id:
            try: await ptb_context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=error_text); await asyncio.sleep(wait_time); await ptb_context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=final_error_text)
            except Exception as edit_err: logger.error(f"Failed edit during FloodWait: {edit_err}"); await ptb_context.bot.send_message(chat_id, final_error_text)
        else: await ptb_context.bot.send_message(chat_id, error_text); await asyncio.sleep(wait_time); await ptb_context.bot.send_message(chat_id, final_error_text)
        logger.error(f"Upload stopped (FloodWait): {base_filename}."); return False
    except Exception as e:
        logger.error(f"Unexpected upload error '{base_filename}': {e}", exc_info=True)
        error_message = f"❌ Upload failed ({upload_mode_str}): {caption}\nError: {str(e)[:200]}"
        if status_message_id:
            try: await ptb_context.bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=error_message)
            except Exception as edit_err: logger.error(f"Failed edit final error msg: {edit_err}"); await ptb_context.bot.send_message(chat_id, error_message)
        else: await ptb_context.bot.send_message(chat_id, error_message)
        return False
