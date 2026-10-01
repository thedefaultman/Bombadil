"""The Thunderbird add-on's own tests (tests/mail/addon/*.test.js), run with Node's test runner.

The add-on (share/mail/extension) is plain JavaScript with no build step and no packages, so its tests need
nothing but `node`: they run the real modules against a fake `messenger` and a fake clock (tests/mail/addon/
fake.js), which behave as the lab saw the real Thunderbird behave, and no Thunderbird, network or mail account
is involved. The same files are checked as text for what they are allowed to ask for (static.test.js).

Skipped when there is no Node of version 22 or later (the tests load the add-on's ES modules, which carry no
package.json, with `--experimental-default-type=module`). One pytest case runs each file, so a failure names
the file and shows Node's own report of it.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ADDON = Path(__file__).resolve().parent / "mail" / "addon"
FILES = sorted(p.name for p in ADDON.glob("*.test.js"))
NODE = shutil.which("node")
TIMEOUT = 120


def node_major() -> int:
    try:
        out = subprocess.run([NODE, "--version"], capture_output=True, text=True, timeout=20, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    found = re.match(r"v(\d+)\.", out.strip())
    return int(found.group(1)) if found else 0


pytestmark = pytest.mark.skipif(NODE is None or node_major() < 22, reason="needs Node 22 or later")


def run_node(*files: str) -> subprocess.CompletedProcess:
    # Node's own variables are left out so that a developer's NODE_OPTIONS cannot change what is being run.
    env = {k: v for k, v in os.environ.items() if not k.startswith("NODE_")}
    env["NO_COLOR"] = "1"
    command = [NODE, "--experimental-default-type=module", "--test", "--test-reporter=spec"]
    command += [str(ADDON / f) for f in files]
    return subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT, env=env, cwd=ADDON.parents[2],
                          check=False)


def test_there_are_tests_for_every_part_of_the_add_on():
    expected = {
        "changing", "engine", "events", "files", "finding", "gate", "hostile", "link", "newest", "reading",
        "sending", "static", "streams",
    }
    assert expected <= {name.split(".")[0] for name in FILES}


@pytest.mark.parametrize("name", FILES)
def test_addon_js(name):
    done = run_node(name)
    report = done.stdout + done.stderr
    assert done.returncode == 0, report[-6000:]
    assert re.search(r"^ℹ fail 0$", report, re.MULTILINE), report[-3000:]
    passed = re.search(r"^ℹ pass (\d+)$", report, re.MULTILINE)
    assert passed and int(passed.group(1)) > 0, "a test file that ran nothing: " + report[-1000:]
