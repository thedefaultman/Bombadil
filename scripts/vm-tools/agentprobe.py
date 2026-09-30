import json, socket, sys, time
prompt, dur = sys.argv[1], float(sys.argv[2])
stop_at = float(sys.argv[3]) if len(sys.argv) > 3 else None
s = socket.socket(socket.AF_UNIX); s.connect("/run/user/1000/bombadil/agentd.sock"); s.settimeout(0.5)
s.sendall((json.dumps({"type": "prompt", "text": prompt.replace("_", " ").replace("@", ";").replace("BANG", "!")}) + chr(10)).encode())
t0 = time.time(); buf = b""; stopped = False
while time.time() - t0 < dur:
    if stop_at is not None and not stopped and time.time() - t0 >= stop_at:
        s.sendall((json.dumps({"type": "stop"}) + chr(10)).encode()); stopped = True; print("+%.1fs >> sent stop" % (time.time() - t0))
    try: chunk = s.recv(65536)
    except socket.timeout: continue
    if not chunk: break
    buf += chunk
    while chr(10).encode() in buf:
        line, buf = buf.split(chr(10).encode(), 1)
        try: m = json.loads(line)
        except ValueError: continue
        if m.get("type") != "event": continue
        k = m.get("kind"); now = f"+{time.time() - t0:.3f}s"
        if k == "status": print(now, "status:", str(m.get("text"))[:90], "| risk", m.get("risk"), "| src", m.get("source"), ("| because: " + str(m.get("because"))) if m.get("because") else "", ("| after: " + json.dumps(m.get("after"))) if m.get("after") else "")
        elif k == "card":
            c = m.get("card") or {}
            print(now, "CARD id=%s partial=%s gone=%s receipt=%s shape=%s nodes=%d title=%s" % (c.get("id"), c.get("partial"), c.get("gone"), c.get("receipt"), c.get("shape"), len(c.get("nodes") or []), c.get("title")) + " | say: " + str(c.get("say") or "")[:90])
        elif k == "local": print(now, "local:", m.get("action"), m.get("phase"), str(m.get("text"))[:80])
        elif k in ("turn_start", "snapshot", "queued"): print(now, k)
        elif k == "tool": print(now, "tool:", m.get("name") or m.get("tool"), str(m.get("command") or "")[:60])
        elif k == "text": print(now, "text:", str(m.get("text"))[:50].replace(chr(10), " "))
        elif k in ("result", "error"): print(now, k + ":", str(m.get("text"))[:200].replace(chr(10), " "), "| ok", m.get("ok"))
        elif k == "turn_end": print(now, "turn_end: stopped", m.get("stopped"), "| line:", m.get("line"), "| secs", m.get("seconds")); dur = 0
