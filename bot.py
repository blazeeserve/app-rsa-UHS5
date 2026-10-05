#!/usr/bin/env python3
"""
@Pubostinrobot — APK -> AppX tenant entry.

Send it a .apk / .apks / .xapk / .apkm; it replies with result.json:

  { "_id": "<tenant>api.classx.co.in", "key": "<KEY2 32>", "rsa_key": "-----BEGIN PRIVATE KEY-----…" }

Self-setup: on start it makes sure the local tools exist (java, baksmali, APKEditor.jar)
and downloads anything missing.
"""
from __future__ import annotations
import asyncio, json, logging, os, shutil, subprocess, sys, tempfile, traceback, urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from pyrogram import Client, filters
from pyrogram.types import Message

import extract
from extract import extract as run_extract

HERE = os.path.dirname(os.path.abspath(__file__))
API_ID = int(os.environ.get("API_ID", "28985973"))
API_HASH = os.environ.get("API_HASH", "96ee87847cdfba0a74f229a5a6e655c3")
BOT_TOKEN = (os.environ.get("BOT_" + "TOKEN") or "").strip()
APK_EXT = (".apk", ".apks", ".xapk", ".apkm")
MAX_MB = int(os.environ.get("MAX_APK_MB", "700"))

APKEDITOR_URL = os.environ.get(
    "APKEDITOR_URL",
    "https://github.com/REAndroid/APKEditor/releases/download/V1.4.9/APKEditor-1.4.9.jar")
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
    """Make sure java / APKEditor.jar / baksmali are present; install whatever is missing."""
    if not shutil.which("java"):
        logging.warning("java not found — trying apt-get install default-jre-headless")
        try:
            subprocess.run(["apt-get", "update", "-qq"], timeout=300)
            subprocess.run(["apt-get", "install", "-y", "-qq", "default-jre-headless"], timeout=600)
        except Exception as e:
            logging.error("java install failed: %s", e)

    # APKEditor.jar (also carries a fallback baksmali)
    if not os.path.exists(APKEDITOR_JAR):
        _download(APKEDITOR_URL, APKEDITOR_JAR)

    # baksmali: system -> APKEditor's bundled org.jf.baksmali -> apt libsmali-java
    sys_b = shutil.which("baksmali")
    if sys_b:
        extract.set_baksmali(sys_b)
        logging.info("baksmali: %s", sys_b)
        return
    if os.path.exists(APKEDITOR_JAR) and shutil.which("java"):
        r = subprocess.run(["java", "-cp", APKEDITOR_JAR, "org.jf.baksmali.Main", "--version"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        if b"baksmali" in r.stdout.lower():
            extract.set_baksmali(["java", "-cp", APKEDITOR_JAR, "org.jf.baksmali.Main"])
            logging.info("baksmali: APKEditor.jar (bundled)")
            return
    logging.warning("baksmali missing — trying apt-get install libsmali-java")
    try:
        subprocess.run(["apt-get", "install", "-y", "-qq", "libsmali-java"], timeout=600)
    except Exception as e:
        logging.error("libsmali-java install failed: %s", e)
    sys_b = shutil.which("baksmali")
    if sys_b:
        extract.set_baksmali(sys_b)
        logging.info("baksmali: %s (installed)", sys_b)
    else:
        logging.error("NO baksmali available — extraction will fail")


app = Client("apk_extract_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

HELP = """**AppX APK → tenant entry**

Send me an APK (`.apk` / `.apks` / `.xapk` / `.apkm`). I reply with **result.json** in the exact tenants.json shape:

```json
{ "_id": "<tenant>api.classx.co.in", "key": "<KEY2 32>", "rsa_key": "-----BEGIN PRIVATE KEY-----…" }
```

• `key`  ← `JavaAESCipher.KEY2` (smali)
• `rsa_key` ← the app's PEM in `assets/`
• `_id`  ← host at the exact `post/userLogin` member, canonicalised to `<tenant>api.classx.co.in`"""


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
        sys.exit("set BOT_TOKEN")
    ensure_tools()
    logging.info("apk-extract bot starting…")
    app.run()
