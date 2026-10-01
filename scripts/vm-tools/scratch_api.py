"""A scripted Anthropic Messages API for driving the real Claude Code CLI end to end, with no model,
no quota and no login: point agentd at it with ANTHROPIC_BASE_URL=http://10.0.2.2:18555 and
ANTHROPIC_API_KEY=sk-ant-fake (the guest reaches the WSL side at 10.0.2.2), then send prompts.
Run it on the WSL side:  python3 scratch_api.py 18555 requests.log

This is tests/desktop/fake_api.py (as of main d9dde3b) bound to all addresses, plus the scenarios
the laptop VM tests needed. A prompt containing the word picks the script:
  silent      the request is accepted and never answered (a provider that hangs: the watchdog line)
  slowtool    one 45 s Bash command (a long tool must not look like silence)
  story       about a minute of streaming text (something to Stop)
  thinkslow   45 s of streamed thinking, then one word (long reasoning must not look like silence)
  vpn         a show_card call streamed a box at a time (the pictures)
  sysfile     a WebFetch, then a sudo change of /etc/wireguard (why and after lines)
  sysslow     the same change held open 30 s (to rest the pointer on the status line)
and the original ones: ffmpeg (sudo pacman, 4 s), password (create_app, streamed), docker (a 120 s
root sleep for Stop), joke (only talks). Anything else answers "OK.".
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = open(sys.argv[2], "a")  # noqa: SIM115 - one log for the life of the server
N = [0]

QML = """import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

AppWindow {
    title: "Passwords"
    subtitle: "Saved on this computer only"

    ListModel {
        id: logins
        ListElement { site: "github.com"; user: "daniel" }
        ListElement { site: "mail.proton.me"; user: "daniel" }
        ListElement { site: "archlinux.org"; user: "dan" }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10
        TextField { Layout.fillWidth: true; placeholderText: "Search" }
        Repeater {
            model: logins
            RowLayout {
                Layout.fillWidth: true
                Label { text: site; color: Theme.fg; font: Theme.font; Layout.fillWidth: true }
                Label { text: user; color: Theme.fg; opacity: 0.6 }
                Button { text: "Copy" }
            }
        }
        Item { Layout.fillHeight: true }
    }
}
"""


def text_of(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") if b.get("type") == "text" else "TOOL_RESULT" for b in c)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b"{}"
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def ev(self, name, data, pause=0.03):
        self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()
        time.sleep(pause)

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        try:
            body = json.loads(self.rfile.read(n))
        except Exception:  # noqa: BLE001 - a body that is not JSON is an empty request
            body = {}
        msgs = body.get("messages", [])
        if "count_tokens" in self.path or not body.get("stream"):
            r = json.dumps(
                {"input_tokens": 10}
                if "count_tokens" in self.path
                else {
                    "id": "msg_ns",
                    "type": "message",
                    "role": "assistant",
                    "model": body.get("model"),
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(r)))
            self.end_headers()
            self.wfile.write(r)
            return
        N[0] += 1
        # The newest prompt: a resumed session carries every earlier turn before it.
        prompts = [
            m
            for m in msgs
            if m.get("role") == "user"
            and not (
                isinstance(m.get("content"), list)
                and any(b.get("type") == "tool_result" for b in m["content"])
            )
        ]
        # agentd puts notes ("[Done by the user without you ...]") before the prompt itself.
        first = (text_of(prompts[-1]) if prompts else "").strip().split("\n\n")[-1]
        # The CLI now ends the list with a "system" message; look at the last user message.
        users = [m for m in msgs if m.get("role") == "user"]
        after_tool = (
            bool(users)
            and isinstance(users[-1].get("content"), list)
            and any(b.get("type") == "tool_result" for b in users[-1]["content"])
        )
        if "silent" in first:
            LOG.write("silent request held open" + chr(10)); LOG.flush()
            time.sleep(900)
            return
        LOG.write(
            json.dumps(
                {
                    "n": N[0],
                    "first": first[-200:],
                    "after_tool": after_tool,
                    "shape": [
                        (
                            m.get("role"),
                            [b.get("type") for b in m["content"]]
                            if isinstance(m.get("content"), list)
                            else "str",
                        )
                        for m in msgs[-3:]
                    ],
                }
            )
            + "\n"
        )
        LOG.flush()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()
        self.ev(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": f"msg_e2e{N[0]:03d}",
                    "type": "message",
                    "role": "assistant",
                    "model": body.get("model"),
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 1,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            },
        )
        idx = [0]

        def text(t, step=10, pause=0.05):
            i = idx[0]
            self.ev(
                "content_block_start",
                {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}},
            )
            for k in range(0, len(t), step):
                self.ev(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": i,
                        "delta": {"type": "text_delta", "text": t[k : k + step]},
                    },
                    pause,
                )
            self.ev("content_block_stop", {"type": "content_block_stop", "index": i})
            idx[0] += 1

        def thinking(t, pause=1.5):
            i = idx[0]
            self.ev(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": i,
                    "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                },
            )
            self.ev(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": i,
                    "delta": {"type": "thinking_delta", "thinking": t},
                },
                pause,
            )
            self.ev(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": i,
                    "delta": {"type": "signature_delta", "signature": "c2ln"},
                },
            )
            self.ev("content_block_stop", {"type": "content_block_stop", "index": i})
            idx[0] += 1

        def tool(tid, name, inp, step=15, pause=0.03):
            i = idx[0]
            self.ev(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": i,
                    "content_block": {"type": "tool_use", "id": tid, "name": name, "input": {}},
                },
            )
            s = json.dumps(inp)
            for k in range(0, len(s), step):
                self.ev(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": i,
                        "delta": {"type": "input_json_delta", "partial_json": s[k : k + step]},
                    },
                    pause,
                )
            self.ev("content_block_stop", {"type": "content_block_stop", "index": i})
            idx[0] += 1

        stop = "end_turn"
        if "ffmpeg" in first:
            if after_tool:
                text("Installed ffmpeg 7.1. It is ready to use from any terminal or app.")
            else:
                thinking("The user wants ffmpeg. Use pacman.")
                text("I'll install ffmpeg with pacman.")
                tool(
                    f"toolu_ff{N[0]}",
                    "Bash",
                    {
                        "command": "sudo pacman -S --noconfirm --print ffmpeg; sleep 4",
                        "description": "Install ffmpeg",
                    },
                )
                stop = "tool_use"
        elif "password" in first:
            if after_tool:
                text("Made Passwords. It is open now, and it keeps everything on this computer.")
            else:
                text("I'll build you a password manager.")
                tool(
                    f"toolu_pw{N[0]}",
                    "mcp__bombadil-os__create_app",
                    {"title": "Passwords", "description": "Saved logins", "qml": QML},
                    step=12,
                    pause=0.06,
                )
                stop = "tool_use"
        elif "docker" in first:
            if after_tool:
                text("Docker is set up.")
            else:
                text("Setting up docker.")
                tool(
                    f"toolu_dk{N[0]}",
                    "Bash",
                    {
                        "command": "sudo sh -c 'echo installing docker; sleep 120'",
                        "description": "Install docker",
                    },
                )
                stop = "tool_use"
        elif "vpn" in first:
            if after_tool:
                text("That is the idea: your traffic travels wrapped to a server you trust.")
            else:
                text("I will draw how it works.")
                tool(f"toolu_vpn{N[0]}", "mcp__bombadil-os__show_card",
                     {"shape": "chain", "title": "How a VPN works",
                      "nodes": [{"label": "Your laptop", "sub": "wraps each packet"},
                                {"label": "Encrypted tunnel", "sub": "unreadable on the way", "state": "active"},
                                {"label": "VPN server", "sub": "unwraps and forwards"},
                                {"label": "The website", "sub": "sees the server, not you"}],
                      "say": "Your traffic travels wrapped to a server you trust, which passes it on."},
                     step=14, pause=0.35)
                stop = "tool_use"
        elif "sysslow" in first:
            if after_tool:
                text("Done: /etc/wireguard exists.")
            else:
                text("I will create /etc/wireguard because WireGuard reads its tunnels from there at boot.")
                tool(f"toolu_ss{N[0]}", "Bash", {"command": "sudo mkdir -p /etc/wireguard && sleep 30", "description": "Create the WireGuard config folder"})
                stop = "tool_use"
        elif "sysfile" in first:
            ntool = 0
            for m in reversed(msgs):   # tool results since the newest real prompt only
                if m.get("role") != "user":
                    continue
                if isinstance(m.get("content"), list) and any(b.get("type") == "tool_result" for b in m["content"]):
                    ntool += sum(1 for b in m["content"] if b.get("type") == "tool_result")
                else:
                    break
            if ntool == 0:
                text("First I will read the WireGuard quickstart.")
                tool(f"toolu_wf{N[0]}", "WebFetch", {"url": "https://www.wireguard.com/quickstart/", "prompt": "Summarize where the config lives"})
                stop = "tool_use"
            elif ntool == 1:
                text("The quickstart keeps tunnels in /etc/wireguard. I will create that folder because WireGuard reads its tunnels from there at boot.")
                tool(f"toolu_sf{N[0]}", "Bash", {"command": "sudo mkdir -p /etc/wireguard && sudo touch /etc/wireguard/wg0.conf && sleep 30", "description": "Create the WireGuard config folder"})
                stop = "tool_use"
            else:
                text("Done: /etc/wireguard exists with an empty wg0.conf.")
        elif "thinkslow" in first:
            i = idx[0]
            self.ev("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
            for k in range(22):
                self.ev("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "thinking_delta", "thinking": "Considering the options carefully. "}}, 2.0)
            self.ev("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "signature_delta", "signature": "c2ln"}})
            self.ev("content_block_stop", {"type": "content_block_stop", "index": i})
            idx[0] += 1
            text("Done thinking.")
        elif "slowtool" in first:
            if after_tool:
                text("The slow step finished.")
            else:
                text("Running a slow step.")
                tool(f"toolu_sl{N[0]}", "Bash", {"command": "sleep 45; echo done", "description": "A slow step"})
                stop = "tool_use"
        elif "story" in first:
            text("Once upon a time there was a penguin who built a kernel. " * 40, step=20, pause=0.5)
        elif "joke" in first:
            text("Why do penguins never get lost at sea?\nThey always follow the kernel.", step=6, pause=0.08)
        else:
            text("OK.")
        self.ev(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 42},
            },
        )
        self.ev("message_stop", {"type": "message_stop"})


ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
