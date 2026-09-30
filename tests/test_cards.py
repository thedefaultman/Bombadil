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
