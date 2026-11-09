"""providers.limit(): did the account refuse a turn for its limit, or was it a busy moment?

The Claude lines are shaped the way Claude Code writes them: a rate_limit_event, an assistant message with
error "rate_limit", and a result with api_error_status 429 (2.1.286, read from its binary). 2.1.319, run
against the scripted API as a claude.ai login, names the window as "5-hour limit" and carries
api_error "usage_limit_reached" on the assistant message and the result (see REAL_319). Only a real refusal
that also shows quota evidence counts; a window that merely reads "rejected", an entitlement check, the
throttle notice and a 529 never do. Neither version ran against a real used-up plan.
"""

import json
from datetime import datetime, timezone

import pytest

from bombadil import providers, rest

AT = datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc).timestamp()
RESET = AT + 3 * 3600   # 15:30 UTC


@pytest.fixture(autouse=True)
def now(monkeypatch):
    monkeypatch.setattr(rest, "now", lambda: AT)
    monkeypatch.setenv("TZ", "UTC")


def _line(**m) -> str:
    return json.dumps(m)


def _assistant(text, error="rate_limit", **more) -> str:
    return _line(type="assistant", error=error, is_api_error_message=True, **more,
                 message={"role": "assistant", "model": "<synthetic>", "content": [{"type": "text", "text": text}]})


def _result(text, status=429, reason="api_error", **more) -> str:
    return _line(type="result", subtype="success", is_error=True, num_turns=1, result=text, api_error_status=status,
                 terminal_reason=reason, session_id="s1", **more)


def _event(**info) -> str:
    return _line(type="rate_limit_event", rate_limit_info=info)


def _run(provider, *lines):
    """What agentd does with them: events in order, what the turn saw kept for limit(), the result last."""
    seen, result = {}, None
    for ev in provider.events(list(lines)):
        if ev["kind"] == "meta":
            seen["rate_limit"] = ev["rate_limit"]
        elif ev["kind"] == "limit":
            seen["notice"] = {k: v for k, v in ev.items() if k != "kind"}
        elif ev["kind"] == "result":
            result = ev
    assert result is not None, "the turn ended without a result"
    return provider.limit(result, seen), result


SESSION = "You've hit your session limit · resets 3:30pm (UTC)"
FIVE_HOUR = "You've hit your 5-hour limit · resets 3:30pm (UTC)"


# -- Claude: what counts --

def test_a_five_hour_window_used_up_is_a_limit_with_the_time_the_event_gave():
    got, result = _run(providers.Claude("x"),
                       _event(status="rejected", resetsAt=RESET, rateLimitType="five_hour", isUsingOverage=False),
                       _assistant(SESSION), _result(SESSION))
    assert got == rest.Limit("limit", "five_hour", RESET, SESSION, rest.RAISE_PAGES["claude"])
    assert result["api_error_status"] == 429 and result["ok"] is False


def _real_319(text=FIVE_HOUR, **info):
    """What 2.1.319 sent for a used-up five-hour window (captured in the headless test): the event with its
    unified windows, an assistant message with the code and its params, and the result with the code."""
    info = {"status": "rejected", "resetsAt": RESET, "rateLimitType": "five_hour", "overageStatus": "rejected",
            "overageDisabledReason": "org_level_disabled", "isUsingOverage": False,
            "unifiedWindows": {"five_hour": {"utilization": 1, "resetsAt": RESET}}, **info}
    return (_event(**info),
            _assistant(text, api_error="usage_limit_reached", api_error_status=429, request_id="req_1",
                       api_error_params={"rate_limit_info": info}),
            _result(text, api_error="usage_limit_reached"))


def test_a_used_up_plan_as_claude_code_2_1_319_sends_it_is_a_limit_with_the_time_the_event_gave():
    got, result = _run(providers.Claude("x"), *_real_319())
    assert got == rest.Limit("limit", "five_hour", RESET, FIVE_HOUR, rest.RAISE_PAGES["claude"])
    assert result["api_error"] == "usage_limit_reached" and result["api_error_status"] == 429


def test_that_assistant_message_is_the_accounts_notice_and_not_what_the_agent_said():
    events = list(providers.Claude("x").events(list(_real_319())))
    assert [e["kind"] for e in events] == ["meta", "limit", "result"]   # no "text"
    assert events[1]["api_error"] == "usage_limit_reached" and events[1]["text"] == FIVE_HOUR


@pytest.mark.parametrize("said, kind", [
    ("You've hit your 5-hour limit · resets 3:30pm (UTC)", "five_hour"),
    ("You've hit your weekly limit · resets 3:30pm (UTC)", "seven_day"),
    ("You've hit your Opus limit · resets 3:30pm (UTC)", "seven_day_opus"),
    ("You've hit your Sonnet limit · resets 3:30pm (UTC)", "seven_day_sonnet"),
])
def test_the_window_is_named_by_the_words_when_there_is_no_event(said, kind):
    got, _ = _run(providers.Claude("x"), _assistant(said, api_error="usage_limit_reached"),
                  _result(said, api_error="usage_limit_reached"))
    assert got.why == "limit" and got.kind == kind and got.until == RESET


def test_a_code_this_version_does_not_know_needs_the_words_and_not_the_window_alone():
    claude = providers.Claude("x")
    said = "Something about your plan changed"
    window = _event(status="rejected", resetsAt=RESET, rateLimitType="five_hour", isUsingOverage=False)
    got, _ = _run(claude, window, _assistant(said, api_error="plan_changed"), _result(said, api_error="plan_changed"))
    assert got is None
    got, _ = _run(claude, window, _assistant(FIVE_HOUR, api_error="plan_changed"),
                  _result(FIVE_HOUR, api_error="plan_changed"))
    assert got == rest.Limit("limit", "five_hour", RESET, FIVE_HOUR, rest.RAISE_PAGES["claude"])


def test_the_time_is_read_from_the_words_when_there_is_no_event_as_with_an_api_key():
    said = "API Error: Request rejected (429) · You've hit your weekly limit · resets Oct 3, 9am (UTC)"
    got, _ = _run(providers.Claude("x"), _assistant(said), _result(said))
    assert got.why == "limit" and got.kind == "seven_day"
    assert got.until == datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("window, kind", [("seven_day", "seven_day"), ("seven_day_opus", "seven_day_opus"),
                                          ("seven_day_sonnet", "seven_day_sonnet")])
def test_the_window_the_event_names_is_the_kind(window, kind):
    got, _ = _run(providers.Claude("x"),
                  _event(status="rejected", resetsAt=RESET, rateLimitType=window, isUsingOverage=False),
                  _assistant(SESSION), _result(SESSION))
    assert got.kind == kind


def test_spending_gone_is_a_spend_limit_and_a_time_is_only_kept_when_there_is_one():
    said = "You're out of extra usage · your organization's monthly spend limit was reached"
    got, _ = _run(providers.Claude("x"),
                  _event(status="rejected", rateLimitType="overage", isUsingOverage=False),
                  _assistant(said, error="billing_error"), _result(said))
    assert (got.why, got.kind, got.until) == ("spend", "overage", None)
    with_time, _ = _run(providers.Claude("x"),
                        _event(status="rejected", rateLimitType="overage", overageResetsAt=RESET, isUsingOverage=False),
                        _assistant(said, error="billing_error"), _result(said))
    assert (with_time.why, with_time.until) == ("spend", RESET)


def test_a_credit_balance_that_is_too_low_is_a_spending_limit_whatever_the_status():
    said = "Your credit balance is too low to access the API"
    # The API answers 400 for it; the CLI calls it a billing error, which is how it is known.
    got, _ = _run(providers.Claude("x"), _assistant(said, error="billing_error"), _result(said, status=400))
    assert got.why == "spend" and got.until is None
    got, _ = _run(providers.Claude("x"), _assistant(said, error="billing_error"), _result(said))
    assert got.why == "spend"
    # The same words with no sign of a refusal are only words.
    got, _ = _run(providers.Claude("x"), _result(said, status=400, reason="completed"))
    assert got is None


def test_a_time_already_gone_is_not_an_until():
    old = _event(status="rejected", resetsAt=AT - 60, rateLimitType="five_hour", isUsingOverage=False)
    got, _ = _run(providers.Claude("x"), old, _assistant("You've hit your session limit"), _result(SESSION))
    assert got.until is None or got.until > AT


def test_the_page_to_raise_it_is_the_providers_own_when_its_message_names_one():
    said = SESSION + " · Upgrade at https://claude.ai/upgrade"
    got, _ = _run(providers.Claude("x"), _assistant(said), _result(said))
    assert got.url == "https://claude.ai/upgrade"


# -- Claude: what does not --

def test_a_window_that_reads_rejected_while_credits_carry_on_is_not_a_refusal():
    # The CLI sends the event ahead of a turn that then works, or that fails for another reason.
    got, _ = _run(providers.Claude("x"),
                  _event(status="rejected", rateLimitType="five_hour", resetsAt=RESET, isUsingOverage=True),
                  _result("The model overloaded", status=500))
    assert got is None
    got, _ = _run(providers.Claude("x"),
                  _event(status="rejected", rateLimitType="five_hour", resetsAt=RESET, isUsingOverage=True),
                  _assistant("Request rejected"), _result("API Error: Request rejected (429)"))
    assert got is None   # the window alone, with paid credits carrying on, is not the account being out


def test_an_allowed_window_with_a_warning_never_rests_the_machine():
    got, _ = _run(providers.Claude("x"),
                  _event(status="allowed_warning", rateLimitType="five_hour", resetsAt=RESET, utilization=0.84),
                  _result("Something else broke", status=500))
    assert got is None
    got, _ = _run(providers.Claude("x"), _event(status="allowed", overageStatus="rejected"), _result("Boom", status=500))
    assert got is None


def test_an_entitlement_check_is_not_the_quota():
    said = "This model requires usage credits"
    got, _ = _run(providers.Claude("x"), _assistant(said, api_error="model_requires_usage_credits"),
                  _result(said, api_error="model_requires_usage_credits"))
    assert got is None


@pytest.mark.parametrize("code, said", [
    ("model_requires_usage_credits", "This model requires usage credits · run /extra-usage"),
    ("long_context_credits_required", "API Error: Usage credits required for 1M context · run /extra-usage"),
    ("model_requires_usage_credits", "You've hit your Fable limit · resets 3:30pm (UTC)"),
])
def test_an_entitlement_check_is_not_the_quota_even_when_it_is_a_429_with_a_rejected_window(code, said):
    got, _ = _run(providers.Claude("x"),
                  _event(status="rejected", resetsAt=RESET, rateLimitType="seven_day_overage_included",
                         isUsingOverage=False),
                  _assistant(said, api_error=code), _result(said, api_error=code))
    assert got is None


@pytest.mark.parametrize("said", [
    "Server is temporarily limiting requests (not your usage limit) · Try again in a moment",
    "API Error: 429 under high load; try again in a minute",
    "Anthropic's API is temporarily overloaded",
])
def test_the_throttle_and_a_busy_moment_are_not_the_account(said):
    got, _ = _run(providers.Claude("x"), _assistant(said), _result(said))
    assert got is None


def test_a_529_is_a_busy_moment():
    got, _ = _run(providers.Claude("x"), _assistant("Overloaded", error="rate_limit"),
                  _result("API Error: 529 Overloaded", status=529))
    assert got is None


def test_the_context_window_and_a_spent_budget_flag_are_not_the_account():
    got, _ = _run(providers.Claude("x"), _result("Context window is full", status=None, reason="blocking_limit"))
    assert got is None
    got, _ = _run(providers.Claude("x"), _line(type="result", subtype="error_max_budget_usd", is_error=True,
                                               num_turns=2, result="Reached the maximum budget", session_id="s"))
    assert got is None


def test_a_turn_that_worked_is_never_a_limit():
    p = providers.Claude("x")
    ok = _line(type="result", subtype="success", is_error=False, result="Done", session_id="s", num_turns=1)
    result = next(e for e in p.events([ok]) if e["kind"] == "result")
    assert p.limit(result, {"rate_limit": {"status": "rejected", "isUsingOverage": False}}) is None


def test_a_refusal_with_no_words_of_a_quota_and_no_rejected_window_is_an_error_not_a_rest():
    got, _ = _run(providers.Claude("x"), _result("API Error: 429 Too Many Requests"))
    assert got is None


# -- Claude: a CLI that waits out the limit instead of ending the turn --

def _waiting(info, delay_ms=7_200_000, http=429):
    p = providers.Claude("x")
    seen = {}
    for ev in p.events([_event(**info)]):
        seen["rate_limit"] = ev["rate_limit"]
    retry = next(p.events([_line(type="system", subtype="api_retry", error_status=http, retry_delay_ms=delay_ms,
                                 attempt=1)]))
    return p.waiting(retry, seen), retry


def test_a_long_retry_with_a_rejected_window_is_the_limit_being_waited_out():
    got, retry = _waiting(dict(status="rejected", resetsAt=RESET, rateLimitType="five_hour", isUsingOverage=False))
    assert retry == {"kind": "retry", "status": 429, "attempt": 1, "delay_ms": 7_200_000}
    assert got == rest.Limit("limit", "five_hour", RESET, "", rest.RAISE_PAGES["claude"])


def test_the_time_is_the_wait_when_the_event_gave_none_and_spending_is_spending():
    got, _ = _waiting(dict(status="rejected", rateLimitType="overage", isUsingOverage=False))
    assert got.why == "spend" and abs(got.until - (AT + 7200)) < 1


@pytest.mark.parametrize("delay, http, info", [
    (30_000, 429, {"status": "rejected", "rateLimitType": "five_hour"}),          # a busy moment's back-off
    (7_200_000, 529, {"status": "rejected", "rateLimitType": "five_hour"}),
    (7_200_000, 429, {"status": "allowed", "rateLimitType": "five_hour"}),
    (7_200_000, 429, {"status": "rejected", "isUsingOverage": True}),             # paid credits carry on
    (None, 429, {"status": "rejected"}),
])
def test_a_short_retry_or_a_window_that_is_not_rejected_is_only_a_retry(delay, http, info):
    got, _ = _waiting(info, delay, http)
    assert got is None


def test_a_retry_with_no_event_before_it_is_only_a_retry():
    p = providers.Claude("x")
    retry = next(p.events([_line(type="system", subtype="api_retry", error_status=429, retry_delay_ms=7_200_000)]))
    assert p.waiting(retry, {}) is None
    assert providers.Codex("x").waiting(retry, {}) is None


# -- Claude: the events --

def test_the_refusal_is_not_the_agents_reply_and_the_event_and_retry_notices_are_kept():
    p = providers.Claude("x")
    events = list(p.events([
        _event(status="rejected", resetsAt=RESET, rateLimitType="five_hour", isUsingOverage=False),
        _assistant(SESSION),
        _line(type="system", subtype="api_retry", error_status=429, attempt=2),
        _line(type="system", subtype="api_retry", error_status=401, attempt=1),
        _result(SESSION)]))
    kinds = [e["kind"] for e in events]
    assert kinds == ["meta", "limit", "retry", "result"]   # no "text": it is not what the agent said
    assert events[0]["rate_limit"]["rateLimitType"] == "five_hour"
    assert events[1]["text"] == SESSION and events[1]["error"] == "rate_limit"
    assert events[2] == {"kind": "retry", "status": 429, "attempt": 2, "delay_ms": None}
    assert not p.is_progress(_line(type="system", subtype="api_retry"))   # a retry is not progress


def test_an_assistant_error_that_is_an_entitlement_check_is_left_to_the_ordinary_path():
    p = providers.Claude("x")
    events = list(p.events([_assistant("This model requires usage credits", api_error="model_requires_usage_credits")]))
    assert [e["kind"] for e in events] == ["text"]


# -- Codex --

def _codex(*lines):
    p = providers.Codex("x")
    events = [e for line in lines for e in p.parse(json.dumps(line))]
    result = next(e for e in events if e["kind"] == "result")
    return p.limit(result, {}), events


def test_codex_out_of_its_plan_is_a_limit_with_its_local_time():
    said = ("You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
            "https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 3:45 PM.")
    got, events = _codex({"type": "turn.started"}, {"type": "error", "message": said},
                         {"type": "turn.failed", "error": {"message": said}})
    assert got.why == "limit" and got.until == datetime(2026, 10, 1, 15, 45, tzinfo=timezone.utc).timestamp()
    assert got.url == "https://chatgpt.com/explore/pro"
    assert [e["kind"] for e in events] == ["result"]   # the same text as an error notice would be shown twice


def test_codex_names_a_date_when_the_time_is_not_today():
    said = "You’ve hit your usage limit. Try again at Oct 2nd, 2026 3:45 PM."
    got, _ = _codex({"type": "turn.failed", "error": {"message": said}})
    assert got.until == datetime(2026, 10, 2, 15, 45, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("said", ["You're out of credits. Add more to keep going.", "Your spend cap was reached.",
                                  "Quota exceeded for this account"])
def test_codex_with_no_credits_is_a_spending_limit_and_may_have_no_time(said):
    got, _ = _codex({"type": "turn.failed", "error": {"message": said}})
    assert got.why == "spend" and got.until is None


def test_codex_rate_limit_exceeded_is_a_retried_throttle_and_stays_a_notice():
    notice = {"type": "error", "message": "rate limit exceeded: slow down, retrying 2/5"}
    got, events = _codex(notice, {"type": "turn.failed", "error": {"message": "rate limit exceeded: gave up"}})
    assert got is None
    assert [e["kind"] for e in events] == ["text", "result"]


def test_a_codex_turn_that_completed_is_never_a_limit():
    p = providers.Codex("x")
    assert p.limit({"kind": "result", "ok": True, "text": "You've hit your usage limit (quoted)"}, {}) is None


def test_the_base_provider_and_the_shell_never_rest_the_machine():
    result = {"kind": "result", "ok": False, "text": "You've hit your usage limit"}
    assert providers.Shell().limit(result, {}) is None
    assert providers.Fake("x").limit(result, {}) is None
