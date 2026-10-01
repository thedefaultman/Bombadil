#!/usr/bin/env python3
"""Q1: which install paths load an UNSIGNED MailExtension in release Thunderbird, with no clicks?

Runs tests/mail/lab/run-thunderbird.sh once per variant (fresh profile each time, unless the variant is a
two-step "existing profile" one), waits for the add-on's native-messaging hello (the add-on calls
messenger.accounts.list() and the host writes the result to hello.json), and records:

    loaded          hello.json appeared (the add-on really ran and talked to a native host)
    addon state     extensions.json entry: location, signedState (0 = missing signature), active, userDisabled, appDisabled
    log             the tb.log lines that mention signing/disabling

Usage: q1_signing_matrix.py [--only NAME ...] [--json OUT.json]      (TB_VERSION=... selects the Thunderbird build)
Needs root only for the policy-etc variant (writes /etc/thunderbird/policies/policies.json, removed afterwards).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = os.path.join(HERE, "run-thunderbird.sh")
LAB = os.environ.get("BOMBADIL_LAB_DIR", "/tmp/bombadil-lab")
EXT_ID = "bombadil-mail-lab@bombadil.lab"

CFG_LOCK = "// AutoConfig file: the first line must be a comment\nlockPref(\"xpinstall.signatures.required\", false);\n"
POLICY_PREF = {"policies": {
    "Preferences": {"xpinstall.signatures.required": {"Value": False, "Status": "locked"}},
    "ExtensionSettings": {EXT_ID: {"installation_mode": "force_installed", "install_url": "file://@XPI@"}}}}
POLICY_PREF_ONLY = {"policies": {"Preferences": {"xpinstall.signatures.required": {"Value": False, "Status": "locked"}}}}
POLICY_EXT_ONLY = {"policies": {"ExtensionSettings": {EXT_ID: {"installation_mode": "force_installed", "install_url": "file://@XPI@"}}}}


def variants(xpi):
    tmp = tempfile.mkdtemp(prefix="q1-")
    files = {}
    for name, doc in (("pol_pref", POLICY_PREF), ("pol_pref_only", POLICY_PREF_ONLY), ("pol_ext", POLICY_EXT_ONLY)):
        p = os.path.join(tmp, name + ".json")
        with open(p, "w") as f:
            f.write(json.dumps(doc).replace("@XPI@", xpi))
        files[name] = p
    cfg = os.path.join(tmp, "lock.cfg")
    with open(cfg, "w") as f:
        f.write(CFG_LOCK)
    return [
        # name, env, description
        ("b1 profile extensions/<id>.xpi + autoDisableScopes=0", {"LAB_LOAD": "profile-xpi"}, "(b)"),
        ("b2 profile extensions/<id>/ unpacked dir + autoDisableScopes=0", {"LAB_LOAD": "profile-dir"}, "(b)"),
        ("b3 profile xpi, NO prefs (defaults: autoDisableScopes=15)", {"LAB_LOAD": "profile-xpi", "LAB_ADDON_PREFS": "0"}, "(b)"),
        ("a1 policy <app>/distribution/policies.json force_installed file://", {"LAB_LOAD": "policy-dist"}, "(a)"),
        ("a2 policy /etc/thunderbird/policies/policies.json force_installed file://", {"LAB_LOAD": "policy-etc"}, "(a)"),
        ("c1 <app>/distribution/extensions/<id>.xpi, no prefs", {"LAB_LOAD": "distribution-ext"}, "(c)"),
        ("c2 <app>/distribution/extensions/<id>.xpi + autoDisableScopes=0", {"LAB_LOAD": "distribution-ext", "LAB_ADDON_PREFS": "1"}, "(c)"),
        ("e1 <app>/extensions/<id>.xpi, no prefs", {"LAB_LOAD": "app-ext"}, "(e)"),
        ("e2 <app>/features/<id>.xpi (system add-on dir), no prefs", {"LAB_LOAD": "features"}, "(e)"),
        ("d1 profile xpi with xpinstall.signatures.required=true in user.js (control: must FAIL)",
         {"LAB_LOAD": "profile-xpi", "LAB_SIGNATURES_REQUIRED": "1"}, "(d) control"),
        ("d2 same as d1 + policy Preferences locking xpinstall.signatures.required=false",
         {"LAB_LOAD": "profile-xpi", "LAB_SIGNATURES_REQUIRED": "1", "LAB_POLICY_JSON": files["pol_pref_only"]}, "(d)"),
        ("d3 same as d1 + AutoConfig lockPref(xpinstall.signatures.required,false)",
         {"LAB_LOAD": "profile-xpi", "LAB_SIGNATURES_REQUIRED": "1", "LAB_AUTOCONFIG": "1", "LAB_AUTOCONFIG_CFG": cfg}, "(d)"),
        ("d4 policy force_installed with xpinstall.signatures.required=true in user.js (control)",
         {"LAB_LOAD": "policy-dist", "LAB_SIGNATURES_REQUIRED": "1"}, "(a)+(d) control"),
    ]


def run(env_extra, wait):
    env = dict(os.environ)
    env.update(env_extra)
    env["LAB_WAIT"] = str(wait)
    p = subprocess.run([RUN, "start"], env=env, capture_output=True, text=True, timeout=wait + 60)
    return p.returncode, p.stderr


def addon_state():
    try:
        d = json.load(open(os.path.join(LAB, "profile", "extensions.json")))
    except Exception as e:  # noqa
        return {"error": str(e)}
    for a in d["addons"]:
        if a["id"] == EXT_ID:
            return {k: a.get(k) for k in ("location", "signedState", "active", "userDisabled", "appDisabled", "version")}
    return None


def log_lines():
    try:
        txt = open(os.path.join(LAB, "tb.log"), errors="replace").read()
    except OSError:
        return []
    pat = re.compile(r"signed|signature|not allowed|disabled|Invalid XPI|Install|blocked|AddonManager|XPIProvider|policies|Enterprise", re.I)
    return [l.strip()[:220] for l in txt.splitlines() if pat.search(l)][:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--json")
    ap.add_argument("--wait", type=int, default=25)
    a = ap.parse_args()
    xpi = subprocess.check_output([sys.executable, os.path.join(HERE, "build_xpi.py")], text=True).strip()
    results = []
    for name, env, tag in variants(xpi):
        if a.only and not any(o in name for o in a.only):
            continue
        if "policy-etc" in env.get("LAB_LOAD", "") and os.geteuid() != 0:
            results.append({"variant": name, "tag": tag, "skipped": "needs root"})
            continue
        rc, err = run(env, a.wait)
        loaded = rc == 0
        r = {"variant": name, "tag": tag, "loaded": loaded, "addon": addon_state(), "log": log_lines()}
        results.append(r)
        print("%-95s %s  %s" % (name, "LOADED" if loaded else "not loaded", r["addon"]), flush=True)
        subprocess.run([RUN, "stop"], capture_output=True)
        if env.get("LAB_LOAD") == "policy-etc":
            subprocess.run([RUN, "clean"], capture_output=True)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(results, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
