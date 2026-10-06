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


PEM_PREFERRED   = "asdfghjkl.pem"    # empirically the uhs5 key when an app ships TWO pems
PEM_DECOY_HINT  = "qxzvpyrfk.pem"    # the usual decoy in a two-pem app


def pick_pem(pems, dex_blob):
    """Rank the app's PEM assets and choose one. Returns (chosen, ranked, warn).

    Evidence (5/5 tenants): when an app ships two assets, the key that unwraps the uhs5
    `hls-clear.key` is `assets/asdfghjkl.pem`; the other (`assets/qxzvpyrfk.pem`) is a decoy.
    The app sometimes names EITHER file in the dex, so dex-naming must NOT outrank the
    filename heuristic -- that was the bug that made targetupscapi ship the decoy.
    """
    named = {p for p, _ in pems if p.encode() in dex_blob}

    def rank(pt):
        n = pt[0]
        return (0 if n == PEM_PREFERRED else 1,     # 1) the known-good uhs5 name
                1 if n == PEM_DECOY_HINT else 0,    # 2) push the known decoy down
                0 if n in named else 1,             # 3) then a dex-referenced asset
                n)                                  # 4) then deterministic

    ranked = sorted(pems, key=rank)
    warn = None
    if len(pems) > 1 and ranked[0][0] != PEM_PREFERRED:
        warn = (f"{len(pems)} PEMs found and none is {PEM_PREFERRED}: picked {ranked[0][0]!r}. "
                f"VERIFY against a live key with which_pem.py before shipping.")
    return ranked[0], ranked, warn


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
    pem_file = pem_text = pem_warn = None
    pems_ranked = []
    if pems:
        dex_blob = b"".join(z.read(n) for n in names if re.fullmatch(r"classes\d*\.dex", n))
        (pem_file, pem_text), pems_ranked, pem_warn = pick_pem(pems, dex_blob)
        if pem_warn:
            print(f"[!] PEM: {pem_warn}", file=sys.stderr)

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
    entry = {"_id": _id, "key": key2}
    # every PEM the app ships, in rank order: rsa_key, rsa_key2, rsa_key3, ...
    for i, (n, t) in enumerate(pems_ranked or ([pem_file, pem_text] if pem_text else []), start=1):
        entry["rsa_key" if i == 1 else f"rsa_key{i}"] = t
    if pems_ranked:
        entry["pems"] = [n for n, _ in pems_ranked]
    json.dump(entry, open(out_json, "w"), indent=2)
    if meta_json:
        json.dump({
            "_id": _id, "tenant": lbl, "tenant_hosts": variants, "pem_file": pem_file,
            "pem_warn": pem_warn, "pems_ranked": [p for p, _ in pems_ranked],
            "pems": [{"file": p, "md5": __import__("hashlib").md5(t.encode()).hexdigest()[:10],
                      "primary": p == pem_file,
                      "rank": ([x[0] for x in pems_ranked].index(p) if pems_ranked else None)}
                     for p, t in pems],
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
  
