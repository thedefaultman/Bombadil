#!/usr/bin/env python3
"""Print every pref the lab writes into a profile, as markdown tables (pref, value, why).

    python3 print_prefs.py [--kind lab|gmail|outlook|icloud] [--email ADDR]

make_profile.py is the single source (the reasons live next to the values); this just renders it, so a findings
document can include an always-correct appendix:  python3 print_prefs.py >> FINDINGS.md
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_profile as m  # noqa: E402


def row(pref, value, why):
    if isinstance(value, bool):
        value = str(value).lower()
    return "| `%s` | `%s` | %s |" % (pref, value, str(why).replace("|", "/"))


def table(title, rows):
    print("\n### %s\n" % title)
    print("| pref | value | why |\n|---|---|---|")
    for r in rows:
        print(row(*r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="lab", choices=["lab", "gmail", "outlook", "icloud"])
    ap.add_argument("--email", default="test@example.test")
    a = ap.parse_args()
    table("Quiet prefs (no first-run windows, no network chatter, no update/telemetry); LAB_NO_QUIET=1 omits them",
          m.PREFS_QUIET + [("mail.shell.checkDefaultCalendar", False, "same as checkDefaultClient, for the calendar")])
    table("Add-on loading prefs (profile-xpi / profile-dir only)", m.PREFS_ADDON)
    table("Debug logging (stdout/tb.log)", m.PREFS_DEBUG)
    t = m.account_template(a.kind, a.email, 1143, 1025)
    table("Account, identity, SMTP and Local Folders prefs (%s account for %s)" % (a.kind, a.email),
          m.account_prefs(a.email, "Lab Tester", t))


if __name__ == "__main__":
    main()
