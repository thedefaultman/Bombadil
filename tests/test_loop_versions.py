"""The versions of what Bombadil runs on: asked once, bounded, never raising, "unknown" when absent.

The programs are stand-ins written as small Python scripts on a PATH of their own, so nothing here
depends on what the machine has installed.
"""

import os
import stat
import sys
import time

import pytest

from bombadil.loop import versions


@pytest.fixture(autouse=True)
def fresh(home, monkeypatch):
    versions.reset()
    monkeypatch.delenv("BOMBADIL_MACHINE", raising=False)
    yield
    versions.reset()


@pytest.fixture
def bindir(tmp_path, monkeypatch):
    """A PATH with only the stand-ins in it."""
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d))
    return d


def program(bindir, name, body, log=None):
    """A stand-in program: Python `body`, and a line in `log` every time it is run."""
    path = bindir / name
    note = f"open({str(log)!r}, 'a').write('x')\n" if log else ""
    path.write_text(f"#!{sys.executable}\nimport sys, time\n{note}{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def installed(bindir, log=None):
    program(bindir, "hyprctl", "print('{\"branch\": \"main\", \"version\": \"0.56.2\", \"tag\": \"v0.56.2\"}')", log)
    program(bindir, "quickshell", "print('quickshell 0.3.1, revision abc123, distributed by: Arch Linux')", log)
    program(bindir, "claude", "print('2.1.283 (Claude Code)')", log)
    program(bindir, "codex", "print('codex-cli 0.157.1')", log)


# -- each program --

def test_versions_are_read_from_what_each_program_says(bindir):
    installed(bindir)
    assert versions.hyprland() == "0.56.2"
    assert versions.quickshell() == "0.3.1"
    assert versions.claude_code() == "2.1.283"
    assert versions.codex() == "0.157.1"


def test_hyprland_falls_back_to_the_binary_when_there_is_no_hyprctl(bindir):
    program(bindir, "Hyprland", "print('Hyprland 0.56.2 built from branch main at commit 1a2b3c (version text)')")
    assert versions.hyprland() == "0.56.2"


def test_hyprctl_that_answers_in_prose_falls_back_too(bindir):
    program(bindir, "hyprctl", "print('not json at all')")
    program(bindir, "Hyprland", "print('Hyprland v0.55.0 built from branch main')")
    assert versions.hyprland() == "0.55.0"


def test_a_program_that_is_not_there_is_unknown(bindir):
    assert [versions.hyprland(), versions.quickshell(), versions.claude_code(), versions.codex()] == ["unknown"] * 4


def test_a_program_that_prints_nothing_useful_is_unknown(bindir):
    program(bindir, "claude", "print('usage: claude [options]')")
    program(bindir, "codex", "sys.exit(3)")
    program(bindir, "quickshell", "print('quickshell: no display')", None)
    assert versions.claude_code() == "unknown"
    assert versions.codex() == "unknown"     # a failing program's output is not a version
    assert versions.quickshell() == "unknown"


def test_a_program_that_hangs_is_killed_and_unknown(bindir, monkeypatch):
    marker = bindir.parent / "started"
    program(bindir, "claude", f"open({str(marker)!r}, 'w').write('x'); time.sleep(60)")
    monkeypatch.setattr(versions, "TIMEOUT", 0.4)
    t0 = time.monotonic()
    assert versions.claude_code() == "unknown"
    assert time.monotonic() - t0 < 5
    assert marker.exists()


def test_capture_gives_up_on_a_program_whose_child_holds_the_pipes(bindir):
    program(bindir, "noisy", "import subprocess; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "time.sleep(30)")
    t0 = time.monotonic()
    assert versions.capture([str(bindir / "noisy")], timeout=0.4) is None
    assert time.monotonic() - t0 < 5


def test_capture_never_raises(bindir):
    assert versions.capture(["/no/such/program"]) is None
    assert versions.capture([""]) is None
    done = versions.capture([str(program(bindir, "echo2", "print(sys.stdin.read().upper())"))], input="hi")
    assert done is not None and done.out.strip() == "HI" and done.code == 0


# -- asked once --

def test_each_program_is_asked_once_per_process(bindir, tmp_path):
    log = tmp_path / "runs"
    installed(bindir, log)
    first = versions.collect()
    second = versions.collect()
    assert first == second
    assert log.read_text() == "xxxx"     # hyprctl, quickshell, claude, codex: once each


def test_what_was_missing_is_asked_again_after_a_while(bindir, monkeypatch):
    assert versions.codex() == "unknown"
    program(bindir, "codex", "print('codex-cli 0.157.1')")
    assert versions.codex() == "unknown"           # remembered
    monkeypatch.setattr(versions, "RETRY_UNKNOWN", 0.0)
    assert versions.codex() == "0.157.1"


def test_collect_refresh_asks_again(bindir, tmp_path):
    log = tmp_path / "runs"
    installed(bindir, log)
    versions.collect()
    versions.collect(refresh=True)
    assert log.read_text() == "x" * 8


def test_collect_gives_every_key_and_takes_one_timeout_at_worst(bindir, monkeypatch):
    for name in ("hyprctl", "quickshell", "claude", "codex"):
        program(bindir, name, "time.sleep(60)")
    monkeypatch.setattr(versions, "TIMEOUT", 0.5)
    t0 = time.monotonic()
    got = versions.collect()
    assert time.monotonic() - t0 < 3            # side by side, not one after the other
    assert set(got) == {"build", "machine", "Hyprland", "Quickshell", "Claude Code", "codex-cli", "kit"}
    assert [got[k] for k in ("Hyprland", "Quickshell", "Claude Code", "codex-cli")] == ["unknown"] * 4


# -- the build --

def test_the_build_is_the_version_stamp_beside_the_installed_share(tmp_path, monkeypatch):
    share = tmp_path / "share"
    share.mkdir()
    (share / "VERSION").write_text("d9dde3b\nbuilt on the cloud\n")
    monkeypatch.setenv("BOMBADIL_SHARE", str(share))
    assert versions.build() == "d9dde3b"


def test_without_the_loop_signals_the_build_is_still_read(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "bombadil.loop.signals", None)   # "import signals" fails
    share = tmp_path / "usr-share-bombadil"
    share.mkdir()
    (share / "VERSION").write_text("  abc1234  \n")
    monkeypatch.setenv("BOMBADIL_SHARE", str(share))
    assert versions.build() == "abc1234"


def test_a_build_with_no_stamp_is_a_git_hash_or_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path / "nothing-here"))
    got = versions.build()
    assert got == "" or (4 <= len(got) <= 40 and all(c in "0123456789abcdef" for c in got))


# -- the kit, the machine --

def test_the_kit_is_its_newest_version_its_parts_and_a_hash_of_its_list(tmp_path, monkeypatch):
    qml = tmp_path / "share" / "share" / "qml" / "Bombadil"
    qml.mkdir(parents=True)
    (qml / "qmldir").write_text("module Bombadil\n# the kit\nsingleton Theme 1.0 Theme.qml\n"
                                "AppWindow 1.2 AppWindow.qml\nPanel 1.10 Panel.qml\n")
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path / "share"))
    got = versions.kit()
    assert got.startswith("1.10 (3 parts, ") and got.endswith(")")
    (qml / "qmldir").write_text("module Bombadil\nsingleton Theme 1.0 Theme.qml\n")
    versions.reset()
    assert versions.kit().startswith("1.0 (1 parts, ")


def test_the_kit_in_the_checkout_is_found_when_nothing_is_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path / "nothing"))
    assert versions.kit() != "unknown"      # share/qml/Bombadil/qmldir is in this repository


def test_the_machine_is_his_word_for_it_when_he_gave_one(monkeypatch):
    monkeypatch.setenv("BOMBADIL_MACHINE", "laptop VM; rm -rf /")
    assert versions.machine() == "laptop VM rm -rf"      # only plain words


def test_the_machine_is_a_vm_when_systemd_sees_one(bindir):
    program(bindir, "systemd-detect-virt", "print('kvm')")
    assert versions.machine() == "VM"
    versions.reset()
    program(bindir, "systemd-detect-virt", "print('docker')")
    assert versions.machine() == "container"


def test_no_machine_is_said_when_nothing_says(bindir, monkeypatch):
    program(bindir, "systemd-detect-virt", "print('none'); sys.exit(1)")
    got = versions.machine()
    assert got in ("", "laptop", "desktop")      # the chassis of whatever runs this test


def test_the_version_functions_survive_an_environment_with_no_path(monkeypatch):
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.setattr(os, "defpath", "")
    assert versions.claude_code() == "unknown"
