%%writefile bot.py
# --- Cell 3 Content Saved as bot.py ---
# Contains ALL configuration and the main bot execution logic.
# ⚠️ EDIT SENSITIVE & NON-SENSITIVE CONFIGURATION BELOW BEFORE RUNNING! ⚠️

import nest_asyncio
nest_asyncio.apply() # Must be called early

import logging
import asyncio
import os

# Import framework components
from pyrogram import Client
from telegram import Update
from telegram.ext import Application, CommandHandler

# Import local modules (handlers needs to be imported AFTER this file is written)
# We will import handlers inside main_async or globally if no circular dependency risk
# For now, let's import inside main_async where Application is created.

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s [%(levelname)s] - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# --- !! ALL CONFIGURATION - EDIT HERE !! ---
# ---------------------------------------------
# --- Sensitive Settings ---
API_ID = 1234567  # ⚠️ Replace with your actual API_ID (integer)
API_HASH = "YOUR_API_HASH_HERE"  # ⚠️ Replace with your actual API_HASH (string)
_TOKEN_MAPPING = {
    "Bot 1 - 777...": "7772724138:...",
    # ... your tokens ...
}
BOT_SELECTION_KEY = "Bot 1 - 777..." # ⚠️ Select Bot Key
TARGET_CHAT_ID = None # ⚠️ SET TARGET CHAT ID HERE (e.g., -100...) or None

# --- Non-Sensitive Settings (Moved from config.py) ---
DOWNLOAD_DIR = "/content/DirectDownloaderTelbot" # Default for Colab
UPLOAD_ENABLED = True
UPLOAD_MODE = "Document" # Options: "Document", "Video", "Audio"
DELETE_AFTER_UPLOAD = True
# ---------------------------------------------
# --- !! END CONFIGURATION !! ---


# --- Main Asynchronous Function ---
async def main_async(token: str, api_id: int, api_hash: str, target_chat_id: int | None):
    """Initializes clients, sets up handlers, and runs the bot."""
    # Import handlers now, after bot.py is written and accessible
    import handlers

    logger.info("Starting bot...")

    # --- Ensure Download Directory Exists ---
    # Uses DOWNLOAD_DIR defined above in this file
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        logger.info(f"Download directory: {DOWNLOAD_DIR}")
    except OSError as e:
        logger.critical(f"CRITICAL: Could not create download directory {DOWNLOAD_DIR}: {e}. Exiting.")
        return

    # --- Initialize Pyrogram Client ---
    pyrogram_client = Client(
        "direct_downloader_bot_session",
        api_id=api_id,
        api_hash=api_hash,
        bot_token=token
    )

    # --- Initialize PTB Application ---
    application = Application.builder().token(token).build()

    # --- Store Data for Handlers ---
    application.bot_data['pyrogram_client'] = pyrogram_client
    # Pass all config values needed by other modules via bot_data
    # This avoids needing other modules to import 'bot.py' directly
    application.bot_data['target_chat_id'] = target_chat_id
    application.bot_data['download_dir'] = DOWNLOAD_DIR
    application.bot_data['upload_enabled'] = UPLOAD_ENABLED
    application.bot_data['upload_mode'] = UPLOAD_MODE
    application.bot_data['delete_after_upload'] = DELETE_AFTER_UPLOAD

    # --- Add Handlers (from handlers module) ---
    application.add_handler(handlers.conv_handler)
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("cancel", handlers.cancel))

    # --- Start Clients and Run ---
    # ... (start/run/stop logic remains the same) ...
    stop_event = asyncio.Event()
    try:
        logger.info("Starting Pyrogram client..."); await pyrogram_client.start(); logger.info("Pyrogram client started.")
        me = await pyrogram_client.get_me(); logger.info(f"Running as @{me.username} (ID: {me.id})")
        logger.info("Starting PTB application..."); await application.initialize(); await application.start(); await application.updater.start_polling(allowed_updates=Update.ALL_TYPES); logger.info("Bot is up and running! Stop cell to shut down.")
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit): logger.info("Shutdown signal."); stop_event.set()
    except Exception as e: logger.critical(f"CRITICAL RUNTIME ERROR: {e}", exc_info=True); stop_event.set()
    finally:
        logger.info("Shutdown sequence...");
        if application.updater and application.updater.running: logger.info("Stopping PTB polling..."); await application.updater.stop()
        if application._running: logger.info("Stopping PTB application..."); await application.stop()
        await application.shutdown(); logger.info("PTB stopped.")
        if pyrogram_client.is_connected: logger.info("Stopping Pyrogram client..."); await pyrogram_client.stop(); logger.info("Pyrogram stopped.")
        logger.info("Shutdown complete.")


# --- Main Execution Block ---
# This code runs when the cell is executed AFTER being saved as bot.py
# Or when running '!python bot.py'

# --- Calculate Token and Validate Config ---
TELEGRAM_BOT_TOKEN = None
IS_CONFIG_VALID = True

# Validate config defined above
if not isinstance(API_ID, int) or API_ID <= 0: logger.error("INVALID API_ID"); IS_CONFIG_VALID = False
if not isinstance(API_HASH, str) or len(API_HASH) < 20: logger.error("INVALID API_HASH"); IS_CONFIG_VALID = False
if BOT_SELECTION_KEY not in _TOKEN_MAPPING: logger.error(f"Invalid BOT_SELECTION_KEY"); IS_CONFIG_VALID = False
else: TELEGRAM_BOT_TOKEN = _TOKEN_MAPPING[BOT_SELECTION_KEY]
if TARGET_CHAT_ID is not None and not isinstance(TARGET_CHAT_ID, int): logger.error("INVALID TARGET_CHAT_ID"); IS_CONFIG_VALID = False
valid_upload_modes = ["Document", "Video", "Audio"];
if UPLOAD_MODE not in valid_upload_modes: logger.error(f"INVALID UPLOAD_MODE '{UPLOAD_MODE}'"); IS_CONFIG_VALID = False

# --- Run the Bot if Config is Valid ---
if __name__ == "__main__": # Ensure this block runs only when script is executed directly
    if IS_CONFIG_VALID and TELEGRAM_BOT_TOKEN:
        print("Configuration valid. Starting bot...")
        logger.info(f"Selected bot: {BOT_SELECTION_KEY}")
        logger.info(f"Uploads {'ENABLED' if UPLOAD_ENABLED else 'DISABLED'}")
        if UPLOAD_ENABLED: logger.info(f"Mode: {UPLOAD_MODE}, Delete: {DELETE_AFTER_UPLOAD}, Target: {TARGET_CHAT_ID or 'Original'}")
        try:
            asyncio.run(main_async(TELEGRAM_BOT_TOKEN, API_ID, API_HASH, TARGET_CHAT_ID))
        except KeyboardInterrupt: logger.info("KeyboardInterrupt received."); print("\nStopping...")
        except Exception as e: logger.critical(f"Failed main loop: {e}", exc_info=True); print(f"❌ Critical error: {e}")
    else: print("\n🛑 Invalid config in bot.py. Fix errors and restart.")
    print("\n--- Bot execution finished ---")
