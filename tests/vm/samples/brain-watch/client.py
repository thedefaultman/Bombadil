"""A watcher client for the VM checks: copies every line from watch.sock to stdout. Run it as
the uid whose stream you want (setpriv); the watcher routes by the peer's uid."""
import socket
import sys

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(sys.argv[1])
out = sys.stdout.buffer
while True:
    b = s.recv(1 << 20)
    if not b:
        break
    out.write(b)
    out.flush()
