#!/usr/bin/env python3
"""
APK -> AppX tenant entry extractor.

result.json is EXACTLY the tenants.json entry shape:

  {
    "_id":     "gyanbinduapi.classx.co.in",
    "key":     "qR9mX4T7pK2H8cV1aF6W0yL3JsDNuZ5E",
    "rsa_key": "-----BEGIN PRIVATE KEY-----\n..."
  }

How each field is obtained (deterministic, no guessing):
  * key      baksmali the dex that defines com/appx/core/utils/JavaAESCipher
             -> read the KEY2 string literal from its .field
  * rsa_key  assets/*.pem (content-matched "PRIVATE KEY")
  * _id      baksmali the dex containing the login endpoint; locate the EXACT
             site (class + method) that carries "post/userLogin"; take the
             base-url/host literals reachable from that site; canonicalise to
             <tenant>api.classx.co.in

meta.json (optional, same dir) carries the debug trail:
  { cipher_smali, cipher_dex, login_dex, login_smali, login_class, login_method,
    login_urls, host_variants, host_counts, hosts }
"""
from __future__ import annotations
import json, os, re, shutil, struct, subprocess, sys, tempfile, zipfile

HOST = re.compile(rb"[a-z0-9.-]+\.(?:appx\.co\.in|classx\.co\.in|akamai\.net\.in|appx\.in)")
LOGIN_PATH = "post/userLogin"
LOGIN_HINTS = (LOGIN_PATH, "userLogin")
CIPHER_HINT = "JavaAESCipher"
CLASSX = "classx.co.in"
BAKSMALI = shutil.which("baksmali") or "/usr/bin/baksmali"

def set_baksmali(cmd):
    global BAKSMALI
    BAKSMALI = cmd

URL_RE = re.compile(r'const-string [vp]\d+, "(https?://[^"]*)"')
STR_RE = re.compile(r'const-string [vp]\d+, "([^"]*)"')


def uleb128(b, i):
    r = s = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 0x7F) << s
        if not x & 0x80:
            return r, i
        s += 7


def dex_strings(data: bytes):
    if data[:4] != b"dex\n":
        return []
    size, off = struct.unpack_from("<II", data, 56)
    out = []
    for k in range(size):
        p = struct.unpack_from("<I", data, off + 4 * k)[0]
        _, q = uleb128(data, p)
        e = data.find(b"\x00", q)
        out.append(data[q:e].decode("utf-8", "replace"))
    return out


def baksmali(dex_bytes: bytes, workdir: str, tag: str):
    src = os.path.join(workdir, f"{tag}.dex")
    out = os.path.join(workdir, f"smali_{tag}")
    open(src, "wb").write(dex_bytes)
    
    # Check if BAKSMALI is a list (like our Java command) or a string
    if isinstance(BAKSMALI, list):
        cmd = BAKSMALI + ["d", src, "-o", out]
    else:
        cmd = [BAKSMALI, "d", src, "-o", out]
        
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
    return out if os.path.isdir(out) else None


def read_cipher_key(smali_root: str):
    for root, _, files in os.walk(smali_root):
        for fn in files:
            if fn.endswith(".smali") and CIPHER_HINT in fn:
                txt = open(os.path.join(root, fn), encoding="utf-8", errors="replace").read()
                m = re.search(r'^\.field\s+.*?\sKEY2\s*:\s*Ljava/lang/String;\s*=\s*"([^"]*)"', txt, re.M)
                if not m:
                    m = re.search(r'^\.field\s+.*?\sKEY\s*:\s*Ljava/lang/String;\s*=\s*"([^"]*)"', txt, re.M)
                if m:
                    return m.group(1), os.path.relpath(os.path.join(root, fn), smali_root)
    return None, None


def find_login_site(smali_root: str):
    """The exact place: the .method whose body holds "post/userLogin" (prefer),
    else the class that references userLogin. Returns dict or None."""
    fallback = None
    for root, _, files in os.walk(smali_root):
        for fn in files:
            if not fn.endswith(".smali"):
                continue
            p = os.path.join(root, fn)
            txt = open(p, encoding="utf-8", errors="replace").read()
            if not any(h in txt for h in LOGIN_HINTS):
                continue
            cls = re.search(r"^\.class\s+.*?(L[^;]+;)", txt, re.M)
            cls = cls.group(1) if cls else fn[:-6]
            rel = os.path.relpath(p, smali_root)
            # walk members; find the exact .method OR .field whose body holds the login literal
            cur_m = cur_f = None
            for line in txt.splitlines():
                if line.startswith(".method"):
                    cur_m = line[len(".method"):].strip(); cur_f = None
                elif line.startswith(".field"):
                    cur_f = line[len(".field"):].strip(); cur_m = None
                if f'"{LOGIN_PATH}"' in line or LOGIN_PATH in line:
                    urls = URL_RE.findall(txt)
                    site = {"login_smali": rel, "login_class": cls,
                            "login_method": cur_m, "login_field": cur_f,
                            "login_line": line.strip(), "login_urls": sorted(set(urls))}
                    return site                            # exact site
                if "userLogin" in line and fallback is None:
                    urls = URL_RE.findall(txt)
                    fallback = {"login_smali": rel, "login_class": cls,
                                "login_method": cur_m, "login_field": cur_f,
                                "login_line": line.strip(), "login_urls": sorted(set(urls))}
    return fallback


def hosts_near(smali_root: str, login_smali: str, login_urls):
    """Hosts from the login class, then its package, then the whole tree."""
    found = set()
    for u in login_urls:
        m = HOST.search(u.encode())
        if m:
            found.add(m.group().decode())
    if found:
        return sorted(found)
    for base in (os.path.join(smali_root, os.path.dirname(login_smali)), smali_root):
        for root, _, files in os.walk(base):
            for fn in files:
                if fn.endswith(".smali"):
                    t = open(os.path.join(root, fn), encoding="utf-8", errors="replace").read()
                    for u in URL_RE.findall(t):
                        m = HOST.search(u.encode())
                        if m:
                            found.add(m.group().decode())
        if found:
            break
    return sorted(found)


def tenant_label(hosts, host_count):
    """Pick the tenant's own api host (usage-ranked), return its label without 'api'."""
    api = [h for h in hosts if re.search(r"api\.(appx|classx|akamai)", h)]
    api = sorted(api, key=lambda h: (-host_count.get(h, 0), h)) or sorted(hosts)
    if not api:
        return None, []
    lbl = api[0].split(".")[0]
    if lbl.endswith("api"):
        lbl = lbl[:-3]
    variants = [h for h in api if h.split(".")[0] == lbl + "api"]
    return lbl, variants


def extract(apk_path: str, out_json: str = "result.json", meta_json: str | None = "meta.json") -> dict:
    z = zipfile.ZipFile(apk_path)
    names = z.namelist()

    # all PEMs (content-matched), then pick the one the app actually uses
    pems = []
    for n in names:
        if n.lower().endswith(".pem"):
            d = z.read(n)
            if b"PRIVATE KEY" in d:
                pems.append((n.split("/")[-1], d.decode("utf-8", "replace")))
    pem_file = pem_text = None
    if pems:
        dex_blob = b"".join(z.read(n) for n in names if re.fullmatch(r"classes\d*\.dex", n))
        # 1) a pem the dex names explicitly
        named = [p for p, _ in pems if p.encode() in dex_blob]
        # 2) empirically: when the app ships two, the working (uhs5) key is asdfghjkl.pem
        pref = sorted(pems, key=lambda pt: (0 if pt[0] in named else 1,
                                            0 if pt[0] == "asdfghjkl.pem" else 1,
                                            pt[0]))
        pem_file, pem_text = pref[0]
        # if a single pem ships, always take it
        if len(pems) == 1:
            pem_file, pem_text = pems[0]

    dexes = [n for n in names if re.fullmatch(r"classes\d*\.dex", n)]
    cache, hosts, host_count = {}, set(), {}
    cipher_dex = login_dex = exact_login_dex = None
    for n in dexes:
        data = z.read(n)
        cache[n] = data
        for m in HOST.finditer(data):
            h = m.group().decode(); hosts.add(h); host_count[h] = host_count.get(h, 0) + 1
        if cipher_dex is None and CIPHER_HINT.encode() in data:
            cipher_dex = n
        if exact_login_dex is None and LOGIN_PATH.encode() in data:
            exact_login_dex = n
        if login_dex is None and any(h.encode() in data for h in LOGIN_HINTS):
            login_dex = n
    login_dex = exact_login_dex or login_dex

    wd = tempfile.mkdtemp(prefix="apkx_")
    key = key2 = cipher_smali = None
    site, near = None, []
    try:
        if cipher_dex:
            root = baksmali(cache[cipher_dex], wd, "cipher")
            if root:
                key2, cipher_smali = read_cipher_key(root)
        if login_dex:
            lroot = os.path.join(wd, "smali_cipher" if login_dex == cipher_dex else "smali_login")
            if login_dex != cipher_dex:
                lroot = baksmali(cache[login_dex], wd, "login")
            if lroot and os.path.isdir(lroot):
                site = find_login_site(lroot)
                near = hosts_near(lroot, site["login_smali"], site["login_urls"]) if site else []
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    lbl, variants = tenant_label(sorted(hosts) if not near else near, host_count)
    # full variant census for the tenant label (from every dex)
    if lbl:
        variants = sorted([h for h in hosts if h.split(".")[0] == lbl + "api"] or variants,
                          key=lambda h: -host_count.get(h, 0))
    _id = f"{lbl}api.{CLASSX}" if lbl else None
    entry = {"_id": _id, "key": key2, "rsa_key": pem_text}
    json.dump(entry, open(out_json, "w"), indent=2)
    if meta_json:
        json.dump({
            "_id": _id, "tenant": lbl, "tenant_hosts": variants, "pem_file": pem_file,
            "pems": [{"file": p, "md5": __import__("hashlib").md5(t.encode()).hexdigest()[:10],
                      "primary": p == pem_file} for p, t in pems],
            "cipher_dex": cipher_dex, "cipher_smali": cipher_smali,
            "login_dex": login_dex, **(site or {}),
            "hosts": sorted(hosts, key=lambda h: -host_count.get(h, 0)),
            "host_counts": {h: host_count[h] for h in sorted(hosts, key=lambda h: -host_count.get(h, 0))[:12]},
        }, open(meta_json, "w"), indent=2)
    z.close()
    return entry


if __name__ == "__main__":
    apk = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: extract.py <base.apk> [out.json]")
    out = sys.argv[2] if len(sys.argv) > 2 else "result.json"
    e = extract(apk, out)
    print(json.dumps({**e, "rsa_key": (e["rsa_key"] or "")[:40] + "…"}, indent=2))
    print("\nwrote", out, "+ meta.json")
