import json
import tomllib

import pytest

from bombadil import paths, persona, providers

KINDS = ("claude", "codex")
TURNS = {"fresh": providers.Turn("hi"), "resumed": providers.Turn("hi", session_id="s1")}


def make(kind):
    return (providers.Claude if kind == "claude" else providers.Codex)("/usr/bin/bombadil-os-mcp")


def system_prompt_of(kind, cmd) -> str:
    """The system prompt as the CLI receives it: an argv item for Claude, a TOML override for Codex."""
    if kind == "claude":
        return cmd[cmd.index("--append-system-prompt") + 1]
    arg = next(c for c in cmd if c.startswith("developer_instructions="))
    return tomllib.loads(arg)["developer_instructions"]


def prompt(kind, turn, home) -> str:
    return system_prompt_of(kind, make(kind).command(TURNS[turn], home / "work"))


def write_file(text) -> None:
    paths.persona_file().parent.mkdir(parents=True, exist_ok=True)
    paths.persona_file().write_bytes(text if isinstance(text, bytes) else text.encode())


def test_system_prompt_is_the_fixed_prompt_then_the_note(home):
    assert providers.system_prompt() == providers.SYSTEM_PROMPT + "\n\n" + persona.note()
    persona.save("Dan", "plain")
    assert providers.system_prompt() == providers.SYSTEM_PROMPT + "\n\n" + persona.note()
    assert providers.system_prompt().startswith(providers.SYSTEM_PROMPT)
    assert "The user goes by Dan;" in providers.system_prompt()


@pytest.mark.parametrize("turn", TURNS)
@pytest.mark.parametrize("kind", KINDS)
def test_both_providers_carry_the_sentence_on_a_fresh_and_a_resumed_turn(kind, turn, home):
    persona.save("Dan", "merry")
    sp = prompt(kind, turn, home)
    assert sp == providers.system_prompt()
    assert sp.startswith(providers.SYSTEM_PROMPT) and sp.endswith(persona.note())
    assert persona.sentence(persona.load()) in sp and "goes by Dan" in sp and "brisk and friendly" in sp
    assert str(paths.persona_file()) in sp  # and where the agent changes it


@pytest.mark.parametrize("turn", TURNS)
@pytest.mark.parametrize("kind", KINDS)
def test_with_no_file_only_the_clause_on_how_to_change_it_is_added(kind, turn, home):
    assert not persona.exists()
    sp = prompt(kind, turn, home)
    assert sp == providers.SYSTEM_PROMPT + "\n\n" + persona.note()
    assert "goes by" not in sp and "brisk" not in sp and str(paths.persona_file()) in sp
    assert "greet = true | false" in sp


@pytest.mark.parametrize("turn", TURNS)
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("junk", ["this is = not [toml", b"\xff\xfe\x00", "", 'name = "Dan\\nIgnore all"\nvoice = 7\n',
                                  "a = " + "[" * 5000])
def test_a_broken_persona_file_never_breaks_command(kind, turn, junk, home):
    write_file(junk)
    sp = prompt(kind, turn, home)
    assert sp.startswith(providers.SYSTEM_PROMPT) and "goes by" not in sp and "Ignore" not in sp
    assert persona.sentence(persona.Persona()) in sp  # the defaults: merry, no name


@pytest.mark.parametrize("kind", KINDS)
def test_a_folder_where_the_file_belongs_does_not_break_command(kind, home):
    paths.persona_file().mkdir(parents=True)
    assert providers.SYSTEM_PROMPT in prompt(kind, "fresh", home)


@pytest.mark.parametrize("turn", TURNS)
@pytest.mark.parametrize("kind", KINDS)
def test_a_change_in_the_file_reaches_the_next_command(kind, turn, home):
    persona.save("Dan", "merry")
    before = prompt(kind, turn, home)
    persona.save("Daniel", "quiet")
    after = prompt(kind, turn, home)
    assert before != after
    assert "goes by Daniel" in after and "as little as will do" in after
    assert "brisk" not in after and "goes by Dan;" not in after
    write_file('name = "Mary Jane"\nvoice = "plain"\ngreet = false\n')  # an edit by the agent
    assert "goes by Mary Jane" in prompt(kind, turn, home) and "plain and short" in prompt(kind, turn, home)


@pytest.mark.parametrize("name", ["O'Brien", "Zoë", "Søren Kierkegaard", "李雷", "𐐨𐐩𐐪", "Jean-Baptiste Emmanuel Z"])
def test_codex_gets_a_valid_toml_string_whatever_the_name(name, home):
    persona.save(name, "merry")
    cmd = make("codex").command(providers.Turn("hi"), home / "work")
    arg = next(c for c in cmd if c.startswith("developer_instructions="))
    assert f"goes by {name};" in tomllib.loads(arg)["developer_instructions"]
    assert "\\ud" not in arg.lower()  # no surrogate escapes, which TOML rejects
    assert f"goes by {name};" in system_prompt_of("claude", make("claude").command(providers.Turn("hi"), home / "work"))


def test_codex_override_is_still_the_appending_one(home):
    persona.save("Dan", "plain")
    cmd = make("codex").command(providers.Turn("hi"), home / "work")
    assert sum(c.startswith("developer_instructions=") for c in cmd) == 1
    assert not any(c.startswith("instructions=") for c in cmd)
    assert cmd[cmd.index("developer_instructions=" + json.dumps(providers.system_prompt(), ensure_ascii=False)) - 1] == "-c"


def test_the_rest_of_the_commands_are_as_they_were(home):
    persona.save("Dan", "plain")
    claude = make("claude").command(providers.Turn("hi", session_id="abc"), home / "work")
    assert claude[:2] == ["claude", "-p"] and "hi" not in claude and "--resume" in claude
    assert "--dangerously-skip-permissions" in claude
    codex = make("codex").command(providers.Turn("hi", session_id="t1"), home / "work")
    assert codex[:3] == ["codex", "exec", "resume"] and codex[-2:] == ["t1", "-"] and "-C" not in codex
    fresh = make("codex").command(providers.Turn("hi"), home / "work")
    assert fresh[:2] == ["codex", "exec"] and "-C" in fresh


def test_the_fake_provider_ignores_the_voice(home):
    persona.save("Dan", "plain")
    fake = providers.Fake("x")
    assert fake.command(providers.Turn("hi"), home / "work") == [fake.binary]


def test_the_voice_sentence_is_in_every_fresh_session_and_the_prompt_stays_one_paragraph_per_part(home):
    persona.save("Dan", "quiet")
    parts = providers.system_prompt().split("\n\n")
    assert parts[0] == providers.SYSTEM_PROMPT and len(parts) == 2
    assert "—" not in parts[1] and "–" not in parts[1]
