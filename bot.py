#!/usr/bin/env python3
"""
@Pubostinrobot — APK -> AppX tenant entry.

Send it a .apk / .apks / .xapk / .apkm; it replies with result.json:

  { "_id": "<tenant>api.classx.co.in", "key": "<KEY2 32>", "rsa_key": "-----BEGIN PRIVATE KEY-----…" }

Self-setup: on start it makes sure APKEditor.jar exists and downloads it if missing.
Java is expected to be installed via Docker.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from pyrogram import Client, filters
from pyrogram.types import Message

import extract
from extract import extract as run_extract

HERE = os.path.dirname(os.path.abspath(__file__))
API_ID = int(os.environ.get("API_ID", "28985973"))
API_HASH = os.environ.get("API_HASH", "96ee87847cdfba0a74f229a5a6e655c3")
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
APK_EXT = (".apk", ".apks", ".xapk", ".apkm")
MAX_MB = int(os.environ.get("MAX_APK_MB", "700"))

APKEDITOR_URL = os.environ.get(
    "APKEDITOR_URL",
    "https://github.com/REAndroid/APKEditor/releases/download/V1.4.9/APKEditor-1.4.9.jar"
)
APKEDITOR_JAR = os.path.join(HERE, "APKEditor.jar")


def _download(url: str, dest: str) -> bool:
    try:
        logging.info("downloading %s -> %s", url, dest)
        with urllib.request.urlopen(url, timeout=180) as r, open(dest + ".part", "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        os.replace(dest + ".part", dest)
        logging.info("  ok (%d B)", os.path.getsize(dest))
        return True
    except Exception as e:
        logging.error("  download failed: %s", e)
        try:
            os.remove(dest + ".part")
        except Exception:
            pass
        return False


def ensure_tools():
    """Make sure java is present (via Docker) and APKEditor.jar is downloaded."""
    if not shutil.which("java"):
        logging.error("Java not found! Ensure you are deploying using the provided Dockerfile.")
        sys.exit(1)

    # Download APKEditor.jar if it's not present
    if not os.path.exists(APKEDITOR_JAR):
        _download(APKEDITOR_URL, APKEDITOR_JAR)

    # Set baksmali to use the bundled org.jf.baksmali inside APKEditor.jar
    extract.set_baksmali(["java", "-cp", APKEDITOR_JAR, "org.jf.baksmali.Main"])
    logging.info("baksmali: APKEditor.jar (bundled)")


app = Client("apk_extract_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

HELP = (
    "**AppX APK → tenant entry**\n\n"
    "Send me an APK (`.apk` / `.apks` / `.xapk` / `.apkm`). I reply with **result.json** in the exact tenants.json shape:\n\n"
    "```json\n"
    '{ "_id": "<tenant>api.classx.co.in", "key": "<KEY2 32>", "rsa_key": "-----BEGIN PRIVATE KEY-----…" }\n'
    "```\n\n"
    "• `key`  ← `JavaAESCipher.KEY2` (smali)\n"
    "• `rsa_key` ← the app's PEM in `assets/`\n"
    "• `_id`  ← host at the exact `post/userLogin` member, canonicalised to `<tenant>api.classx.co.in`"
)
