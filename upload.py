# upload.py
# Handles the file upload using Pyrogram.
# MODIFIED: Downloads thumbnail from URL to temp file if configured.

import logging
import os
import time
import asyncio
import tempfile # Added for temporary file
import requests # Added for downloading URL

# Import Pyrogram types/errors
from pyrogram import Client
from pyrogram.errors import FloodWait, MediaCaptionTooLong, BadRequest, BotMethodInvalid

# Import PTB types/errors
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest as PTBBadRequest

logger = logging.getLogger(__name__)

# --- NEW Helper Function to Download Thumbnail ---
async def _download_thumb(url: str) -> str | None:
    """Downloads image URL to a temporary file, returns path or None."""
    temp_thumb_path = None
    try:
        # Use asyncio.to_thread for synchronous requests call
        response = await asyncio.to_thread(requests.get, url, stream=True, timeout=10)
        response.raise_for_status()

        # Check content type (optional but recommended)
        content_type = response.headers.get('content-type')
        if not content_type or not content_type.startswith('image/'):
             logger.warning(f"Thumbnail URL content-type is not image: {content_type} ({url})")
             return None

        # Create a temporary file
        # Suffix helps Telegram/Pyrogram guess file type if needed
        suffix = ".jpg"
        if 'jpeg' in content_type: suffix = ".jpg"
        elif 'png' in content_type: suffix = ".png"
        elif 'webp' in content_type: suffix = ".webp"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            for chunk in response.iter_content(chunk_size=8192):
                temp_file.write(chunk)
            temp_thumb_path = temp_file.name # Get the path
            logger.info(f"Thumbnail downloaded successfully to temporary file: {temp_thumb_path}")

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download thumbnail URL {url}: {e}")
        temp_thumb_path = None # Ensure it's None on error
    except Exception as e:
         logger.error(f"An unexpected error occurred downloading thumbnail {url}: {e}", exc_info=True)
         temp_thumb_path = None
    finally:
        # Close response connection if open
        if 'response' in locals() and response:
            response.close()

    return temp_thumb_path
# --- End Helper Function ---


async def upload_file_pyrogram(
    pyrogram_client: Client,
    ptb_update: Update,
    ptb_context: ContextTypes.DEFAULT_TYPE,
    file_path: str,
    caption: str
):
    """ Uploads file using Pyrogram, downloads thumbnail from URL if needed. """

    # --- Get Config from Context ---
    upload_enabled = ptb_context.bot_data.get('upload_enabled', True)
    upload_mode = ptb_context.bot_data.get('upload_mode', 'Document')
    delete_after_upload = ptb_context.bot_data.get('delete_after_upload', True)
    target_chat_id = ptb_context.bot_data.get('target_chat_id')
    thumbnail_url = ptb_context.bot_data.get('thumbnail_url') # Get URL from context

    if not upload_enabled:
        # ... (skip logic) ...
        return True

    # --- Determine Destination ---
    original_chat_id = ptb_update.effective_chat.id
    # ... (upload_destination_chat_id logic) ...
    if target_chat_id and isinstance(target_chat_id, int) and target_chat_id != 0: upload_destination_chat_id = target_chat_id
    else: upload_destination_chat_id = original_chat_id
    logger.info(f"Upload destination: {upload_destination_chat_id}")

    base_filename = os.path.basename(file_path); upload_start_time = time.time(); last_update_time = 0
    status_message = None; status_message_id = None; upload_mode_str = upload_mode

    # --- Caption Truncation ---
    # ... (caption truncation) ...
    max_caption_length = 1024;
    if len(caption) > max_caption_length: caption = caption[:max_caption_length - 4] + "..."

    # --- Download Thumbnail (if URL provided) ---
    temp_thumb_path = None
    thumb_to_use = None
    if thumbnail_url and isinstance(thumbnail_url, str) and thumbnail_url.startswith(('http://', 'https://')):
        logger.info(f"Attempting to download thumbnail from URL: {thumbnail_url}")
        temp_thumb_path = await _download_thumb(thumbnail_url)
        if temp_thumb_path:
            thumb_to_use = temp_thumb_path # Store path for use later
        else:
            logger.warning("Failed to download thumbnail, proceeding without custom thumb.")
    # --- End Thumbnail Download ---

    try:
        # --- Send Initial Status ---
        # ... (send initial status logic) ...
        try: status_message = await ptb_context.bot.send_message(original_chat_id, f"⏫ Prep {upload_mode_str.lower()} upload: {caption}..."); status_message_id = status_message.message_id; logger.info(f"Pyro Start {upload_mode_str} '{base_filename}' -> {upload_destination_chat_id}. Status: {status_message_id} in {original_chat_id}")
        except Exception as e: logger.error(f"Failed init status msg: {e}"); status_message_id = None


        # --- Progress Callback (Unchanged - uses pyrogram edit) ---
        async def progress(current, total):
            nonlocal last_update_time, status_message_id, pyrogram_client
            # ... (progress logic unchanged) ...
            if not status_message_id: return
            try:
                now = time.time(); throttle_interval = 6; if now - last_update_time < throttle_interval: return
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

        # --- Determine Upload Function & Args ---
        sent_message = None; upload_func = None
        kwargs = {'chat_id': upload_destination_chat_id, 'caption': caption, 'progress': progress}
        # ***** MODIFIED: Add downloaded thumb path to kwargs if valid *****
        if thumb_to_use:
            kwargs['thumb'] = thumb_to_use
        # ***** END MODIFICATION *****
        attempted_mode = upload_mode_str

        # ... (Determine upload_func based on upload_mode_str - unchanged) ...
        if upload_mode_str == "Video": upload_func = pyrogram_client.send_video; kwargs['video'] = file_path; kwargs['supports_streaming'] = True
        elif upload_mode_str == "Audio": upload_func = pyrogram_client.send_audio; kwargs['audio'] = file_path
        else: attempted_mode = "Document"; upload_func = pyrogram_client.send_document; kwargs['document'] = file_path; kwargs['force_document'] = True


        try: # Inner try for upload/fallback
            logger.info(f"Attempt {attempted_mode} upload -> {upload_destination_chat_id} (Thumb: {'URL' if thumb_to_use else 'No'})...")
            if status_message_id: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⏫ Upload ({attempted_mode})...")
            sent_message = await upload_func(**kwargs) # Upload happens here
            logger.info(f"Success upload as {attempted_mode} -> {upload_destination_chat_id}.")
            upload_mode_str = attempted_mode
        except (MediaCaptionTooLong, BadRequest, BotMethodInvalid, TimeoutError, ValueError) as e:
            # --- Fallback Logic (Add thumb to fallback kwargs too) ---
            logger.error(f"Pyro err {attempted_mode} -> {upload_destination_chat_id}: {e}. Fallback...")
            if status_message_id: try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⚠️ {attempted_mode} fail: {str(e)[:100]}. Fallback...")
            except Exception: pass
            if attempted_mode != "Document":
                try:
                    logger.info("Attempt fallback Document..."); kwargs.pop('video', None); kwargs.pop('audio', None); kwargs.pop('supports_streaming', None); kwargs['document'] = file_path; kwargs['force_document'] = True
                    # Keep thumb in kwargs if it was valid
                    if status_message_id: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"⏫ Upload (Fallback)...")
                    sent_message = await pyrogram_client.send_document(**kwargs) # Fallback upload
                    upload_mode_str = "Document (Fallback)"; logger.info("Fallback success.")
                except Exception as fallback_e: logger.error(f"Fallback failed: {fallback_e}", exc_info=True); sent_message = None
            else: logger.error(f"Doc upload failed: {e}", exc_info=True); sent_message = None
            # ... (Edit status message on fallback failure) ...
            if not sent_message and status_message_id:
                 final_fallback_error = fallback_e if 'fallback_e' in locals() else e
                 try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=f"❌ Fallback/Doc fail: {str(final_fallback_error)[:100]}")
                 except Exception: pass


        # --- Check Success / Final Status / Cleanup ---
        # ... (This part remains the same - uses pyrogram edit/send) ...
        if not sent_message: # Check if upload succeeded
            logger.error(f"Upload failed {base_filename}. Mode: {upload_mode_str}"); final_error_text = f"❌ Upload failed ({upload_mode_str}): {caption}"
            if status_message_id: try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=final_error_text)
            except Exception: await pyrogram_client.send_message(chat_id=original_chat_id, text=final_error_text)
            elif original_chat_id: await pyrogram_client.send_message(chat_id=original_chat_id, text=final_error_text)
            # --- Attempt to delete temp thumb if upload failed ---
            if temp_thumb_path and os.path.exists(temp_thumb_path):
                 try: os.remove(temp_thumb_path); logger.info("Cleaned up temp thumb after failed upload.")
                 except Exception as e_del: logger.error(f"Error deleting temp thumb after failed upload: {e_del}")
            # --- End temp thumb cleanup on failure ---
            return False

        # --- Final Success Message (to ORIGINAL chat) ---
        final_message = f"✅ Upload OK ({upload_mode_str}): {caption}"; logger.info(f"Upload finish: '{base_filename}' ({upload_mode_str}) -> {upload_destination_chat_id}")
        if upload_destination_chat_id != original_chat_id: final_message += f"\n(Sent -> ID: {upload_destination_chat_id})"
        if status_message_id: try: await pyrogram_client.edit_message_text(chat_id=original_chat_id, message_id=status_message_id, text=final_message)
        except Exception: await pyrogram_client.send_message(original_chat_id, final_message)
        elif original_chat_id: await pyrogram_client.send_message(original_chat_id, final_message)

        # --- Delete Local File (Data file + Temp Thumb) ---
        if delete_after_upload:
             # Delete main file first
             try: os.remove(file_path); logger.info(f"Deleted local data file: {file_path}"); await ptb_context.bot.send_message(original_chat_id, f"🗑️ Local data deleted: {caption}", disable_notification=True)
             except OSError as e: logger.error(f"Failed delete {file_path}: {e}"); await ptb_context.bot.send_message(original_chat_id, f"⚠️ Failed delete local data: {caption}\n{e}")
        # --- Delete temp thumb regardless of delete_after_upload for data file ---
        if temp_thumb_path and os.path.exists(temp_thumb_path):
             try: os.remove(temp_thumb_path); logger.info("Cleaned up temp thumbnail file.")
             except Exception as e_del: logger.error(f"Error deleting temp thumbnail file: {e_del}")
        # --- End Deletion ---

        return True # Indicate overall success

    # --- Error Handling (unchanged - uses pyrogram edit/send) ---
    except FloodWait as fw:
        # ... FloodWait handling ...
        logger.warning(f"Upload FloodWait: {fw.value}s"); wait_time = fw.value + 2; error_text=f"⏳ Flood wait {wait_time}s..."; final_error_text=f"❌ Upload failed (FloodWait): {caption}"
        # ... (send/edit logic remains the same) ...
        # --- Attempt to delete temp thumb if upload failed ---
        if temp_thumb_path and os.path.exists(temp_thumb_path): try: os.remove(temp_thumb_path) except Exception: pass
        return False
    except Exception as e:
        # ... General exception handling ...
        logger.error(f"Unexpected upload error '{base_filename}': {e}", exc_info=True); error_message = f"❌ Upload failed ({upload_mode_str}): {caption}\nError: {str(e)[:200]}"
        # ... (send/edit logic remains the same) ...
        # --- Attempt to delete temp thumb if upload failed ---
        if temp_thumb_path and os.path.exists(temp_thumb_path): try: os.remove(temp_thumb_path) except Exception: pass
        return False
