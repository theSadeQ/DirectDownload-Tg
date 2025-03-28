# utils.py
import os
import re
import urllib.parse
import logging

logger = logging.getLogger(__name__)

def write_failed_downloads_to_file(failed_items, downloader_name, download_directory):
    """Writes the list of failed source URLs to a text file."""
    if not failed_items:
        return None
    file_path = os.path.join(download_directory, f"failed_downloads_{downloader_name}.txt")
    try:
        os.makedirs(download_directory, exist_ok=True)
        with open(file_path, "w") as f:
            f.write(f"# Failed source URLs for {downloader_name}\n")
            for item in failed_items:
                f.write(f"{item}\n")
        logger.info(f"List of failed {downloader_name} source URLs saved to: {file_path}")
        return file_path
    except Exception as e:
        logger.error(f"Error writing failed items to file: {e}")
        return None

def clean_filename(filename):
    """Cleans filenames by removing/replacing invalid characters."""
    try:
        filename = urllib.parse.unquote(filename, encoding='utf-8', errors='replace')
    except Exception:
        pass
    filename = filename.replace('%20', ' ')
    cleaned_filename = re.sub(r'[\\/:*?"<>|]', '_', filename)
    cleaned_filename = re.sub(r'_+', '_', cleaned_filename)
    cleaned_filename = cleaned_filename.strip('._ ')
    cleaned_filename = cleaned_filename[:250] # Limit length
    return cleaned_filename if cleaned_filename else "downloaded_file"

def extract_filename_from_url(url):
    """Extracts and cleans the filename from a given URL."""
    try:
        if not isinstance(url, str) or not url.lower().startswith(('http://', 'https://')):
             logger.warning(f"Skipping potentially invalid URL: {str(url)[:100]}")
             return None
        parsed_url = urllib.parse.urlparse(url)
        path = parsed_url.path
        filename_raw = os.path.basename(path)
        if not filename_raw and path != '/':
             segments = path.strip('/').split('/')
             if segments:
                 filename_raw = segments[-1]
        if not filename_raw:
             filename_raw = parsed_url.netloc.replace('.', '_') + "_file"
        decoded_filename = urllib.parse.unquote(filename_raw, encoding='utf-8', errors='replace')
        return clean_filename(decoded_filename)
    except Exception as e:
        logger.warning(f"Error extracting filename from URL {url}: {e}")
        return None

async def run_and_report_process(update, context, download_upload_task, service_name, download_dir):
    """ Awaits the download/upload task and reports final status / failed file list. """
    chat_id = update.effective_chat.id
    failed_sources = []
    try:
        failed_sources = await download_upload_task
        final_msg = f"🏁 <b>{service_name}</b> process finished."
        logger.info(f"{service_name} process finished for chat {chat_id}.")
        if failed_sources:
            final_msg += f"\n⚠️ Encountered {len(failed_sources)} failure(s)."
            logger.warning(f"{len(failed_sources)} failure(s) for {service_name} in chat {chat_id}.")
            failed_file_path = write_failed_downloads_to_file(failed_sources, service_name, download_dir)
            if failed_file_path:
                 escaped_path = failed_file_path.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                 final_msg += f"\nList saved to <pre>{escaped_path}</pre>"
        else:
            final_msg += f"\n✅🎉 All items seem to have completed successfully!"
            logger.info(f"All {service_name} items completed successfully for chat {chat_id}.")

        # Import ParseMode here locally or pass it if preferred
        from telegram.constants import ParseMode
        await context.bot.send_message(chat_id, final_msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error during the {service_name} run/report phase: {e}", exc_info=True)
        try:
            error_text = str(e).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            from telegram.constants import ParseMode # Import again for error message
            await context.bot.send_message(chat_id, f"🚨 Unexpected error after <b>{service_name}</b> process finished.\nError: <pre>{error_text[:500]}</pre>", parse_mode=ParseMode.HTML)
        except Exception as send_error:
             logger.error(f"Failed to send final error message to user {chat_id}: {send_error}")
