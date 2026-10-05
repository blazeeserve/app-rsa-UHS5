#!/usr/bin/env python3
"""
extract.py — Core extraction logic for Pubostinrobot.

Public API used by bot.py:
    set_baksmali(cmd)                          -> register baksmali prefix
    extract(apk_path, out_json_path, _unused)  -> dict {_id, key, rsa_key}

Handles .apk / .apks / .xapk / .apkm.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from typing import Iterable, List, Optional, Union

log = logging.getLogger("extract")

# ------------------------------------------------------------------ baksmali
_BAKSMALI: Optional[List[str]] = None


def set_baksmali(cmd: Union[str, List[str], None]) -> None:
    """Register baksmali invocation prefix.

    Examples:
        set_baksmali("baksmali")
        set_baksmali(["java", "-cp", "APKEditor.jar", "org.jf.baksmali.Main"])
    """
    global _BAKSMALI
    if cmd is None:
        _BAKSMALI = None
    elif isinstance(cmd, str):
        _BAKSMALI = [cmd]
    else:
        _BAKSMALI = list(cmd)


def _baksmali_prefix() -> List[str]:
    if _BAKSMALI:
        return list(_BAKSMALI)
    sys_b = shutil.which("baksmali")
    if sys_b:
        return [sys_b]
    jar = os.path.join(os.path.dirname(os.path.abspath(__file__)), "APKEditor.jar")
    if os.path.exists(jar):
        return ["java", "-cp", jar, "org.jf.baksmali.Main"]
    raise RuntimeError("baksmali not configured")


# ------------------------------------------------------------- fs helpers
def _iter_files(root: str) -> Iterable[str]:
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            yield os.path.join(dirpath, name)


def _read_text(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _run(cmd: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    log.info("run: %s", " ".join(cmd))
    return subprocess.run(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout)


_BINARY_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
    ".so", ".dex", ".arsc", ".ttf", ".otf", ".woff", ".woff2",
    ".mp3", ".mp4", ".wav", ".ogg", ".zip", ".jar", ".apk",
)


def _is_texty(path: str) -> bool:
    low = path.lower()
    return not low.endswith(_BINARY_EXT)


# --------------------------------------------------------- APK unpacking
def _unpack_apk(path: str, workdir: str) -> str:
    """Return path to a single base .apk (handles split archives)."""
    low = path.lower()
    if low.endswith(".apk"):
        return path

    with zipfile.ZipFile(path) as z:
        apks = [n for n in z.namelist() if n.lower().endswith(".apk")]
        if not apks:
            raise RuntimeError("no .apk inside archive")

        base = None
        for n in apks:
            if os.path.basename(n).lower() in ("base.apk", "base-master.apk"):
                base = n
                break
        if base is None:
            base = max(apks, key=lambda n: z.getinfo(n).file_size)

        out = os.path.join(workdir, "base.apk")
        with z.open(base) as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        log.info("unpacked base: %s -> %s", base, out)
        return out


# ---------------------------------------------------------- regex patterns
_KEY_FIELD_RE = re.compile(
    r'\.field\s+[^\n]*?KEY2[^\n]*?=\s*"([^"\n]+)"',
    re.IGNORECASE)
_KEY_ANY_RE = re.compile(r'"([A-Za-z0-9+/=_\-]{32})"')
_PEM_RE = re.compile(
    r'-----BEGIN (?:RSA |EC |)PRIVATE KEY-----'
    r'.*?'
    r'-----END (?:RSA |EC |)PRIVATE KEY-----',
    re.DOTALL)
_HOST_CLASSX_RE = re.compile(
    r'([a-z0-9][a-z0-9\-]*api\.classx\.co\.in)',
    re.IGNORECASE)
_HOST_URL_RE = re.compile(r'https?://([A-Za-z0-9\.\-]+)', re.IGNORECASE)


# ------------------------------------------------------------ finders
def _find_key2(smali_root: str) -> Optional[str]:
    """Find JavaAESCipher.KEY2 (smali .field ... = "...")."""
    # 1) file literally named JavaAESCipher*
    for f in _iter_files(smali_root):
        if not f.endswith(".smali"):
            continue
        if "JavaAESCipher" not in os.path.basename(f):
            continue
        txt = _read_text(f)
        m = _KEY_FIELD_RE.search(txt)
        if m:
            return m.group(1)

    # 2) any smali with a KEY2 field
    for f in _iter_files(smali_root):
        if not f.endswith(".smali"):
            continue
        txt = _read_text(f)
        if "KEY2" not in txt:
            continue
        m = _KEY_FIELD_RE.search(txt)
        if m:
            return m.group(1)

    # 3) any 32-char literal in a JavaAESCipher* file
    for f in _iter_files(smali_root):
        if "JavaAESCipher" not in os.path.basename(f):
            continue
        txt = _read_text(f)
        m = _KEY_ANY_RE.search(txt)
        if m:
            return m.group(1)

    return None


def _find_pem(contents_root: str) -> Optional[str]:
    """Find PEM private key (prefer assets/, then anywhere)."""
    assets = os.path.join(contents_root, "assets")
    for base in (assets, contents_root):
        if not os.path.isdir(base):
            continue
        for f in _iter_files(base):
            try:
                if os.path.getsize(f) > 2 * 1024 * 1024:
                    continue
            except OSError:
                continue
            if not _is_texty(f):
                # PEM might still be inside oddly-named file; try anyway
                pass
            txt = _read_text(f)
            if "PRIVATE KEY" not in txt:
                continue
            m = _PEM_RE.search(txt)
            if m:
                pem = m.group(0).strip()
                if not pem.endswith("\n"):
                    pem += "\n"
                return pem
    return None


def _find_host(smali_root: str, contents_root: str) -> Optional[str]:
    """Find the API host tied to post/userLogin, canonicalised."""
    roots = [r for r in (smali_root, contents_root) if os.path.isdir(r)]

    # Pass 1: files mentioning post/userLogin
    for root in roots:
        for f in _iter_files(root):
            if not _is_texty(f):
                continue
            try:
                if os.path.getsize(f) > 4 * 1024 * 1024:
                    continue
            except OSError:
                continue
            txt = _read_text(f)
            if "post/userLogin" not in txt and "post/userlogin" not in txt.lower():
                continue
            m = _HOST_CLASSX_RE.search(txt)
            if m:
                return m.group(1).lower()
            for h in _HOST_URL_RE.findall(txt):
                if "classx.co.in" in h.lower():
                    return h.lower()

    # Pass 2: global *.classx.co.in host
    for root in roots:
        for f in _iter_files(root):
            if not _is_texty(f):
                continue
            try:
                if os.path.getsize(f) > 4 * 1024 * 1024:
                    continue
            except OSError:
                continue
            txt = _read_text(f)
            if "classx.co.in" not in txt:
                continue
            m = _HOST_CLASSX_RE.search(txt)
            if m:
                return m.group(1).lower()

    return None


# ------------------------------------------------------------------ driver
def extract(apk_path: str, out_json_path: str, _unused=None) -> dict:
    """Extract tenant entry from APK/APKS/XAPK/APKM; write result.json.

    Returns dict with keys: _id, key, rsa_key.
    """
    workdir = tempfile.mkdtemp(prefix="apkx_")
    try:
        base = _unpack_apk(apk_path, workdir)

        # 1) baksmali dump
        smali_dir = os.path.join(workdir, "smali")
        os.makedirs(smali_dir, exist_ok=True)
        prefix = _baksmali_prefix()

        r = _run(prefix + ["d", base, "-o", smali_dir], timeout=900)
        if r.returncode != 0:
            out = r.stdout.decode("utf-8", "ignore")[-400:]
            log.warning("baksmali 'd' failed (rc=%s): %s", r.returncode, out)
            r2 = _run(prefix + ["disassemble", base, "-o", smali_dir], timeout=900)
            if r2.returncode != 0:
                raise RuntimeError(
                    "baksmali failed: " +
                    r2.stdout.decode("utf-8", "ignore")[-300:])

        # 2) raw apk contents (for assets/*.pem etc.)
        contents = os.path.join(workdir, "contents")
        os.makedirs(contents, exist_ok=True)
        with zipfile.ZipFile(base) as z:
            for n in z.namelist():
                if n.lower().endswith(".dex"):
                    continue
                try:
                    z.extract(n, contents)
                except Exception:
                    pass

        # 3) find fields
        key = _find_key2(smali_dir)
        pem = _find_pem(contents)
        host = _find_host(smali_dir, contents)

        entry = {
            "_id": host or "",
            "key": key or "",
            "rsa_key": pem or "",
        }

        with open(out_json_path, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2, ensure_ascii=False)

        log.info("extract: _id=%s key=%s pem=%dB",
                 entry["_id"], entry["key"], len(entry["rsa_key"]))
        return entry
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# CLI: python extract.py <apk> [out.json]
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) < 2:
        sys.exit("usage: extract.py <apk> [out.json]")
    out = sys.argv[2] if len(sys.argv) > 2 else "result.json"
    print(json.dumps(extract(sys.argv[1], out), indent=2))
