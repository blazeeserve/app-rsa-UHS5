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

    # Safely check if extract.py supports set_baksmali before calling it
    if hasattr(extract, "set_baksmali"):
        extract.set_baksmali(["java", "-cp", APKEDITOR_JAR, "org.jf.baksmali.Main"])
        logging.info("baksmali configured: APKEditor.jar (bundled)")
    else:
        logging.warning("extract.py does not have a 'set_baksmali' function. Assuming it handles paths internally.")


app = Client("apk_extract_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

# Using standard string concatenation to prevent Heroku SyntaxError on triple quotes
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
        await m.reply_document(res, caption=summarize(entry))
        await st.delete()
        
    except Exception as e:
        await st.edit(f"❌ failed: `{e}`")
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if not BOT_TOKEN:
        sys.exit("Error: BOT_TOKEN is missing. Please set it in app.json or Heroku environment variables.")
    ensure_tools()
    logging.info("apk-extract bot starting…")
    app.run()
