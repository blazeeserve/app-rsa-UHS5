#!/usr/bin/env python3
"""
@Pubostinrobot — APK -> AppX tenant entry.

Send it a .apk / .apks / .xapk / .apkm; it replies with a success/fail message.
The extracted result.json is sent to a specified LOG_CHAT_ID.
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
LOG_CHAT_ID = (os.environ.get("LOG_CHAT_ID") or "").strip() # New Log Chat ID
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
    """Make sure java is present and APKEditor.jar is downloaded."""
    if not shutil.which("java"):
        logging.error("Java not found! Ensure you are deploying using the provided Dockerfile.")
        sys.exit(1)

    if not os.path.exists(APKEDITOR_JAR):
        _download(APKEDITOR_URL, APKEDITOR_JAR)

    if hasattr(extract, "set_baksmali"):
        extract.set_baksmali(["java", "-cp", APKEDITOR_JAR, "org.jf.baksmali.Main"])
        logging.info("baksmali configured: APKEditor.jar (bundled)")
    else:
        logging.warning("extract.py does not have a 'set_baksmali' function. Assuming it handles paths internally.")


app = Client("apk_extract_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

HELP = (
    "**AppX APK → tenant entry**\n\n"
    "Send me an APK (`.apk` / `.apks` / `.xapk` / `.apkm`). I will extract it and let you know if it was successful."
)


def summarize(entry) -> str:
    return (f"✅ **{entry.get('_id') or 'unknown'}**\n"
            f"• key : `{entry.get('key')}`\n"
            f"• pem : {len(entry.get('rsa_key') or '')} B")


@app.on_message(filters.command("start"))
async def _start(_, m: Message):
    await m.reply(HELP)


@app.on_message(filters.document)
async def _apk(_, m: Message):
    doc = m.document
    name = (doc.file_name or "").lower()
    if not name.endswith(APK_EXT):
        return
    
    if doc.file_size and doc.file_size > MAX_MB * 1024 * 1024:
        await m.reply(f"⚠️ too big ({doc.file_size/1e6:.0f} MB > {MAX_MB} MB)")
        return
        
    st = await m.reply(f"📥 downloading `{doc.file_name}` …")
    tmp = tempfile.mkdtemp(prefix="apkbot_")
    path = os.path.join(tmp, doc.file_name or "base.apk")
    
    try:
        await m.download(file_name=path)
        logging.info("job: %s (%s B)", doc.file_name, doc.file_size)
        await st.edit("🔍 extracting …")
        
        res = os.path.join(tmp, "result.json")
        entry = await asyncio.get_event_loop().run_in_executor(None, run_extract, path, res, None)
        
        logging.info("extracted: _id=%s key=%s", entry.get("_id"), entry.get("key"))
        
        # 1. Send the actual JSON file and summary to the Log Channel (if configured)
        if LOG_CHAT_ID:
            try:
                user_info = f"{m.from_user.first_name} (`{m.from_user.id}`)" if m.from_user else "Unknown"
                caption = f"📄 **File:** `{doc.file_name}`\n👤 **User:** {user_info}\n\n{summarize(entry)}"
                await app.send_document(chat_id=int(LOG_CHAT_ID), document=res, caption=caption)
            except Exception as log_err:
                logging.error(f"Failed to send to log chat: {log_err}")

        # 2. Send only a simple success message to the user
        await st.edit("✅ **Extraction Successful!**")
        
    except Exception as e:
        # Send simple fail message to the user
        await st.edit("❌ **Extraction Failed.**")
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if not BOT_TOKEN:
        sys.exit("Error: BOT_TOKEN is missing. Please set it in app.json or Heroku environment variables.")
    ensure_tools()
    logging.info("apk-extract bot starting…")
    app.run()
