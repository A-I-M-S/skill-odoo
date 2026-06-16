import sys
import os
import json
import logging
import urllib.request
from scripts.config import load_env_file, Settings
from scripts.processor import process_inbox

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
load_env_file()
settings = Settings.from_env()
print("Starting process_inbox (dry_run=False)")
try:
    process_inbox(settings, dry_run=False)
except Exception as e:
    print(f"Exception: {e}")
