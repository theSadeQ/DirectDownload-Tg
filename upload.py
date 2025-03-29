# upload.py
# Handles the file upload using Pyrogram/Pyrofork.
# Gets config via context. Edits progress via pyro client. Downloads thumb URL.
# Force written by Cell 4 to ensure correctness.

import logging
import os
import time
import asyncio
import tempfile
import requests

# Import Pyrogram types/errors
from pyrogram import Client
from pyrogram.errors import FloodWait, MediaCaptionTooLong, BadRequest, BotMethodInvalid

# Import PTB types/errors
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest as PTBBadRequest

logger = logging.getLogger(__name__)

# --- Helper Function to Download Thumbnail ---
async def _download_thumb(url: str) -> str | None:
    """Downloads image URL to a temporary file, returns path or None."""
    temp_thumb_path = None
    response = None # Initialize response outside try
    try:
        logger.debug(f"Downloading thumbnail from: {url}")
        response = await asyncio.to_thread(requests.get, url, stream=True, timeout=15) # Slightly longer timeout
        response.raise_for_status()
        content_type = response.headers.get('content-type', '').lower()
        if not content_type.startswith('image/'):
            logger.warning(f"Thumbnail URL content-type is not image: {content_type} ({url})")
            return None # Return None if not an image type
        suffix = ".jpg";
        if 'png' in content_type: suffix = ".png"; elif 'webp' in content_type: suffix = ".webp"; elif 'gif' in content_type: suffix = ".gif"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="thumb_") as temp_file:
            downloaded_bytes = 0
            for chunk in response.iter_content(chunk_size=8192):
                if chunk: temp_file.write(chunk); downloaded_bytes += len(chunk)
            temp_thumb_path = temp_file.name; logger.info(f"Thumb ({downloaded_bytes} bytes) downloaded to: {temp_thumb_path}")
    except requests.exceptions.Timeout: logger.error(f"Timeout DL thumb URL {url}"); temp_thumb_path = None
    except requests.exceptions.RequestException as e: logger.error(f"Failed DL thumb URL {url}: {e}"); temp_thumb_path = None
    except Exception as e: logger.error(f"Unexpected thumb DL error {url}: {e}", exc_info=True); temp_thumb_path = None
    finally:
        if response is not None: response.close()
    if temp_thumb_path and os.path.exists(temp_thumb_path): return temp_thumb_path
    else:
        if temp_thumb_path and os.path.exists(temp_thumb_path): try: os.remove(temp_thumb_path)
        except Exception: pass; return None

# --- Main Upload Function ---
async def upload_file_pyrogram(
    pyrogram_client: Client,
    ptb_update: Update,
    ptb_context: ContextTypes.DEFAULT_TYPE,
    file_path: str,
    caption: str
):
    """ Uploads file using Pyrogram, downloads thumbnail, edits progress via pyro client. """
    upload_enabled = ptb_context.bot_data.get('upload_enabled', True); upload_mode = ptb_context.bot_data.get('upload_mode', 'Document'); delete_after_upload = ptb_context.bot_data.get('delete_after_upload', True); target_chat_id = ptb_context.bot_data.get('target_chat_id'); thumbnail_url = ptb_context.bot_data.get('thumbnail_url')
    if not upload_enabled: logger.info(f"Upload skipped (disabled): {os.path.basename(file_path)}"); await ptb_context.bot.send_message(ptb_update.effective_chat.id, f"ℹ️ Skip: {caption}"); return True
    original_chat_id = ptb_update.effective_chat.id; upload_destination_chat_id = target_chat_id if target_chat_id and isinstance(target_chat_id, int) and target_chat_id != 0 else original_chat_id
    logger.info(f"Upload destination: {upload_destination_chat_id}"); base_filename = os.path.basename(file_path); upload_start_time = time.time(); last_update_time = 0
    status_message = None; status_message_id = None; upload_mode_str = upload_mode
    max_caption_length = 1024;
    if len(caption) > max_caption_length: caption = caption[:max_caption_length - 4] + "..."
    temp_thumb_path = None; thumb_to_use = None
    if thumbnail_url and isinstance(thumbnail_url, str) and thumbnail_url.startswith(('http://', 'https://')):
        logger.info(f"Attempting DL thumb: {thumbnail_url}"); temp_thumb_path = await _download_thumb(thumbnail_url)
        if temp_thumb_path: thumb_to_use = temp_thumb_path
        else: logger.warning("Failed DL thumb.")

    try:
        try: status_message = await ptb_context.bot.send_message(original_chat_id, f"⏫ Prep {upload_mode_str.lower()} up: {caption}..."); status_message_id = status_message.message_id; logger.info(f"Pyro Start {upload_mode_str} '{base_filename}' -> {upload_destination_chat_id}. Status: {status_message_id} in {original_chat_id}")
        except Exception as e: logger.error(f"Failed init status msg: {e}"); status_message_id = None

        async def progress(current, total):
            nonlocal last_update_time, status_message_id, pyrogram_client
            if not status_message_id: return
            try:
                now = time.time(); throttle_interval = 6
                if now - last_update_time < throttle_interval: return
                percent_str = f"{round((current/total)*100,1)}%" if total>0 else "??%"; elapsed_time=now-upload_start_time; speed=current/elapsed_time if elapsed_time>0 else 0; speed_str=f"{speed/1024/1024:.2f}MB/s" if speed>0 else "N/A"; eta_str="N/A"
                if total>0 and speed>0: eta=((total-current)/speed); eta_str=time.strftime("%H:%M:%S",time.gmtime(eta)) if eta>=0 else "N/A"
                bar_len=10; filled_len = min(bar_len, int(bar_len*current/total)) if total>0 and current>=0 else 0; bar='█'*filled_len+'░'*(bar_len-filled_len); size_str=f"{(current/1024/1024):.1f}MB{' / '+str(round(total/1024/1024,1))+'MB' if total > 0 else ''}"
                progress_text = f"⏫ Upload ({upload_mode_str}): {caption}\n[{bar}] {percent_str}\n{size_str}\nSpeed: {speed_str}|ETA: {eta_str}"
                try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=progress_text); last_update_time = now
                except FloodWait as fw: logger.warning(f"Pyro prog FloodWait: {fw.value}s"); await asyncio.sleep(fw.value+1); last_update_time=time.time()+fw.value
                except BadRequest as py_e:
                    if "MODIFIED" in str(py_e): pass
                    elif "INVALID" in str(py_e) or "not found" in str(py_e).lower(): logger.warning(f"Status msg gone (Pyro). Stop edits."); status_message_id = None
                    else: logger.error(f"Edit progress err (Pyro): {py_e}")
                except Exception as e: logger.error(f"Unexpected prog edit err (Pyro): {e}", exc_info=False)
            except Exception as e: logger.error(f"Critical prog cb err: {e}", exc_info=True)

        sent_message = None; upload_func = None
        kwargs = {'chat_id': upload_destination_chat_id, 'caption': caption, 'progress': progress}
        if thumb_to_use: kwargs['thumb'] = thumb_to_use
        attempted_mode = upload_mode_str
        if upload_mode_str == "Video": upload_func = pyrogram_client.send_video; kwargs['video'] = file_path; kwargs['supports_streaming'] = True
        elif upload_mode_str == "Audio": upload_func = pyrogram_client.send_audio; kwargs['audio'] = file_path
        else: attempted_mode = "Document"; upload_func = pyrogram_client.send_document; kwargs['document'] = file_path; kwargs['force_document'] = True

        try: # Inner try for upload/fallback
            logger.info(f"Attempt {attempted_mode} up -> {upload_destination_chat_id} (T:{'Y' if thumb_to_use else 'N'})...")
            if status_message_id: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⏫ Upload ({attempted_mode})...")
            sent_message = await upload_func(**kwargs)
            logger.info(f"Success upload {attempted_mode} -> {upload_destination_chat_id}.")
            upload_mode_str = attempted_mode
        except (MediaCaptionTooLong, BadRequest, BotMethodInvalid, TimeoutError, ValueError) as e:
            logger.error(f"Pyro err {attempted_mode} -> {upload_destination_chat_id}: {e}. Fallback...")
            # Edit status message *before* fallback attempt
            if status_message_id:
                # Correctly indented try block
                try:
                    await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⚠️ {attempted_mode} fail: {str(e)[:100]}. Fallback...")
                except Exception as edit_err:
                     logger.error(f"Failed edit during fallback notify: {edit_err}")
            # Fallback logic
            if attempted_mode != "Document":
                try:
                    logger.info("Attempt fallback Document...")
                    # Corrected kwargs modification
                    kwargs.pop('video', None)
                    kwargs.pop('audio', None)
                    kwargs.pop('supports_streaming', None)
                    kwargs['document'] = file_path
                    kwargs['force_document'] = True
                    if status_message_id: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⏫ Upload (Fallback)...")
                    sent_message = await pyrogram_client.send_document(**kwargs)
                    upload_mode_str = "Document (Fallback)"; logger.info("Fallback success.")
                except Exception as fallback_e: logger.error(f"Fallback failed: {fallback_e}", exc_info=True); sent_message = None; final_fallback_error = fallback_e
            else: logger.error(f"Doc upload failed: {e}", exc_info=True); sent_message = None; final_fallback_error = e
            # Edit status after fallback attempt fails
            if not sent_message and status_message_id:
                 try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"❌ Fallback/Doc fail: {str(final_fallback_error)[:100]}")
                 except Exception: pass

        if not sent_message:
            logger.error(f"Upload failed {base_filename}. Mode: {upload_mode_str}"); final_error_text = f"❌ Upload failed ({upload_mode_str}): {caption}"
            if status_message_id: try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=final_error_text)
            except Exception: await pyrogram_client.send_message(chat_id=original_chat_id, text=final_error_text)
            elif original_chat_id: await pyrogram_client.send_message(chat_id=original_chat_id, text=final_error_text)
            if temp_thumb_path and os.path.exists(temp_thumb_path): try: os.remove(temp_thumb_path); logger.info("Cleaned temp thumb after fail.")
            except Exception as e_del: logger.error(f"Error deleting temp thumb after fail: {e_del}")
            return False
        final_message = f"✅ Upload OK ({upload_mode_str}): {caption}"; logger.info(f"Upload finish: '{base_filename}' ({upload_mode_str}) -> {upload_destination_chat_id}")
        if upload_destination_chat_id != original_chat_id: final_message += f"\n(Sent -> ID: {upload_destination_chat_id})"
        if status_message_id: try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=final_message)
        except Exception: await pyrogram_client.send_message(original_chat_id, final_message)
        elif original_chat_id: await pyrogram_client.send_message(original_chat_id, final_message)
        if delete_after_upload:
             try: os.remove(file_path); logger.info(f"Deleted data: {file_path}"); await ptb_context.bot.send_message(original_chat_id, f"🗑️ Local data deleted: {caption}", disable_notification=True)
             except OSError as e: logger.error(f"Failed delete {file_path}: {e}"); await ptb_context.bot.send_message(original_chat_id, f"⚠️ Failed delete data: {caption}\n{e}")
        if temp_thumb_path and os.path.exists(temp_thumb_path): try: os.remove(temp_thumb_path); logger.info("Cleaned temp thumbnail.")
        except Exception as e_del: logger.error(f"Error deleting temp thumb: {e_del}")
        return True

    except FloodWait as fw:
        logger.warning(f"Upload FloodWait: {fw.value}s"); wait_time = fw.value + 2; error_text=f"⏳ Flood wait {wait_time}s..."; final_error_text=f"❌ Upload failed (FloodWait): {caption}"
        if status_message_id: try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=error_text); await asyncio.sleep(wait_time); await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=final_error_text)
        except Exception: await pyrogram_client.send_message(original_chat_id, final_error_text)
        elif original_chat_id: await pyrogram_client.send_message(original_chat_id, error_text); await asyncio.sleep(wait_time); await pyrogram_client.send_message(original_chat_id, final_error_text)
        logger.error(f"Upload stopped (FloodWait): {base_filename}."); if temp_thumb_path and os.path.exists(temp_thumb_path): try: os.remove(temp_thumb_path) except Exception: pass; return False
    except Exception as e:
        logger.error(f"Unexpected upload error '{base_filename}': {e}", exc_info=True); error_message = f"❌ Upload failed ({upload_mode_str}): {caption}\nError: {str(e)[:200]}"
        if status_message_id: try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=error_message)
        except Exception: await pyrogram_client.send_message(original_chat_id, error_message)
        elif original_chat_id: await pyrogram_client.send_message(original_chat_id, error_message)
        if temp_thumb_path and os.path.exists(temp_thumb_path): try: os.remove(temp_thumb_path) except Exception: pass; return False

print("upload.py written successfully.") # Add confirmation
