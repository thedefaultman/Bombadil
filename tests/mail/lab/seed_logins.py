#!/usr/bin/env python3
"""Write passwords into a Thunderbird profile's login manager WITHOUT starting Thunderbird.

Thunderbird keeps IMAP/SMTP passwords (and OAuth2 refresh tokens) in
<profile>/logins.json, each value encrypted with the profile's NSS "secret
decoder ring" key in <profile>/key4.db. There is no pref for a password, so an
account that must sync without a prompt needs an entry here. This does what
Thunderbird itself does (nsSecretDecoderRing::EncryptString, empty primary
password) through NSS via ctypes:

    NSS_Initialize("sql:<profile>") ->  PK11_InitPin(slot, "", "") if the DB is new
    PK11SDR_Encrypt(keyid="" (default key, created on first use), plaintext)
    base64(blob) -> logins.json  (encType 1)

Usage:
    seed_logins.py PROFILE_DIR [--nss-dir DIR] ORIGIN REALM USER PASSWORD [ORIGIN REALM USER PASSWORD ...]
    e.g. seed_logins.py prof imap://127.0.0.1 imap://127.0.0.1 test@example.test lab \\
                             smtp://127.0.0.1 smtp://127.0.0.1 test@example.test lab

Login-manager keys Thunderbird uses (hostname == origin, httpRealm == realm):
    IMAP  imap://<host>     realm imap://<host>
    SMTP  smtp://<host>     realm smtp://<host>
    OAuth2 access/refresh   oauth://<issuer>  realm <scopes>  (username = account user)
"""
import base64
import ctypes
import json
import os
import sys
import time
import uuid


class SECItem(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("data", ctypes.POINTER(ctypes.c_ubyte)), ("len", ctypes.c_uint)]


def load_nss(nss_dir):
    def first(name):
        for d in filter(None, [nss_dir, "/usr/lib", "/usr/lib/x86_64-linux-gnu", "/usr/lib64"]):
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
        return name

    for dep in ("libnspr4.so", "libplc4.so", "libplds4.so", "libnssutil3.so"):
        ctypes.CDLL(first(dep), mode=ctypes.RTLD_GLOBAL)
    nss = ctypes.CDLL(first("libnss3.so"), mode=ctypes.RTLD_GLOBAL)
    nss.NSS_Initialize.argtypes = [ctypes.c_char_p] * 4 + [ctypes.c_uint32]
    nss.PK11_GetInternalKeySlot.restype = ctypes.c_void_p
    nss.PK11_NeedUserInit.argtypes = [ctypes.c_void_p]
    nss.PK11_InitPin.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    nss.PK11SDR_Encrypt.argtypes = [ctypes.POINTER(SECItem), ctypes.POINTER(SECItem), ctypes.POINTER(SECItem), ctypes.c_void_p]
    nss.SECITEM_FreeItem.argtypes = [ctypes.POINTER(SECItem), ctypes.c_int]
    return nss


def encrypt_all(profile, nss_dir, strings):
    nss = load_nss(nss_dir)
    if nss.NSS_Initialize(("sql:" + profile).encode(), b"", b"", b"secmod.db", 0) != 0:
        raise RuntimeError("NSS_Init failed for %s" % profile)
    slot = nss.PK11_GetInternalKeySlot()
    if nss.PK11_NeedUserInit(slot):
        nss.PK11_InitPin(slot, b"", b"")
    out = []
    for s in strings:
        raw = s.encode()
        buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        req = SECItem(0, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)), len(raw))
        keyid = SECItem(0, None, 0)
        rep = SECItem(0, None, 0)
        if nss.PK11SDR_Encrypt(ctypes.byref(keyid), ctypes.byref(req), ctypes.byref(rep), None) != 0:
            raise RuntimeError("PK11SDR_Encrypt failed")
        out.append(base64.b64encode(ctypes.string_at(rep.data, rep.len)).decode())
        nss.SECITEM_FreeItem(ctypes.byref(rep), 0)
    nss.NSS_Shutdown()
    return out


def encrypt_in_subprocess(profile, nss_dir, strings):
    """libsoftokn3 needs libmozsqlite3/libfreeblpriv3 from the Thunderbird dir: they are found through
    LD_LIBRARY_PATH, which is only read at process start, so do the NSS work in a child process."""
    import subprocess
    env = dict(os.environ)
    if nss_dir:
        env["LD_LIBRARY_PATH"] = nss_dir + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--worker", profile, nss_dir or ""],
                       input=json.dumps(strings).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if r.returncode:
        raise RuntimeError("NSS worker failed: " + r.stderr.decode())
    return json.loads(r.stdout)


def seed(profile, logins, nss_dir=None):
    """logins: [(origin, realm, username, password)]"""
    os.makedirs(profile, exist_ok=True)
    flat = []
    for _o, _r, u, p in logins:
        flat += [u, p]
    enc = encrypt_in_subprocess(profile, nss_dir, flat)
    path = os.path.join(profile, "logins.json")
    data = {"nextId": 1, "logins": [], "potentiallyVulnerablePasswords": [],
            "dismissedBreachAlertsByLoginGUID": {}, "version": 3}
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
    now = int(time.time() * 1000)
    for i, (origin, realm, _u, _p) in enumerate(logins):
        data["logins"].append({
            "id": data["nextId"],
            "hostname": origin,
            "httpRealm": realm,
            "formSubmitURL": None,
            "usernameField": "",
            "passwordField": "",
            "encryptedUsername": enc[2 * i],
            "encryptedPassword": enc[2 * i + 1],
            "guid": "{%s}" % uuid.uuid4(),
            "encType": 1,
            "timeCreated": now,
            "timeLastUsed": now,
            "timePasswordChanged": now,
            "timesUsed": 1,
            "syncCounter": 0,
            "everSynced": False,
            "encryptedUnknownFields": None,
        })
        data["nextId"] += 1
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def main(argv):
    if len(argv) > 1 and argv[1] == "--worker":
        print(json.dumps(encrypt_all(argv[2], argv[3] or None, json.load(sys.stdin))))
        return
    nss_dir = None
    if "--nss-dir" in argv:
        i = argv.index("--nss-dir")
        nss_dir = argv[i + 1]
        del argv[i:i + 2]
    profile, rest = argv[1], argv[2:]
    if not rest or len(rest) % 4:
        sys.exit(__doc__)
    logins = [tuple(rest[i:i + 4]) for i in range(0, len(rest), 4)]
    print(seed(profile, logins, nss_dir))


if __name__ == "__main__":
    main(sys.argv)
