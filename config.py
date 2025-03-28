# config.py
import logging

# --- Download & Upload Settings (Non-Sensitive) ---
DOWNLOAD_DIR = "/content/DirectDownloaderTelbot"  # Default for Colab, change if running elsewhere
UPLOAD_ENABLED = True
# Options: "Document", "Video", "Audio"
UPLOAD_MODE = "Video"
DELETE_AFTER_UPLOAD = True

# --- Logging (Optional: configure basic logging settings here if desired) ---
# Example: LOG_LEVEL = logging.INFO
# (Logging setup is mainly handled in bot.py now)
