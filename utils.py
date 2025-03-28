# utils.py
# Contains general utility functions for the bot, including file operations and splitting.

import os
import re
import urllib.parse
import logging
import math
import asyncio # For sleep in splitter

# Import config to access DOWNLOAD_DIR
# Assumes config.py is in the same root directory or accessible via Python path
try:
    import config
except ImportError:
    logging.critical("CRITICAL: config.py not found or cannot be imported by utils.py!")
    # Define a fallback or re-raise to ensure failure if config is essential
    raise ImportError("Essential configuration file 'config.py' not found.") from None

logger = logging.getLogger(__name__)

# --- Constants ---
# Define the split size (e.g., 1.95 GiB to be safe under Telegram's 2GB limit)
# 2000 * 1024 * 1024 = 2,097,152,000 bytes. Let's use 1.95 GiB = 2,093,796,352 bytes
SPLIT_SIZE = 1950 * 1024 * 1024 # Approx 1.95 GiB


# --- File/String Utilities ---

def write_failed_downloads_to_file(failed_items, downloader_name, download_directory):
    """Writes the list of failed source URLs to a text file."""
    if not failed_items:
        return None
    # Use the download_directory passed as argument (should come from config)
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
        pass # Ignore decoding errors if any
    filename = filename.replace('%20', ' ') # Specific fix before general cleaning
    # Remove or replace characters invalid in common filesystems (Windows/Linux/Mac)
    # \ / : * ? " < > |
    cleaned_filename = re.sub(r'[\\/:*?"<>|]', '_', filename)
    # Replace multiple underscores resulting from substitution
    cleaned_filename = re.sub(r'_+', '_', cleaned_filename)
    # Remove leading/trailing spaces, dots, underscores
    cleaned_filename = cleaned_filename.strip('._ ')
    # Ensure filename is not empty and doesn't exceed typical limits (e.g., 250 chars)
    cleaned_filename = cleaned_filename[:250]
    return cleaned_filename if cleaned_filename else "downloaded_file"

def extract_filename_from_url(url):
    """Extracts and cleans the filename from a given URL."""
    try:
        if not isinstance(url, str) or not url.lower().startswith(('http://', 'https://')):
             logger.warning(f"Skipping potentially invalid URL: {str(url)[:100]}")
             return None
        parsed_url = urllib.parse.urlparse(url)
        path = parsed_url.path
        # Get the last part of the path
        filename_raw = os.path.basename(path)
        # If the path ends in '/', basename might be empty, try getting the last non-empty segment
        if not filename_raw and path != '/':
             segments = path.strip('/').split('/')
             if segments:
                 filename_raw = segments[-1]
        # If still no filename, use part of the domain or a default
        if not filename_raw:
             filename_raw = parsed_url.netloc.replace('.', '_') + "_file"
        # Decode URL encoding and clean
        decoded_filename = urllib.parse.unquote(filename_raw, encoding='utf-8', errors='replace')
        return clean_filename(decoded_filename)
    except Exception as e:
        logger.warning(f"Error extracting filename from URL {url}: {e}")
        return None


# --- File Splitting Utilities ---

async def split_if_needed(original_path, context=None, chat_id=None):
    """
    Checks file size and splits it into parts if it exceeds SPLIT_SIZE.
    Returns a list of file paths (original file or parts), or None on error.
    Sends status updates using context if provided.
    """
    try:
        if not os.path.exists(original_path):
            logger.error(f"Splitting error: File not found at {original_path}")
            return None

        file_size = os.path.getsize(original_path)
        base_filename = os.path.basename(original_path)
        logger.info(f"File '{base_filename}' size: {file_size} bytes.")

        if file_size <= SPLIT_SIZE:
            logger.info("File size is within limit, no splitting needed.")
            return [original_path] # Return list containing only the original path

        # --- File needs splitting ---
        num_parts = math.ceil(file_size / SPLIT_SIZE)
        logger.info(f"File exceeds {SPLIT_SIZE} bytes limit. Splitting into {num_parts} parts.")
        status_message = f"✂️ File '{base_filename}' is large ({file_size/1024/1024:.1f} MB), splitting into {num_parts} parts..."
        if context and chat_id:
             try: # Best effort status message
                await context.bot.send_message(chat_id, status_message)
             except Exception as e: logger.warning(f"Failed to send split status: {e}")

        # Create a subdirectory for parts within the main download directory
        parts_dir = os.path.join(config.DOWNLOAD_DIR, base_filename + "_parts")
        os.makedirs(parts_dir, exist_ok=True)
        part_paths = []
        bytes_written_total = 0
        read_block_size = 10 * 1024 * 1024 # Read in 10MB chunks

        with open(original_path, "rb") as infile:
            for i in range(num_parts):
                part_num_str = f"{i+1:03d}" # e.g., 001, 002
                part_filename = f"{base_filename}.part{part_num_str}"
                part_path = os.path.join(parts_dir, part_filename)
                part_paths.append(part_path)
                bytes_written_part = 0
                logger.info(f"Creating part {i+1}/{num_parts}: {part_filename}")

                try:
                    with open(part_path, "wb") as outfile:
                        while bytes_written_part < SPLIT_SIZE:
                            # Determine how much more to read for this part, up to read_block_size
                            bytes_to_read = min(read_block_size, SPLIT_SIZE - bytes_written_part)
                            chunk = infile.read(bytes_to_read)
                            if not chunk:
                                break # End of source file
                            outfile.write(chunk)
                            bytes_written_part += len(chunk)
                            bytes_written_total += len(chunk) # Track overall progress (optional)

                    if bytes_written_part == 0: # Handle case where last part might be empty
                        logger.warning(f"Part {i+1} was empty, removing.")
                        os.remove(part_path)
                        part_paths.pop()
                        num_parts -=1 # Adjust total part count if last one was empty
                        break # Stop loop

                    logger.info(f"Finished part {i+1}/{num_parts}, size: {bytes_written_part}")

                except Exception as part_write_e:
                    logger.error(f"Error writing part {part_path}: {part_write_e}", exc_info=True)
                    if context and chat_id: await context.bot.send_message(chat_id, f"❌ Error writing part {i+1} for '{base_filename}'.")
                    # Cleanup already written parts? Maybe not, let user handle temp files on error.
                    return None # Indicate failure

                await asyncio.sleep(0.05) # Small sleep to prevent blocking event loop entirely

        if bytes_written_total != file_size:
             logger.warning(f"Total bytes written ({bytes_written_total}) does not match original size ({file_size}) for {base_filename}. Check split logic.")
             # Decide if this is critical enough to return None or just log

        logger.info(f"Splitting complete for '{base_filename}'. Total bytes processed: {bytes_written_total}")
        # Send completion message
        if context and chat_id:
            try: await context.bot.send_message(chat_id, f"✅ Splitting of '{base_filename}' into {len(part_paths)} parts complete.")
            except Exception: pass

        return part_paths

    except FileNotFoundError:
        logger.error(f"Splitting error: File not found at {original_path}")
        return None
    except Exception as e:
        logger.error(f"Error during file splitting check for {original_path}: {e}", exc_info=True)
        if context and chat_id:
            try: await context.bot.send_message(chat_id, f"❌ Error checking/splitting file '{os.path.basename(original_path)}': {e}")
            except Exception: pass
        return None # Indicate failure


async def cleanup_split_parts(original_path, parts):
    """Deletes split parts and their directory, and optionally the original file."""
    if not parts or len(parts) <= 1: # Only cleanup if actual splitting occurred (more than 1 part)
        logger.debug(f"Cleanup skipped for {original_path} as no splitting occurred or only one part.")
        return

    parts_dir = os.path.dirname(parts[0]) # Get directory from first part
    logger.info(f"Cleaning up {len(parts)} split parts in {parts_dir}")
    deleted_parts_count = 0
    try:
        for part_path in parts:
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                    deleted_parts_count += 1
                except Exception as e_part:
                    logger.error(f"Failed to delete part {part_path}: {e_part}")
        logger.info(f"Deleted {deleted_parts_count} part files.")

        # Attempt to remove the directory if it exists and is empty
        if os.path.isdir(parts_dir):
             try:
                 os.rmdir(parts_dir)
                 logger.info(f"Removed parts directory: {parts_dir}")
             except OSError as e_dir: # Directory might not be empty if a part failed deletion
                  logger.warning(f"Could not remove parts directory {parts_dir} (maybe not empty?): {e_dir}")

        # Delete original large file only if splitting occurred (len(parts)>1)
        if os.path.exists(original_path):
             try:
                 os.remove(original_path)
                 logger.info(f"Deleted original large file: {original_path}")
             except Exception as e_orig:
                  logger.error(f"Failed to delete original large file {original_path}: {e_orig}")

    except Exception as e:
        logger.error(f"Error during cleanup of split parts for {original_path}: {e}", exc_info=True)
