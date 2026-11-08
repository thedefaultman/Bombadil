import subprocess

from bombadil import snapshots


def _snaps(tmp_path, monkeypatch, ran):
    (tmp_path / "root").write_text("")
    monkeypatch.setattr(snapshots.shutil, "which", lambda name: "/usr/bin/snapper")

    def runner(cmd, **kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="7\n", stderr="")
    return snapshots.Snapshots(runner=runner, configs_dir=tmp_path)


def test_a_turns_restore_point_is_one_snapper_may_prune(tmp_path, monkeypatch):
    # Snapshots made without a cleanup algorithm are never deleted, so every turn added one for good.
    ran = []
    snap = _snaps(tmp_path, monkeypatch, ran).create("turn:3: install htop")
    assert snap == snapshots.Snapshot(7, "turn:3: install htop")
    cmd = ran[0]
    assert cmd[cmd.index("--cleanup-algorithm") + 1] == "number"
    assert cmd[cmd.index("create"):][:2] == ["create", "--print-number"]
    assert cmd[-2:] == ["--description", "turn:3: install htop"]
