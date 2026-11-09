"""cards: the diagram card the shell draws. Checking, layout, the text twin, and reading a card
that is still being written."""

import json

import pytest

from bombadil import cards


def node(label, **kw):
    return {"label": label, **kw}


def diagram(**over):
    spec = {"shape": "chain", "title": "How a VPN works", "nodes": [node("Laptop"), node("Tunnel"), node("Internet")]}
    spec.update(over)
    return spec


def ok(spec):
    card, errors = cards.validate_diagram(spec)
    assert errors == [] and card is not None
    return card


def bad(spec):
    card, errors = cards.validate_diagram(spec)
    assert card is None and errors
    return errors


# -- checking --

def test_a_chain_gets_ids_links_and_a_text_twin():
    c = ok(diagram(say="Everything you send goes through the tunnel first."))
    assert c["type"] == "diagram" and c["shape"] == "chain"
    assert [n["id"] for n in c["nodes"]] == ["n1", "n2", "n3"]
    assert c["links"] == [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}]
    assert c["text"] == ("How a VPN works: Laptop → Tunnel → Internet\n"
                         "Everything you send goes through the tunnel first.")


def test_a_chain_can_be_unlinked():
    c = ok(diagram(linked=False))
    assert c["links"] == []
    assert c["text"] == "How a VPN works: Laptop; Tunnel; Internet"


def test_errors_say_what_and_why_so_the_agent_can_fix_them():
    errs = bad({"shape": "spiral", "title": "", "nodes": [node("x" * 40), node("ok", state="purple", icon="nope")]})
    text = "\n".join(errs)
    assert "shape must be one of chain, layers, compare, timeline" in errs
    assert "title is required" in text
    assert "nodes[0].label is 40 characters; the limit is 32" in text
    assert "nodes[1].state must be one of ok, warn, bad, new, gone, active" in text
    assert "nodes[1].icon 'nope' is not a kit icon" in text


def test_a_picture_holds_at_most_twelve_boxes_and_sixteen_links():
    errs = bad(diagram(nodes=[node(f"n{i}") for i in range(13)]))
    assert any("13 nodes; a picture holds at most 12" in e for e in errs)
    nodes = [node(f"n{i}", id=f"n{i}") for i in range(12)]
    links = [{"from": "n0", "to": f"n{1 + i % 11}"} for i in range(17)]
    assert any("17 links; a picture holds at most 16" in e for e in bad(diagram(shape="layers", nodes=nodes, links=links)))


def test_a_link_must_join_two_boxes_that_exist():
    errs = bad(diagram(links=[{"from": "n1", "to": "ghost"}]))
    assert "links[0] joins 'n1' to 'ghost'; both must be node ids (n1, n2, n3)" in errs


def test_ids_are_unique_and_plain():
    assert any("used twice" in e for e in bad(diagram(nodes=[node("a", id="x"), node("b", id="x")])))
    assert any("may use letters" in e for e in bad(diagram(nodes=[node("a", id="a b")])))


def test_lengths():
    assert any("sub is 61 characters" in e for e in bad(diagram(nodes=[node("a", sub="x" * 61)])))
    assert any("title is 61 characters" in e for e in bad(diagram(title="t" * 61)))
    assert any("say is 161 characters" in e for e in bad(diagram(say="s" * 161)))
    assert any("label is 25 characters" in e for e in bad(diagram(links=[{"from": "n1", "to": "n2", "label": "l" * 25}])))


def test_whitespace_in_text_is_folded():
    c = ok(diagram(title="  How   a\nVPN works ", nodes=[node(" a  b ", sub="x\ty")]))
    assert c["title"] == "How a VPN works" and c["nodes"][0]["label"] == "a b" and c["nodes"][0]["sub"] == "x y"


def test_highlight_names_boxes():
    c = ok(diagram(highlight="n2"))
    assert c["highlight"] == ["n2"]
    assert any("highlight 'n9' is not a node id" in e for e in bad(diagram(highlight=["n9"])))


def test_a_note_is_kept_short():
    assert ok(diagram(nodes=[node("a", note="names come from 1.1.1.1")]))["nodes"][0]["note"] == "names come from 1.1.1.1"
    assert len(ok(diagram(nodes=[node("a", note="n" * 200)]))["nodes"][0]["note"]) <= cards.MAX_SUB


def test_the_kit_icons_are_the_only_icons():
    assert "wifi" in cards.icon_names() and "shield" in cards.icon_names()
    assert ok(diagram(nodes=[node("a", icon="wifi")]))["nodes"][0]["icon"] == "wifi"


# -- what a box opens --

@pytest.mark.parametrize("opens", [
    {"kind": "path", "value": "/etc/wireguard/wg0.conf"}, {"kind": "path", "value": "~/Downloads"},
    {"kind": "unit", "value": "NetworkManager.service"}, {"kind": "unit", "value": "wg-quick@wg0.service"},
    {"kind": "package", "value": "wireguard-tools"}, {"kind": "url", "value": "https://wireguard.com/quickstart"},
    {"kind": "turn", "value": "12"}, {"kind": "turn", "value": 12},
])
def test_boxes_open_real_things(opens):
    assert ok(diagram(nodes=[node("a", opens=opens)]))["nodes"][0]["opens"] == {"kind": opens["kind"],
                                                                                "value": str(opens["value"])}


@pytest.mark.parametrize("opens,why", [
    ({"kind": "path", "value": "relative/file"}, "absolute"),
    ({"kind": "unit", "value": "NetworkManager"}, "must look like NetworkManager.service"),
    ({"kind": "unit", "value": "x.service; rm -rf /"}, "must look like"),
    ({"kind": "package", "value": "a b"}, "package name"),
    ({"kind": "url", "value": "file:///etc/passwd"}, "http"),
    ({"kind": "url", "value": "javascript:alert(1)"}, "http"),
    ({"kind": "turn", "value": "last"}, "number"),
    ({"kind": "shell", "value": "rm -rf ~"}, "opens.kind must be one of"),
    ("rm -rf ~", "must be {kind, value}"),
])
def test_what_a_box_may_not_open(opens, why):
    assert any(why in e for e in bad(diagram(nodes=[node("a", opens=opens)])))


# -- layers --

def layered(nodes, links):
    return ok({"shape": "layers", "title": "What Bluetooth needs",
               "nodes": [{"id": i, "label": i} for i in nodes],
               "links": [{"from": a, "to": b} for a, b in links]})


def test_layers_put_a_box_below_everything_that_points_at_it():
    c = layered("abcd", [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
    rank = {n["id"]: n["rank"] for n in c["nodes"]}
    assert rank == {"a": 0, "b": 1, "c": 1, "d": 2}


def test_layers_order_a_row_to_keep_links_from_crossing():
    c = layered("abcde", [("a", "d"), ("b", "c"), ("a", "e")])
    col = {n["id"]: n["col"] for n in c["nodes"]}
    # rank 0: a, b; rank 1: d, e (children of a), c (child of b): c must sit beside b's side
    assert {n["id"] for n in c["nodes"] if n["rank"] == 0} == {"a", "b"}
    assert col["a"] == 0 and col["b"] == 1
    assert col["c"] > col["d"] and col["c"] > col["e"]


def test_layers_survive_a_cycle_and_a_self_link():
    c = layered("abc", [("a", "b"), ("b", "c"), ("c", "a"), ("a", "a")])
    assert sorted(n["rank"] for n in c["nodes"]) == [0, 1, 2]


def test_layers_are_deterministic():
    spec = {"shape": "layers", "title": "t", "nodes": [{"id": i, "label": i} for i in "abcdef"],
            "links": [{"from": a, "to": b} for a, b in ("ab", "ac", "bd", "ce", "df", "ef")]}
    assert ok(spec) == ok(json.loads(json.dumps(spec)))


def test_layers_text_says_what_each_needs():
    c = layered("abc", [("a", "b"), ("a", "c")])
    assert c["text"].splitlines()[1:] == ["a needs b, c", "b", "c"]


# -- compare --

def compare(nodes):
    return {"shape": "compare", "title": "Before and after", "nodes": nodes}


def test_compare_needs_a_side_on_every_box():
    assert any("nodes[0].side is required" in e for e in bad(compare([node("DNS")])))
    assert any("side must be before or after" in e for e in bad(compare([node("DNS", side="left")])))


def test_compare_puts_the_same_key_on_one_row():
    c = ok(compare([node("DNS", side="before", sub="192.168.1.1", key="dns"),
                    node("DNS", side="after", sub="1.1.1.1", key="dns", state="new"),
                    node("Tunnel", side="after", state="new")]))
    rows = [n["row"] for n in c["nodes"]]
    assert rows == [0, 0, 1]
    assert c["text"].splitlines()[1:] == ["Before: DNS (192.168.1.1)", "After: DNS (1.1.1.1) [new]; Tunnel [new]"]


# -- timeline --

def test_timeline_keeps_the_time_and_the_weight_of_each_step():
    c = ok({"shape": "timeline", "title": "Boot",
            "nodes": [node("kernel", time="0.5 s", weight=0.5), node("network", time="6 s", weight="4.25", state="warn")]})
    assert c["nodes"][1]["time"] == "6 s" and c["nodes"][1]["weight"] == 4.25
    assert c["text"].splitlines()[1:] == ["0.5 s: kernel", "6 s: network [slow or weak]"]
    assert any("weight must be a number" in e for e in bad({"shape": "timeline", "title": "t",
                                                            "nodes": [node("a", weight="heavy")]}))


# -- the card from another process --

def test_accept_checks_again_and_keeps_only_what_the_os_adds():
    c = ok(diagram())
    c["source"], c["target"], c["receipt"] = "network", "wg-quick@wg0.service", True
    out, errs = cards.accept(c)
    assert errs == [] and out["source"] == "network" and out["target"] == "wg-quick@wg0.service" and out["receipt"] is True
    c["source"], c["target"], c["receipt"] = "the moon", "x; y", "yes"
    out, _ = cards.accept(c)
    assert "source" not in out and "target" not in out and "receipt" not in out


def test_accept_refuses_what_is_not_a_card():
    assert cards.accept("rm -rf")[0] is None
    out, errs = cards.accept({"shape": "chain", "title": "t", "nodes": [{"label": "a", "opens": {"kind": "url",
                                                                                          "value": "file:///x"}}]})
    assert out is None and errs


# -- a card still being written --

WRITTEN = json.dumps({"kind": "diagram", "shape": "chain", "title": "How a VPN works",
                      "nodes": [{"label": "Laptop", "sub": "you"}, {"label": "Tunnel", "state": "new"},
                                {"label": "Internet"}]})


def test_a_half_written_card_shows_its_frame_and_the_boxes_finished_so_far():
    assert cards.partial_diagram('{"kind": "dia') is None
    p = cards.partial_diagram('{"kind": "diagram", "shape": "layers", "title": "How a VP')
    assert p is None   # a title cut off mid-word is not shown as one
    p = cards.partial_diagram('{"kind": "diagram", "shape": "layers", "title": "How a VPN works", "nodes": [')
    assert p["shape"] == "layers" and p["title"] == "How a VPN works" and p["nodes"] == [] and p["partial"] is True
    cut = WRITTEN[:WRITTEN.index('"Internet"') - 3]
    p = cards.partial_diagram(cut)
    assert [n["label"] for n in p["nodes"]] == ["Laptop", "Tunnel"]
    assert p["nodes"][0]["sub"] == "you" and p["nodes"][1]["state"] == "new"


def test_a_box_cut_in_the_middle_of_a_word_is_not_drawn_yet():
    cut = WRITTEN[:WRITTEN.index('"Tunnel"') + 4]
    assert [n["label"] for n in cards.partial_diagram(cut)["nodes"]] == ["Laptop"]


def test_braces_and_quotes_inside_text_do_not_confuse_the_reader():
    text = '{"title": "t", "nodes": [{"label": "a } b", "sub": "say \\"hi\\" {x}"}, {"label": "c"'
    p = cards.partial_diagram(text)
    assert [n["label"] for n in p["nodes"]] == ["a } b"] and p["nodes"][0]["sub"] == 'say "hi" {x}'


def test_the_whole_card_reads_the_same_as_the_finished_one():
    p = cards.partial_diagram(WRITTEN)
    assert [n["label"] for n in p["nodes"]] == ["Laptop", "Tunnel", "Internet"]


def stream(name, call_json, size=7, index=1, ident="toolu_1"):
    s = cards.CardStream()
    out = [s.feed({"kind": "tool_start", "name": name, "index": index, "id": ident})]
    for i in range(0, len(call_json), size):
        out.append(s.feed({"kind": "tool_input", "index": index, "partial": call_json[i:i + size]}))
    return [c for c in out if c]


def test_the_stream_says_so_when_the_card_grows_and_only_then():
    got = stream("mcp__bombadil-os__show_card", WRITTEN)
    sizes = [(c["title"], len(c["nodes"])) for c in got]
    assert sizes[0] == ("How a VPN works", 0)
    assert sizes[-1] == ("How a VPN works", 3)
    assert sizes == sorted(set(sizes), key=sizes.index)   # never the same state twice
    assert {c["id"] for c in got} == {"stream-toolu_1"} and all(c["partial"] for c in got)


def test_the_stream_ignores_other_tools():
    assert stream("mcp__bombadil-os__create_app", WRITTEN) == []
    assert stream("Bash", WRITTEN) == []
    s = cards.CardStream()
    assert s.feed({"kind": "tool_input", "index": 4, "partial": "{}"}) is None


def test_two_calls_at_once_are_two_cards():
    s = cards.CardStream()
    s.feed({"kind": "tool_start", "name": "mcp__bombadil-os__show_card", "index": 1, "id": "a"})
    s.feed({"kind": "tool_start", "name": "mcp__bombadil-os__show_card", "index": 2, "id": "b"})
    a = s.feed({"kind": "tool_input", "index": 1, "partial": '{"title": "First"'})
    b = s.feed({"kind": "tool_input", "index": 2, "partial": '{"title": "Second"'})
    assert (a["id"], a["title"], b["id"], b["title"]) == ("stream-a", "First", "stream-b", "Second")


# -- the kinds that come from connections: message, task, list --
# (Everything a person might read here is invented: Priya Shah, Marcus Webb, #launch, workspace "Acme".)

NOW = 1_700_000_000.0     # a Tuesday afternoon, UTC; the times below are said in the machine's own zone


def reading(**over):
    """A message card as cardmake makes it: a thread of two, a draft nobody has typed in yet."""
    c = {"type": "message", "id": "msg:slack:w1/C1/1700000000.000200", "title": "Priya Shah in #launch",
         "source": "connect", "say": "Reply in #launch. Sending is yours.",
         "thread": [{"ref": "slack:w1/C1/1700000000.000100", "from": "Priya Shah", "text": "Legal signed off.",
                     "ts": NOW - 120, "mine": False, "unread": False},
                    {"ref": "slack:w1/C1/1700000000.000200", "from": "Priya Shah", "text": "Can we lock the 14th?",
                     "ts": NOW - 60, "mine": False, "unread": True}],
         "reply": {"proposal": "p3", "kind": "slack_reply", "target": "slack:w1/C1/1700000000.000200",
                   "where": "#launch", "content": "Yes, the 14th.", "warnings": [], "fingerprint": "ab12",
                   "ready": True, "state": "open", "by": "person", "receipt": None, "note": ""},
         "open": {"kind": "url", "value": "https://acme.test/archives/C1/p1700000000000200"}}
    c.update(over)
    return c


def work(**over):
    c = {"type": "task", "id": "task:linear:LIN-42", "title": "Fix the launch checklist", "source": "connect",
         "service": "linear", "status": "In review", "why": "Assigned to you by Marcus Webb.",
         "fields": [{"key": "status", "label": "Status", "value": "In review", "edit": None},
                    {"key": "body", "label": "Comment", "value": "", "edit": "text"}],
         "proposal": {"proposal": "p4", "kind": "task_comment", "target": "linear:LIN-42", "where": "Linear",
                      "content": {"body": "On it."}, "warnings": [], "fingerprint": "cd34", "ready": False,
                      "state": "open", "by": "person", "receipt": None, "note": ""},
         "open": {"kind": "url", "value": "https://acme.test/issue/LIN-42"}, "say": "Comment on this in Linear."}
    c.update(over)
    return c


def listing(**over):
    c = {"type": "list", "id": "list:unread", "title": "Unread messages", "source": "connect",
         "say": "2 unread messages, newest first.",
         "rows": [{"id": "msg:a", "title": "Priya Shah in #launch", "sub": "Can we lock the 14th?", "when": "11:04",
                   "state": "new", "actions": [], "opens": {"kind": "card", "value": "msg:a"}},
                  {"id": "conn:slack:w1", "title": "Slack in Acme", "sub": "Direct messages and mentions.",
                   "when": "", "state": "ok",
                   "actions": [{"id": "disconnect", "label": "Disconnect", "style": "quiet"}], "opens": None}]}
    c.update(over)
    return c


def accepted(card):
    out, errors = cards.accept(card, now=NOW)
    assert errors == [] and out is not None, errors
    return out


def refused(card, **kw):
    out, errors = cards.accept(card, now=NOW, **kw)
    assert out is None and errors
    return errors


def test_a_message_card_is_accepted_whole_with_its_words_worked_out_once():
    c = accepted(reading())
    assert c["type"] == "message" and c["id"] == "msg:slack:w1/C1/1700000000.000200" and c["via"] == "Slack"
    assert [m["from"] for m in c["thread"]] == ["Priya Shah", "Priya Shah"]
    assert [m["unread"] for m in c["thread"]] == [False, True]
    assert c["reply"]["ready"] is True and c["reply"]["button"] == "Send" and c["reply"]["content"] == "Yes, the 14th."
    assert c["open"] == {"kind": "url", "value": "https://acme.test/archives/C1/p1700000000000200"}
    assert c["lines"] == {"sending": "Sending…",
                          "unknown": "I can't tell whether that went. Look in Slack before you press again."}
    assert c["text"].splitlines()[0] == "Priya Shah in #launch (Slack)"
    assert "Button: Send" in c["text"] and "Reply: Yes, the 14th." in c["text"] and c["text"].endswith("Open in Slack")


def test_a_message_card_says_how_long_ago_in_its_own_short_words():
    c = accepted(reading(thread=[{"ref": "slack:w1/C1/1", "from": "Priya Shah", "text": "Hello", "ts": NOW - 90_000,
                                  "mine": False, "unread": False}]))
    now_local = cards.time.localtime(NOW)
    then_local = cards.time.localtime(NOW - 90_000)
    assert c["thread"][0]["when"] == cards.time.strftime("%a %H:%M", then_local) and now_local[:3] != then_local[:3]
    assert cards._when(NOW - 30 * 86400, NOW) == cards.time.strftime("%d %b", cards.time.localtime(NOW - 30 * 86400)).lstrip("0")


def test_the_last_six_of_a_longer_thread_are_kept_oldest_first():
    thread = [{"ref": f"slack:w1/C1/{i}", "from": "Priya Shah", "text": f"message {i}", "ts": NOW - 100 + i,
               "mine": False, "unread": False} for i in range(9)]
    c = accepted(reading(thread=thread))
    assert [m["text"] for m in c["thread"]] == [f"message {i}" for i in range(3, 9)]


def test_a_thread_needs_refs_and_times_and_says_which_one_is_wrong():
    errs = refused(reading(thread=[{"ref": "not a ref", "from": "x", "text": "y", "ts": "noon"}]))
    assert any("thread[0].ref" in e for e in errs) and any("thread[0].ts" in e for e in errs)
    assert any("thread must be a list" in e for e in refused(reading(thread="Hello")))
    assert any("thread[1] must be an object" in e for e in refused(reading(thread=[reading()["thread"][0], 3])))


def test_what_every_card_has_is_checked_with_the_diagrams_limits():
    c = accepted(reading(title="x" * 120, say="y" * 400))
    assert len(c["title"]) == 80 and c["title"].endswith("…") and len(c["say"]) == 160
    errs = refused(reading(id="has a space", title="  ", source="the moon"))
    assert any(e.startswith("id must be") for e in errs) and any(e.startswith("title is required") for e in errs)
    assert any(e.startswith("source must be one of connect, notification, mail") for e in errs)
    assert any("must be an object" in e for e in refused("a card"))


def test_other_peoples_words_are_made_plain_before_they_reach_the_bar():
    hostile = "Priya\u202e Shah\x00\x1b[2J\u200b"
    thread = [{"ref": "slack:w1/C1/1", "from": hostile, "text": "line one\r\nline two\x07\u2028line three\n\n\n\nend\u202e",
               "ts": NOW, "mine": False, "unread": True}]
    c = accepted(reading(title=hostile + " in #launch", thread=thread, say="Hello\u202e\nthere"))
    assert c["title"] == "Priya Shah [2J in #launch" and c["thread"][0]["from"] == "Priya Shah [2J"
    assert c["thread"][0]["text"] == "line one\nline two line three\n\nend"
    assert c["say"] == "Hello there"
    for s in (c["title"], c["say"], c["thread"][0]["from"], c["thread"][0]["text"], c["text"]):
        assert not any(ch in s for ch in "\x00\x1b\x07\u202e\u200b\u2028\r")
    assert cards._line("a" * 5000, 40) == "a" * 39 + "…"
    assert cards._line(None, 40) == "" and cards._block(None, 40) == ""


def test_a_senders_name_cannot_pose_as_another_by_hiding_or_reversing_letters():
    for name in ("Pri\u200bya Shah", "Priya\u2060 Shah", "\u202ePriya Shah", "Priya\u00ad Shah"):
        c = accepted(reading(thread=[{**reading()["thread"][0], "from": name}]))
        assert c["thread"][0]["from"] == "Priya Shah"


def test_the_box_is_what_would_be_sent_so_it_is_refused_not_altered_when_it_is_not_plain_or_too_long():
    reply = reading()["reply"]
    for content in ("Yes\u202e", "Yes\x00", "Yes\u200b", "a\rb"):
        errs = refused(reading(reply={**reply, "content": content}))
        assert any("reply.content has control, invisible or direction-changing characters" in e for e in errs)
    errs = refused(reading(reply={**reply, "content": "x" * (cards.MAX_CONTENT + 1)}))
    assert any(f"reply.content is {cards.MAX_CONTENT + 1} characters; the limit is {cards.MAX_CONTENT}" in e for e in errs)
    # A line break and a tab are text. Nothing is cut, collapsed or trimmed.
    c = accepted(reading(reply={**reply, "content": "Yes.\n\n  The 14th.\t"}))
    assert c["reply"]["content"] == "Yes.\n\n  The 14th.\t"
    assert any("reply.content must be text" in e for e in refused(reading(reply={**reply, "content": ["Yes"]})))


def test_a_reply_is_one_of_the_two_kinds_a_message_card_can_have():
    reply = reading()["reply"]
    assert any("reply.kind must be one of slack_reply, copy" in e
               for e in refused(reading(reply={**reply, "kind": "task_create"})))
    assert refused(reading(reply="send it"))[0] == "reply must be an object"
    assert accepted(reading(reply=None))["reply"] is None      # a thread that cannot be answered has no box


@pytest.mark.parametrize("field,value", [("proposal", "p 3"), ("proposal", 3), ("fingerprint", "no spaces here"),
                                         ("state", "done"), ("by", "the agent"), ("target", "x y"),
                                         ("warnings", "careful"), ("receipt", "ok")])
def test_each_part_of_a_draft_is_checked(field, value):
    errs = refused(reading(reply={**reading()["reply"], field: value}))
    assert any(e.startswith(f"reply.{field}") for e in errs), errs


def test_a_draft_that_says_ready_is_ready_only_when_it_says_so_in_so_many_words():
    for ready in (1, "true", None, "yes"):
        assert accepted(reading(reply={**reading()["reply"], "ready": ready}))["reply"]["ready"] is False
    assert accepted(reading())["reply"]["ready"] is True


def test_warnings_are_the_four_the_card_knows_with_plain_words_and_at_most_four():
    warnings = [{"kind": "new_place", "text": ""}, {"kind": "broadcast", "text": "This reaches\u202e everyone."},
                "long", {"kind": "tainted", "text": "x" * 500}, {"kind": "long", "text": "a fifth"}]
    c = accepted(reading(reply={**reading()["reply"], "warnings": warnings}))
    words = [w["text"] for w in c["reply"]["warnings"]]
    assert [w["kind"] for w in c["reply"]["warnings"]] == ["new_place", "broadcast", "long", "tainted"]
    assert words[0] == "You have never received a message from here." and words[1] == "This reaches everyone."
    assert words[2] == "This is a long message." and len(words[3]) == 240
    assert "You have never received a message from here." in c["text"]
    assert any("warnings[0].kind must be one of" in e
               for e in refused(reading(reply={**reading()["reply"], "warnings": [{"kind": "spooky"}]})))


def test_the_receipt_is_a_line_and_a_page_on_the_web_and_nothing_else():
    reply = {**reading()["reply"], "state": "sent", "ready": False,
             "receipt": {"line": "Posted in #launch · 11:04", "web": {"name": "Slack", "url": "https://acme.test/p/1"},
                         "token": "never"}}
    c = accepted(reading(reply=reply))
    assert c["reply"]["receipt"] == {"line": "Posted in #launch · 11:04",
                                     "web": {"name": "Slack", "url": "https://acme.test/p/1"}}
    assert c["text"].splitlines()[-3:-1] == ["Posted in #launch · 11:04", "Button: Send"]
    for bad_receipt in ({"line": ""}, {"line": "ok", "web": {"name": "x", "url": "javascript:alert(1)"}},
                        {"line": "ok", "web": "https://acme.test"}, "Posted"):
        assert refused(reading(reply={**reply, "receipt": bad_receipt}))


def test_a_draft_that_may_have_gone_says_so_in_the_cards_words_and_the_text_twin():
    reply = {**reading()["reply"], "state": "unknown", "ready": False}
    c = accepted(reading(reply=reply))
    assert "I can't tell whether that went. Look in Slack before you press again." in c["text"]
    assert c["lines"]["unknown"] in c["text"]
    assert "Sending…" in accepted(reading(reply={**reply, "state": "sending"}))["text"]


def test_where_a_card_opens_is_checked_like_a_pictures():
    assert accepted(reading(open=None))["open"] is None
    errs = refused(reading(open={"kind": "url", "value": "file:///etc/passwd"}))
    assert any(e.startswith("open: opens.value for a url must start with http") for e in errs)
    assert any(e.startswith("open: opens.kind must be one of") for e in refused(reading(open={"kind": "card", "value": "x"})))
    assert any(e.startswith("open: opens must be") for e in refused(reading(open="https://acme.test")))


def test_a_notification_is_a_message_card_whose_button_copies_and_is_no_send():
    card = reading(id="msg:n7", title="Priya Shah in Teams", source="notification",
                   thread=[{"ref": "notify:7", "from": "Priya Shah", "text": "Legal signed off.", "ts": NOW,
                            "mine": False, "unread": True}],
                   reply={"proposal": None, "kind": "copy", "where": "Teams", "content": ""}, open=None,
                   say="Copy and open takes your words to Teams.")
    c = accepted(card)
    assert c["via"] == "Teams" and c["reply"]["kind"] == "copy" and c["reply"]["button"] == "Copy and open"
    assert c["reply"]["proposal"] is None and c["reply"]["ready"] is False
    assert "Open in Teams" not in c["text"] and "Button: Copy and open" in c["text"]
    assert accepted({**card, "source": "mail"})["via"] == "Mail"


def test_a_message_card_drops_every_key_it_does_not_know():
    card = reading(secret="x", html="<b>hi</b>", token="x")
    card["thread"][0]["avatar"] = "http://tracker.test/1.png"
    card["reply"]["authorization"] = "x"
    c = accepted(card)
    assert set(c) == {"type", "id", "title", "source", "say", "thread", "reply", "open", "via", "lines", "text"}
    assert set(c["thread"][0]) == {"ref", "from", "text", "ts", "when", "mine", "unread"}
    assert set(c["reply"]) == {"kind", "proposal", "target", "where", "content", "warnings", "fingerprint", "ready",
                               "state", "by", "receipt", "note", "button"}


def test_a_message_card_the_agent_sends_by_the_drawing_tools_socket_is_refused():
    errs = refused(reading(), diagrams_only=True)
    assert errs == ["Messages, tasks and lists come from your connections, not from show_card."]
    for card in (work(), listing()):
        assert refused(card, diagrams_only=True) == errs
    assert cards.accept(ok(diagram()), diagrams_only=True)[0] is not None    # a picture still goes through


# -- task --

def test_a_task_card_is_accepted_with_its_boxes_its_button_and_its_text_twin():
    c = accepted(work())
    assert c["type"] == "task" and c["via"] == "Linear" and c["proposal"]["button"] == "Comment"
    assert [(f["key"], f["edit"]) for f in c["fields"]] == [("status", None), ("body", "text")]
    assert c["proposal"]["content"] == {"body": "On it."}
    assert c["text"].splitlines() == ["Fix the launch checklist (Linear)", "Status: In review",
                                      "Assigned to you by Marcus Webb.", "Status: In review", "Comment: On it.",
                                      "Comment on this in Linear.", "Button: Comment", "Open in Linear"]
    assert c["lines"]["unknown"].endswith("Look in Linear before you press again.")


def test_a_new_task_form_says_create_and_an_empty_box_is_still_a_box():
    card = work(title="New task in Linear", fields=[{"key": "title", "label": "Title", "value": "", "edit": "line"},
                                                    {"key": "due", "label": "Due", "value": "", "edit": "date"}],
                proposal={**work()["proposal"], "kind": "task_create", "target": "linear", "content": {}},
                open=None)
    c = accepted(card)
    assert c["proposal"]["button"] == "Create" and [f["edit"] for f in c["fields"]] == ["line", "date"]
    assert "Title: (empty)" in c["text"] and "Due: (empty)" in c["text"]


def test_a_task_card_checks_its_fields_and_its_draft():
    f = work()["fields"]
    assert any("fields[0].key must start with a letter" in e for e in refused(work(fields=[{**f[0], "key": "1 x"}])))
    assert any("fields[1].key 'status' is used twice" in e for e in refused(work(fields=[f[0], {**f[1], "key": "status"}])))
    assert any("fields[0].label is empty" in e for e in refused(work(fields=[{**f[0], "label": ""}])))
    assert any("fields[0].edit must be one of line, text, date or null" in e
               for e in refused(work(fields=[{**f[0], "edit": "html"}])))
    assert any("fields must be a list of at most 12" in e for e in refused(work(fields=[f[0]] * 13)))
    assert any("service is required" in e for e in refused(work(service="")))
    assert any("proposal is required" in e for e in refused({k: v for k, v in work().items() if k != "proposal"}))
    prop = work()["proposal"]
    assert any("proposal.kind must be one of task_create, task_comment" in e
               for e in refused(work(proposal={**prop, "kind": "slack_reply"})))
    assert any("proposal.content must be an object" in e for e in refused(work(proposal={**prop, "content": "On it."})))
    assert any("proposal.content has the key" in e for e in refused(work(proposal={**prop, "content": {"a b": "x"}})))
    assert any("proposal.content.body must be text" in e for e in refused(work(proposal={**prop, "content": {"body": ["x"]}})))
    assert any("proposal.content.body has control, invisible or direction-changing characters" in e
               for e in refused(work(proposal={**prop, "content": {"body": "On it.\u202e"}})))
    assert any("proposal.content.body is 5001 characters; the limit is 5000" in e
               for e in refused(work(proposal={**prop, "content": {"body": "x" * 5001}})))


def test_a_task_card_takes_a_reply_for_a_proposal_and_makes_what_it_shows_plain():
    spec = work(title="Fix\u202e the \x00checklist", why="Because\nof this", status="In  review")
    spec["reply"] = spec.pop("proposal")
    c = accepted(spec)
    assert c["title"] == "Fix the checklist" and c["why"] == "Because of this" and c["status"] == "In review"
    assert c["proposal"]["kind"] == "task_comment"
    spec["fields"] = [{"key": "body", "label": "Comment", "value": "line\r\n\r\n\r\nline\u200b", "edit": "text"},
                      {"key": "status", "label": "Status", "value": "a\nb", "edit": None}]
    c = accepted(spec)
    assert c["fields"][0]["value"] == "line\n\nline" and c["fields"][1]["value"] == "a b"


def test_a_task_card_drops_every_key_it_does_not_know():
    card = work(secret="x")
    card["fields"][0]["html"] = "<b>"
    c = accepted(card)
    assert set(c) == {"type", "id", "title", "source", "say", "service", "status", "why", "fields", "proposal", "open",
                      "via", "lines", "text"}
    assert set(c["fields"][0]) == {"key", "label", "value", "edit"}


# -- list --

def test_a_list_card_is_accepted_with_its_rows_marks_and_buttons():
    c = accepted(listing())
    assert c["type"] == "list" and c["via"] == "Connections"
    assert [(r["id"], r["state"]) for r in c["rows"]] == [("msg:a", "new"), ("conn:slack:w1", "ok")]
    assert c["rows"][0]["opens"] == {"kind": "card", "value": "msg:a"} and c["rows"][0]["actions"] == []
    assert c["rows"][1]["actions"] == [{"id": "disconnect", "label": "Disconnect", "style": "quiet"}]
    assert c["text"].splitlines() == ["Unread messages",
                                      "Priya Shah in #launch (Can we lock the 14th?), 11:04 [new]",
                                      "Slack in Acme (Direct messages and mentions.): Disconnect",
                                      "2 unread messages, newest first."]


def test_a_list_holds_at_most_twelve_rows_and_two_buttons_a_row_from_the_named_set():
    row = listing()["rows"][1]
    errs = refused(listing(rows=[{**row, "id": f"r{i}"} for i in range(13)]))
    assert any("13 rows; a list holds at most 12" in e for e in errs)
    assert len(accepted(listing(rows=[{**row, "id": f"r{i}"} for i in range(12)]))["rows"]) == 12
    act = {"id": "open", "label": "Open", "style": "primary"}
    errs = refused(listing(rows=[{**row, "actions": [act, act, act]}]))
    assert any("rows[0].actions must be a list of at most 2 buttons" in e for e in errs)
    errs = refused(listing(rows=[{**row, "actions": [{"id": "delete_everything", "label": "Delete"}]}]))
    assert any("rows[0].actions[0].id must be one of open, disconnect, connect, copy_open, dismiss" in e for e in errs)
    errs = refused(listing(rows=[{**row, "actions": [{"id": "open", "label": "", "style": "loud"}]}]))
    assert any("actions[0].label is empty" in e for e in errs) and any("style must be primary or quiet" in e for e in errs)
    assert accepted(listing(rows=[{**row, "actions": [{"id": "connect", "label": "Connect"}]}]))["rows"][0][
        "actions"][0]["style"] == "quiet"


def test_a_row_is_checked_for_its_id_title_state_and_where_it_opens():
    row = listing()["rows"][0]
    assert any("rows[0].id must be a string" in e for e in refused(listing(rows=[{**row, "id": "has space"}])))
    assert any("rows[1].id 'msg:a' is used twice" in e for e in refused(listing(rows=[row, row])))
    assert any("rows[0].title is empty" in e for e in refused(listing(rows=[{**row, "title": ""}])))
    assert any("rows[0].state must be one of ok, warn, bad, new, gone, active" in e
               for e in refused(listing(rows=[{**row, "state": "red"}])))
    assert any("rows[0].opens.value for a card must be a card or row id" in e
               for e in refused(listing(rows=[{**row, "opens": {"kind": "card", "value": "a b"}}])))
    assert any("rows[0].opens.value for a url must start with http" in e
               for e in refused(listing(rows=[{**row, "opens": {"kind": "url", "value": "ftp://x"}}])))
    assert accepted(listing(rows=[{**row, "opens": {"kind": "url", "value": "https://acme.test/x"}}]))[
        "rows"][0]["opens"] == {"kind": "url", "value": "https://acme.test/x"}
    assert accepted(listing(rows=[]))["rows"] == []
    assert any("rows must be a list" in e for e in refused(listing(rows="none")))


def test_a_list_makes_its_rows_words_plain_and_cuts_them():
    row = {**listing()["rows"][0], "title": "Priya\u202e Shah " + "x" * 200, "sub": "a\nb\x00" + "y" * 400,
           "when": "11:04" + "z" * 80}
    c = accepted(listing(rows=[row]))["rows"][0]
    assert c["title"].startswith("Priya Shah x") and len(c["title"]) == 80 and c["title"].endswith("…")
    assert c["sub"].startswith("a b y") and len(c["sub"]) == 240 and len(c["when"]) == 24
    assert accepted(listing(rows=[{**row, "state": None}]))["rows"][0]["state"] == "ok"


def test_a_list_card_drops_every_key_it_does_not_know():
    row = {**listing()["rows"][1], "token": "x"}
    row["actions"] = [{"id": "disconnect", "label": "Disconnect", "style": "quiet", "run": "rm -rf /"}]
    c = accepted(listing(rows=[row], extra="x"))
    assert set(c) == {"type", "id", "title", "source", "say", "rows", "via", "text"}
    assert set(c["rows"][0]) == {"id", "title", "sub", "when", "state", "actions", "opens"}
    assert set(c["rows"][0]["actions"][0]) == {"id", "label", "style"}


def test_the_three_kinds_leave_the_diagram_path_as_it_was():
    c = accepted(diagram())
    assert c["type"] == "diagram" and "via" not in c and "lines" not in c
    assert cards.accept({"type": "checklist", "id": "x", "title": "t", "nodes": [{"label": "a"}], "shape": "chain"})[0]["type"] == "diagram"
    assert cards.accept({"type": "message"}, now=NOW)[0] is None
    assert cards.accept({"type": "task"}, now=NOW)[0] is None
    assert cards.accept({"type": "list"}, now=NOW)[0] is None
