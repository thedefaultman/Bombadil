import os
import struct
import subprocess
import time

import pytest

from bombadil.brain import forks


def msg(what: int, data: bytes, idx: int = forks.CN_IDX_PROC, ntype: int = forks.NLMSG_DONE) -> bytes:
    ev = struct.pack("=IIQ", what, 0, 123456789) + data
    cn = struct.pack("=IIIIHH", idx, forks.CN_VAL_PROC, 0, 0, len(ev), 0) + ev
    body = struct.pack("=IHHII", 16 + len(cn), ntype, 0, 0, 0) + cn
    return body + b"\0" * (-len(body) % 4)


def fork(ppid, ptgid, cpid, ctgid) -> bytes:
    return msg(forks.PROC_EVENT_FORK, struct.pack("=iiii", ppid, ptgid, cpid, ctgid))


def exit_(pid, tgid) -> bytes:
    return msg(forks.PROC_EVENT_EXIT, struct.pack("=iiIIii", pid, tgid, 0, 17, 1, 1))


def test_parse_forks_and_exits():
    assert forks.parse(fork(10, 10, 11, 11)) == [("fork", 10, 11)]
    # A thread started by a non-leader thread: the parent is the process (tgid).
    assert forks.parse(fork(12, 10, 13, 13)) == [("fork", 10, 13)]
    assert forks.parse(exit_(11, 11)) == [("exit", 11)]


def test_threads_exec_and_other_messages_are_skipped():
    assert forks.parse(fork(10, 10, 14, 10)) == []      # a new thread, not a process
    assert forks.parse(exit_(14, 10)) == []             # a thread ending
    assert forks.parse(msg(forks.PROC_EVENT_EXEC, struct.pack("=ii", 5, 5))) == []
    assert forks.parse(msg(forks.PROC_EVENT_FORK, struct.pack("=iiii", 1, 1, 2, 2), idx=7)) == []
    err = msg(forks.PROC_EVENT_FORK, struct.pack("=iiii", 1, 1, 2, 2), ntype=forks.NLMSG_ERROR)
    assert forks.parse(err) == []


def test_several_messages_in_one_buffer_and_short_input():
    buf = fork(1, 1, 2, 2) + exit_(2, 2) + fork(3, 3, 4, 4)
    assert forks.parse(buf) == [("fork", 1, 2), ("exit", 2), ("fork", 3, 4)]
    for cut in range(len(buf)):
        forks.parse(buf[:cut])   # never raises
    assert forks.parse(buf[:-4]) == [("fork", 1, 2), ("exit", 2)]
    # The fork union cut short is skipped, not misread.
    assert forks.parse(msg(forks.PROC_EVENT_FORK, struct.pack("=ii", 1, 1))) == []


def test_ancestor_walks_to_the_nearest_live_one_that_started_in_time():
    m = forks.ForkMap()
    m.fork(100, 200, t=10.0)     # turn shell 100 forks 200
    m.fork(200, 300, t=11.0)     # 200 forks the writer 300
    started = {100: 5.0}         # only 100 is alive; it started at 5
    alive = lambda pid, t: pid in started and started[pid] <= t  # noqa: E731
    assert m.ancestor(300, alive) == 100
    assert m.ancestor(200, alive) == 100
    # A pid reused after the fork (started later than it) is not the ancestor.
    started[100] = 10.5
    assert m.ancestor(300, alive) is None
    assert m.ancestor(999, alive) is None


def test_ancestor_survives_a_loop_in_bad_data():
    m = forks.ForkMap()
    m.fork(2, 1, t=1.0)
    m.fork(1, 2, t=1.0)
    assert m.ancestor(1, lambda pid, t: False) is None


def test_records_live_until_a_while_after_exit_and_the_map_is_bounded():
    m = forks.ForkMap(keep=120, limit=3)
    m.fork(1, 10, t=0)
    m.fork(1, 11, t=0)
    m.exit(10, t=100)
    m.prune(now=150)
    assert m.parent(10) == (1, 0)          # exited 50 s ago: still remembered
    m.prune(now=221)
    assert m.parent(10) is None and m.parent(11) == (1, 0)
    for c in (12, 13, 14):
        m.fork(1, c, t=1)
    assert len(m) == 3 and m.parent(11) is None   # the oldest went first
    # A reused pid replaces the old record.
    m.fork(7, 14, t=2)
    assert m.parent(14) == (7, 2)
    m.feed([("fork", 8, 20), ("exit", 20)], t=3)
    assert m.parent(20) == (8, 3)


@pytest.mark.skipif(os.geteuid() != 0, reason="cn_proc needs CAP_NET_ADMIN")
def test_real_fork_events():
    try:
        sock = forks.listen()
    except OSError as e:
        pytest.skip(f"no cn_proc here: {e}")
    m = forks.ForkMap()
    try:
        p = subprocess.Popen(["sh", "-c", "true & wait"])
        p.wait()
        end = time.monotonic() + 5
        while m.parent(p.pid) is None and time.monotonic() < end:
            forks.drain(sock, m)
            time.sleep(0.05)
    finally:
        sock.close()
    assert m.parent(p.pid) is not None and m.parent(p.pid)[0] == os.getpid()
    # sh's own child was recorded with sh as its parent.
    assert any(parent == p.pid for parent, _ in m._forks.values())
