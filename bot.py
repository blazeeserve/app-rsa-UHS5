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

# ---------- PATH FIX for Heroku apt buildpack (worker dyno) ----------
for _p in ("/app/.apt/usr/bin", "/app/.apt/usr/sbin"):
    if os.path.isdir(_p) and _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _p + ":" + os.environ.get("PATH", "")
_libdir = "/app/.apt/usr/lib/x86_64-linux-gnu"
if os.path.isdir(_libdir):
    os.environ["LD_LIBRARY_PATH"] = _libdir + ":" + os.environ.get("LD_LIBRARY_PATH", "")
# ----------------------------------------------------------------------

API_ID = int(os.environ.get("API_ID", "28985973"))
API_HASH = os.environ.get("API_HASH", "96ee87847cdfba0a74f229a5a6e655c3")
BOT_TOKEN = (os.environ.get("BOT_" + "TOKEN") or "").strip()
APK_EXT = (".apk", ".apks", ".xapk", ".apkm")
MAX_MB = int(os.environ.get("MAX_APK_MB", "500"))

# Java heap cap — baksmali ko OOM se bachata hai
JAVA_HEAP = os.environ.get("JAVA_HEAP", "-Xmx700m")

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
        os.environ.setdefault("JAVA_OPTS", JAVA_HEAP)
        extract.set_baksmali(sys_b)
        logging.info("baksmali: %s (JAVA_OPTS=%s)", sys_b, JAVA_HEAP)
        return

    if os.path.exists(APKEDITOR_JAR) and shutil.which("java"):
        r = subprocess.run(
            ["java", JAVA_HEAP, "-cp", APKEDITOR_JAR, "org.jf.baksmali.Main", "--version"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        if b"baksmali" in r.stdout.lower():
            extract.set_baksmali(
                ["java", JAVA_HEAP, "-cp", APKEDITOR_JAR, "org.jf.baksmali.Main"])
            logging.info("baksmali: APKEditor.jar (bundled, heap=%s)", JAVA_HEAP)
            return

    logging.warning("baksmali missing — trying apt-get install libsmali-java")
    try:
        subprocess.run(["apt-get", "install", "-y", "-qq", "libsmali-java"], timeout=600)
    except Exception as e:
        logging.error("libsmali-java install failed: %s", e)

    sys_b = shutil.which("baksmali")
    if sys_b:
        os.environ.setdefault("JAVA_OPTS", JAVA_HEAP)
        extract.set_baksmali(sys_b)
        logging.info("baksmali: %s (installed, JAVA_OPTS=%s)", sys_b, JAVA_HEAP)
    else:
        logging.error("NO baksmali available — extraction will fail")


app = Client("apk_extract_bot", api_id=API_ID, api_hash=API_HASH,
             bot_token=BOT_TOKEN, in_memory=True)

HELP = """**AppX APK → tenant entry**

Send me an APK (`.apk` / `.apks` / `.xapk` / `.apkm`). I reply with **result.json** in the exact tenants.json shape:

```json
{ "_id": "<tenant>api.classx.co.in", "key": "<KEY2 32>", "rsa_key": "-----BEGIN PRIVATE KEY-----…" }
