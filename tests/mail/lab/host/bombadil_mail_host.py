#!/usr/bin/env python3
"""Native-messaging host for the Bombadil Mail lab.

Thunderbird launches this (stdio framing: 4-byte NATIVE-endian length + JSON)
when the add-on calls messenger.runtime.connectNative("bombadil_mail"). The
host is a dumb relay between that pipe and a Unix stream socket served by the
lab harness (lab_bridge.py), speaking newline-delimited JSON on the socket.

  ext --[len+json]--> stdin  -> host -> socket --[json\\n]--> harness
  ext <-[len+json]-- stdout  <- host <- socket <-[json\\n]--- harness

Options (or env): --sock / BOMBADIL_MAIL_SOCK, --log / BOMBADIL_MAIL_HOST_LOG,
--hello-file / BOMBADIL_MAIL_HELLO_FILE (first {"hello":true} frame from the
add-on is written there: the simplest observable proof that an unsigned
add-on really ran, used by the signing matrix).

Everything the host sees (start, signals, EOF, frame sizes) is logged with
wall-clock timestamps so lifetime questions can be answered from the log.
"""
import argparse
import json
import os
import signal
import socket
import struct
import sys
import threading
import time

ap = argparse.ArgumentParser()
ap.add_argument("--sock", default=os.environ.get("BOMBADIL_MAIL_SOCK"))
ap.add_argument("--log", default=os.environ.get("BOMBADIL_MAIL_HOST_LOG"))
ap.add_argument("--hello-file", default=os.environ.get("BOMBADIL_MAIL_HELLO_FILE"))
# Thunderbird appends the manifest path and the extension id as argv[1], argv[2].
args, _extra = ap.parse_known_args()

_log_lock = threading.Lock()


def log(msg):
    if not args.log:
        return
    with _log_lock, open(args.log, "a") as f:
        f.write("%.3f pid=%d %s\n" % (time.time(), os.getpid(), msg))


log("start argv=%r ppid=%d env_lab=%r" % (sys.argv, os.getppid(), os.environ.get("BOMBADIL_LAB_DIR")))
for _sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGPIPE):
    def _h(signum, frame, _n=_sig.name):
        log("signal %s received, exiting" % _n)
        os._exit(0)
    signal.signal(_sig, _h)

stdin = sys.stdin.buffer
stdout = sys.stdout.buffer
out_lock = threading.Lock()
sock_lock = threading.Lock()
sock = None


def read_exact(n):
    buf = b""
    while len(buf) < n:
        chunk = stdin.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def to_ext(obj_or_bytes):
    data = obj_or_bytes if isinstance(obj_or_bytes, bytes) else json.dumps(obj_or_bytes, separators=(",", ":")).encode()
    with out_lock:
        stdout.write(struct.pack("=I", len(data)) + data)
        stdout.flush()
    log("host->ext frame %d bytes" % len(data))


def sock_reader():
    """Relay harness -> extension."""
    global sock
    while True:
        s = sock
        if s is None:
            time.sleep(0.2)
            continue
        f = s.makefile("rb")
        try:
            for line in f:
                line = line.strip()
                if line:
                    to_ext(line)
        except Exception as e:  # noqa
            log("socket reader error %r" % (e,))
        log("socket closed by harness")
        with sock_lock:
            if sock is s:
                sock = None


def connect_loop():
    global sock
    while True:
        if sock is None and args.sock:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(args.sock)
                with sock_lock:
                    sock = s
                log("connected to %s" % args.sock)
            except OSError:
                time.sleep(0.3)
                continue
        time.sleep(0.3)


threading.Thread(target=sock_reader, daemon=True).start()
threading.Thread(target=connect_loop, daemon=True).start()

while True:
    hdr = read_exact(4)
    if hdr is None:
        log("stdin EOF (Thunderbird closed the port or exited), exiting")
        break
    (n,) = struct.unpack("=I", hdr)
    body = read_exact(n)
    if body is None:
        log("stdin EOF inside frame")
        break
    log("ext->host frame %d bytes" % n)
    if args.hello_file and b'"hello":true' in body[:200].replace(b" ", b""):
        with open(args.hello_file + ".tmp", "wb") as f:
            f.write(body)
        os.replace(args.hello_file + ".tmp", args.hello_file)
    with sock_lock:
        s = sock
    if s is not None:
        try:
            s.sendall(body + b"\n")
        except OSError as e:
            log("socket send failed %r" % (e,))
    else:
        log("no harness socket, frame dropped")
log("exit")
