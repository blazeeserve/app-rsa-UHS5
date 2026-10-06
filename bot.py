#!/usr/bin/env python3
"""
@Pubostinrobot — APK -> AppX tenant entry.

Send it a .apk / .apks / .xapk / .apkm; it replies with a success/fail message.
The extracted result.json is sent to a specified LOG_CHAT_ID.
Send .json files and use /short to merge and sort them into tenants.json.
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
LOG_CHAT_ID = (os.environ.get("LOG_CHAT_ID") or "").strip()
APK_EXT = (".apk", ".apks", ".xapk", ".apkm")
MAX_MB = int(os.environ.get("MAX_APK_MB", "700"))

APKEDITOR_URL = os.environ.get(
    "APKEDITOR_URL",
    "https://github.com/REAndroid/APKEditor/releases/download/V1.4.9/APKEditor-1.4.9.jar"
)
APKEDITOR_JAR = os.path.join(HERE, "APKEditor.jar")

# Memory queue to hold user JSONs before merging
user_json_queues = {}


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


def _sync_peer_http(bot_token: str, channel_id: str):
    """Synchronous HTTP call to force peer caching on Telegram's backend."""
    try:
        ping_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": channel_id,
            "text": "🔄 _Syncing server memory..._",
            "disable_notification": True
        }).encode("utf-8")
        
        req = urllib.request.Request(ping_url, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as response:
            r = json.loads(response.read().decode())
            if r.get("ok"):
                msg_id = r["result"]["message_id"]
                del_url = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
                del_payload = json.dumps({"chat_id": channel_id, "message_id": msg_id}).encode("utf-8")
                del_req = urllib.request.Request(del_url, data=del_payload, headers={'Content-Type': 'application/json'})
                urllib.request.urlopen(del_req, timeout=10)
    except Exception as e:
        logging.error(f"Peer Sync Error: {e}")


async def ensure_peer_id(bot_token: str, channel_id: str):
    """Executes the HTTP sync in a background thread to prevent blocking Pyrogram."""
    await asyncio.get_event_loop().run_in_executor(None, _sync_peer_http, bot_token, channel_id)
    await asyncio.sleep(1.5)


app = Client("apk_extract_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

HELP = (
    "**AppX APK → tenant entry**\n\n"
    "1️⃣ Send an APK (`.apk`/`.xapk`) to extract it.\n"
    "2️⃣ Send extracted `.json` files to me.\n"
    "3️⃣ Use /short to merge and alphabetically sort all sent JSONs into `tenants.json`."
)


def summarize(entry) -> str:
    return (f"✅ **{entry.get('_id') or 'unknown'}**\n"
            f"• key : `{entry.get('key')}`\n"
            f"• pem : {len(entry.get('rsa_key') or '')} B")


@app.on_message(filters.command("start"))
async def _start(_, m: Message):
    await m.reply(HELP)


@app.on_message(filters.command(["short", "sort"]))
async def _short(_, m: Message):
    uid = m.from_user.id
    if uid not in user_json_queues or not user_json_queues[uid]:
        await m.reply("⚠️ You haven't sent me any JSON files yet. Send some `.json` files, then type /short.")
        return

    st = await m.reply("🔄 Merging and sorting your files...")
    
    # Deduplicate based on _id
    merged_data = []
    seen_ids = set()
    
    for item in user_json_queues[uid]:
        item_id = item.get("_id")
        if item_id and item_id not in seen_ids:
            seen_ids.add(item_id)
            merged_data.append(item)
        elif not item_id:
            merged_data.append(item)

    # Sort alphabetically by _id
    merged_data.sort(key=lambda x: str(x.get("_id", "")).lower())
    
    tmp = tempfile.mkdtemp(prefix="apkbot_merge_")
    out_path = os.path.join(tmp, "tenants.json")
    
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(merged_data, f, indent=2)
            
        caption = f"✅ **Successfully Merged & Sorted!**\n📦 **Total Apps Added:** `{len(merged_data)}`"
        await m.reply_document(out_path, caption=caption)
        
        # Clear the queue after successful merge
        user_json_queues[uid] = []
    except Exception as e:
        await m.reply(f"❌ Error generating tenants.json: `{e}`")
    finally:
        await st.delete()
        shutil.rmtree(tmp, ignore_errors=True)


async def _process_json(m: Message):
    uid = m.from_user.id
    st = await m.reply("📥 Reading JSON...")
    
    # Assign a unique file name using the message ID to prevent collisions during simultaneous uploads
    safe_filename = f"downloads/json_{m.id}_{m.document.file_name or 'file.json'}"
    path = await m.download(file_name=safe_filename)
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        if uid not in user_json_queues:
            user_json_queues[uid] = []
            
        # Handle both list formats and single dict formats
        if isinstance(data, list):
            user_json_queues[uid].extend(data)
        elif isinstance(data, dict):
            user_json_queues[uid].append(data)
            
        count = len(user_json_queues[uid])
        await st.edit(f"✅ JSON added to queue! You now have **{count}** app entries ready.\n\nSend more JSONs, or type /short to merge.")
    except Exception as e:
        await st.edit(f"❌ Failed to parse JSON: `{e}`")
    finally:
        if path and os.path.exists(path):
            os.remove(path)


async def _process_apk(m: Message):
    doc = m.document
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
        
        if LOG_CHAT_ID:
            user_info = f"{m.from_user.first_name} (`{m.from_user.id}`)" if m.from_user else "Unknown"
            caption = f"📄 **File:** `{doc.file_name}`\n👤 **User:** {user_info}\n\n{summarize(entry)}"
            try:
                await app.send_document(chat_id=int(LOG_CHAT_ID), document=res, caption=caption)
            except Exception as log_err:
                if "Peer id invalid" in str(log_err) or "PEER_ID_INVALID" in str(log_err):
                    logging.info("Peer ID not cached. Triggering HTTP sync fallback...")
                    await ensure_peer_id(BOT_TOKEN, LOG_CHAT_ID)
                    await app.send_document(chat_id=int(LOG_CHAT_ID), document=res, caption=caption)
                else:
                    logging.error(f"Failed to send to log chat: {log_err}")

        await st.edit("✅ **Extraction Successful!**")
        
    except Exception as e:
        await st.edit("❌ **Extraction Failed.**")
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.on_message(filters.document)
async def _document_router(_, m: Message):
    name = (m.document.file_name or "").lower()
    if name.endswith(APK_EXT):
        await _process_apk(m)
    elif name.endswith(".json"):
        await _process_json(m)


if __name__ == "__main__":
    if not BOT_TOKEN:
        sys.exit("Error: BOT_TOKEN is missing. Please set it in app.json or Heroku environment variables.")
    ensure_tools()
    logging.info("apk-extract bot starting…")
    app.run()
