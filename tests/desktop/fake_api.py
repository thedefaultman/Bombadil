"""A scripted Anthropic Messages API for driving the real Claude Code CLI end to end.

Each prompt the driver types has a script: "install ffmpeg" runs sudo pacman, "make me a password
manager" streams a create_app call slowly (so the line counts up), "set up docker" runs a root
sleep for Stop to end, "tell me a joke" only talks.
"""

import json
import sys
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

LOG = open(sys.argv[2], "a")
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
        except Exception:
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
        elif first == "ask me":
            # A coding session asks its user: its dot lights and the pill's line asks.
            if after_tool:
                text("Migrated the local database.")
            else:
                tool(
                    f"toolu_mg{N[0]}",
                    "AskUserQuestion",
                    {
                        "questions": [
                            {
                                "question": "Run the migration on the local database?",
                                "header": "Migration",
                                "multiSelect": False,
                                "options": [
                                    {"label": "Yes", "description": "Apply it now"},
                                    {"label": "No", "description": "Leave the database as it is"},
                                ],
                            }
                        ]
                    },
                )
                stop = "tool_use"
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


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
