"""Tests for brickfolio.llm and brickfolio.generator (Prompt 9).

Offline by construction: the repair loop is driven by a scripted
FakeChatClient (never a socket), and ChatClient itself is exercised
through a recording fake opener -- the same test seam the vision
client's tests use.  Coverage map:

* generator: first-try success; the round-1-bad / round-2-fixed repair
  loop; parse-failure repair; markdown-fenced and prose-only replies;
  max-rounds exhaustion; transport failure; shape-mode substitution;
  the system prompt's inventory listing.
* llm: request URL/method/body, temperature, env-var defaults, error
  paths (scheme-less URL, HTTP error, non-2xx fake response, network
  error, non-JSON, missing choices/content).
"""

from __future__ import annotations

import email.message
import io
import json
import urllib.error

import pytest

from brickfolio.generator import (
    GenerationResult,
    build_system_prompt,
    generate_build,
)
from brickfolio.llm import DEFAULT_BASE_URL, DEFAULT_MODEL, ChatClient, LLMError
from brickfolio.models import InventoryItem

# The pile: 2x red 2x4 brick, 1x black 2x2 brick, 1x red 1x1 brick --
# the same stock the Prompt-8 validator tests use, so the validator's
# exact error strings here match its documented behavior.
INVENTORY = [
    InventoryItem("3001", "4", 2),
    InventoryItem("3003", "0", 1),
    InventoryItem("3005", "4", 1),
]


def part_line(part_num, color="4", x=0, y=0, z=0):
    """One canonical LDraw type-1 line (identity rotation, 4x3 matrix)."""
    return f"1 {color} {x} {y} {z} 1 0 0 0 1 0 0 0 1 0 0 1 {part_num}.dat"


# Exactly buildable from INVENTORY: consumes every last brick.
GOOD_MODEL = "\n".join(
    [
        part_line("3001", "4"),                # two red 2x4s, side by side
        part_line("3001", "4", x=80),
        part_line("3003", "0", y=24),          # black 2x2 on top
        part_line("3005", "4", y=24, x=-20),   # red 1x1 on top
    ]
)

# Uses one part the pile does not own -- the round-1 mistake the
# scripted repair loop must catch and feed back verbatim.
BAD_MODEL = "\n".join(
    [
        part_line("3001", "4"),
        part_line("3001", "4", x=80),
        part_line("9999", "4"),  # not in the pile
    ]
)

GOOD_USAGE = [("3001", "4", 2), ("3003", "0", 1), ("3005", "4", 1)]
BAD_VALIDATION_ERROR = "part 9999 color 4: needed 1, have 0"


# --- fakes (module boundaries, per the project testing strategy) --------------


class FakeChatClient:
    """Scripted stand-in for ChatClient: returns queued replies in order.

    Records every call's messages so tests can assert what the repair
    loop actually fed back to the LLM.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, temperature=0.6):
        self.calls.append([dict(m) for m in messages])
        if not self.replies:
            raise AssertionError("FakeChatClient ran out of scripted replies")
        return self.replies.pop(0)


class DeadChatClient:
    """A client whose transport is down: chat() always raises LLMError."""

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def chat(self, messages, temperature=0.6):
        self.calls += 1
        raise self.exc


class FakeResponse:
    """The slice of urllib's response API ChatClient relies on."""

    def __init__(self, body=b"", status=200):
        self.body = body
        self.status = status

    def read(self):
        return self.body


class RecordingOpener:
    """Fake transport: records each request, then replies with a scripted
    response (or raises a scripted exception).

    Mirrors the duck type ChatClient expects of a real opener:
    ``open(request, timeout=...)``.
    """

    def __init__(self, body=b"", status=200, exc=None):
        self.response = FakeResponse(body, status)
        self.exc = exc
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append({"request": request, "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        return self.response


# --- generator: system prompt -------------------------------------------------


def test_system_prompt_contains_inventory_listing():
    prompt = build_system_prompt(INVENTORY)
    # The compact part:quantity listing, one line per (part, color).
    assert "3001 color 4: 2" in prompt
    assert "3003 color 0: 1" in prompt
    assert "3005 color 4: 1" in prompt
    # The hard constraint and the output format are both spelled out.
    assert "ONLY the parts listed below" in prompt
    assert "LDraw" in prompt
    assert "1 <color> <x> <y> <z> <matrix values> <part>.dat" in prompt


def test_system_prompt_aggregates_duplicate_lines():
    split = [
        InventoryItem("3001", "4", 1),
        InventoryItem("3001", "4", 1),
        InventoryItem("3003", "0", 1),
    ]
    prompt = build_system_prompt(split)
    assert "3001 color 4: 2" in prompt
    assert prompt.count("3001 color 4:") == 1


def test_system_prompt_listing_is_deterministic():
    shuffled = [INVENTORY[2], INVENTORY[0], INVENTORY[1]]
    assert build_system_prompt(shuffled) == build_system_prompt(INVENTORY)


# --- generator: the ask/repair loop -------------------------------------------


def test_valid_on_first_round():
    client = FakeChatClient(GOOD_MODEL)
    result = generate_build(INVENTORY, "car", client)
    assert result.valid is True
    assert result.rounds == 1
    assert result.errors == []
    assert result.usage == GOOD_USAGE
    assert result.model is not None
    assert len(result.model.parts) == 4
    assert len(client.calls) == 1
    # The wire conversation: system prompt with the listing, then the ask.
    assert client.calls[0][0]["role"] == "system"
    assert "3001 color 4: 2" in client.calls[0][0]["content"]
    assert client.calls[0][1]["role"] == "user"
    assert "car" in client.calls[0][1]["content"]


def test_repair_loop_fixes_unavailable_part():
    # Round 1 uses a part the pile does not own; round 2 fixes it.
    client = FakeChatClient(BAD_MODEL, GOOD_MODEL)
    result = generate_build(INVENTORY, "a small house", client)
    assert result.valid is True
    assert result.rounds == 2
    assert result.errors == []
    assert result.usage == GOOD_USAGE
    assert result.model is not None
    assert len(result.model.parts) == 4

    # The round-2 feedback quoted the validator's error verbatim.
    feedback = client.calls[1][-1]["content"]
    assert BAD_VALIDATION_ERROR in feedback
    # And the conversation kept its full history so the LLM saw its own
    # faulty output: system, ask, faulty assistant reply, repair ask.
    roles = [m["role"] for m in client.calls[1]]
    assert roles == ["system", "user", "assistant", "user"]
    assert client.calls[1][2]["content"] == BAD_MODEL
    # The system prompt (with the inventory listing) rode along on every
    # round, not just the first.
    assert client.calls[1][0]["role"] == "system"
    assert "3001 color 4: 2" in client.calls[1][0]["content"]


def test_max_rounds_exhaustion_reports_errors():
    client = FakeChatClient(BAD_MODEL, BAD_MODEL, BAD_MODEL)
    result = generate_build(INVENTORY, "rocket", client, max_rounds=3)
    assert result.valid is False
    assert result.rounds == 3
    # errors are the FINAL round's problems -- fixed-then-broken history
    # is not accumulated, so nothing is listed twice.
    assert result.errors == [BAD_VALIDATION_ERROR]
    # The last parsed model is still handed back for inspection/dumping.
    assert result.model is not None
    assert result.model.parts[0].part_num == "3001"


def test_parse_error_gets_repaired():
    broken = "1 4 0 0 0"  # truncated type-1 line: unparseable
    client = FakeChatClient(broken, GOOD_MODEL)
    result = generate_build(INVENTORY, "boat", client)
    assert result.valid is True
    assert result.rounds == 2
    feedback = client.calls[1][-1]["content"]
    # The parser's own message is quoted so the LLM can find the line.
    assert "not valid LDraw" in feedback
    assert "malformed part placement" in feedback


def test_markdown_fenced_reply_is_extracted():
    fenced = (
        "Sure! Here's a tiny house:\n"
        "```ldr\n" + GOOD_MODEL + "\n```\n"
        "Enjoy building!"
    )
    client = FakeChatClient(fenced)
    result = generate_build(INVENTORY, "tiny house", client)
    assert result.valid is True
    assert result.rounds == 1
    assert result.model is not None
    assert len(result.model.parts) == 4
    assert result.usage == GOOD_USAGE


def test_prose_only_reply_triggers_repair():
    client = FakeChatClient("I would love to help design a spaceship!", GOOD_MODEL)
    result = generate_build(INVENTORY, "spaceship", client)
    # Prose parses "successfully" into an empty model, which must count
    # as a problem -- otherwise the loop could return valid-with-nothing.
    assert result.valid is True
    assert result.rounds == 2
    feedback = client.calls[1][-1]["content"]
    assert "no part placements" in feedback


def test_transport_failure_yields_invalid_result():
    client = DeadChatClient(LLMError("connection refused"))
    result = generate_build(INVENTORY, "castle", client)
    # A dead server ends the run as a result, not an exception -- the
    # CLI prints a report either way.
    assert result.valid is False
    assert result.model is None
    assert result.rounds == 1
    assert result.usage == []
    assert result.errors == ["LLM call failed: connection refused"]
    assert client.calls == 1  # no pointless retries within one run


def test_shape_mode_allows_color_substitution():
    # Asks for the 2x4 brick in black (color 0) -- not stocked in that
    # color, but shape mode only counts shapes; the warning is advisory.
    mono = "\n".join(
        [
            part_line("3001", "0"),
            part_line("3001", "0", x=80),
            part_line("3003", "0"),
            part_line("3005", "4"),
        ]
    )
    client = FakeChatClient(mono)
    result = generate_build(INVENTORY, "monochrome house", client, mode="shape")
    assert result.valid is True
    assert result.rounds == 1
    assert result.errors == []
    assert result.usage[0] == ("3001", "0", 2)


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        generate_build(INVENTORY, "x", FakeChatClient(GOOD_MODEL), mode="bogus")


def test_non_positive_rounds_rejected():
    with pytest.raises(ValueError):
        generate_build(INVENTORY, "x", FakeChatClient(), max_rounds=0)


def test_result_valid_is_derived_from_errors():
    # House style (see ValidationReport): valid can never disagree with
    # errors -- not even for a hand-built result.
    assert GenerationResult(errors=["boom"]).valid is False
    assert GenerationResult(model=None, rounds=2, valid=False, errors=[]).valid is True


# --- llm: ChatClient over a fake opener ----------------------------------------


def ok_body(content):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


def test_chat_round_trip_sends_openai_body_and_returns_content():
    opener = RecordingOpener(ok_body("1 0 0 ..."))
    client = ChatClient(base_url="http://llm.local:1234", model="tinyllm", opener=opener)
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]

    assert client.chat(messages) == "1 0 0 ..."

    request = opener.requests[0]["request"]
    assert request.full_url == "http://llm.local:1234/v1/chat/completions"
    assert request.get_method() == "POST"
    assert request.headers["Content-type"] == "application/json"
    assert opener.requests[0]["timeout"] == 60
    assert json.loads(request.data.decode("utf-8")) == {
        "model": "tinyllm",
        "messages": messages,
        "temperature": 0.6,
    }


def test_chat_temperature_is_forwarded():
    opener = RecordingOpener(ok_body("x"))
    client = ChatClient(base_url="http://x", model="m", opener=opener)
    client.chat([{"role": "user", "content": "q"}], temperature=0.1)
    assert json.loads(opener.requests[0]["request"].data)["temperature"] == 0.1


def test_chat_empty_content_is_returned_not_raised():
    # An LLM that answered nothing is a repair-loop problem, not a
    # wire-format problem.
    opener = RecordingOpener(ok_body(""))
    client = ChatClient(base_url="http://x", model="m", opener=opener)
    assert client.chat([{"role": "user", "content": "q"}]) == ""


def test_chat_http_error_raises_llm_error():
    http_error = urllib.error.HTTPError(
        "http://x/v1/chat/completions",
        500,
        "Internal Server Error",
        email.message.Message(),
        io.BytesIO(b'{"error": "out of memory"}'),
    )
    opener = RecordingOpener(exc=http_error)
    client = ChatClient(base_url="http://x", model="m", opener=opener)
    with pytest.raises(LLMError) as info:
        client.chat([{"role": "user", "content": "q"}])
    assert "500" in str(info.value)
    assert "out of memory" in str(info.value)


def test_chat_non_2xx_fake_response_raises_llm_error():
    # Injected openers hand the response back regardless of status; the
    # client must not treat a 503 body as a completion payload.
    opener = RecordingOpener(ok_body("x"), status=503)
    client = ChatClient(base_url="http://x", model="m", opener=opener)
    with pytest.raises(LLMError) as info:
        client.chat([{"role": "user", "content": "q"}])
    assert "503" in str(info.value)


def test_chat_network_error_raises_llm_error():
    opener = RecordingOpener(exc=urllib.error.URLError(ConnectionRefusedError()))
    client = ChatClient(base_url="http://x", model="m", opener=opener)
    with pytest.raises(LLMError) as info:
        client.chat([{"role": "user", "content": "q"}])
    assert str(info.value).startswith("LLM request failed:")


def test_chat_non_json_reply_raises_llm_error():
    opener = RecordingOpener(b"<html>502 Bad Gateway</html>")
    client = ChatClient(base_url="http://x", model="m", opener=opener)
    with pytest.raises(LLMError) as info:
        client.chat([{"role": "user", "content": "q"}])
    assert "non-JSON" in str(info.value)


def test_chat_missing_choices_raises_llm_error():
    opener = RecordingOpener(b'{"id": "abc", "object": "chat.completion"}')
    client = ChatClient(base_url="http://x", model="m", opener=opener)
    with pytest.raises(LLMError) as info:
        client.chat([{"role": "user", "content": "q"}])
    assert "no choices" in str(info.value)


def test_chat_missing_content_raises_llm_error():
    opener = RecordingOpener(b'{"choices": [{"message": {"role": "assistant"}}]}')
    client = ChatClient(base_url="http://x", model="m", opener=opener)
    with pytest.raises(LLMError) as info:
        client.chat([{"role": "user", "content": "q"}])
    assert "no message content" in str(info.value)


# --- llm: configuration defaults ------------------------------------------------


def test_env_defaults_used_when_args_omitted(monkeypatch):
    monkeypatch.setenv("BRICKFOLIO_LLM_BASE_URL", "http://jetson:8080/")
    monkeypatch.setenv("BRICKFOLIO_LLM_MODEL", "qwen3-4b")
    opener = RecordingOpener(ok_body("ok"))
    client = ChatClient(opener=opener)
    assert client.base_url == "http://jetson:8080"  # trailing / normalized
    assert client.model == "qwen3-4b"
    client.chat([{"role": "user", "content": "hi"}])
    assert opener.requests[0]["request"].full_url == (
        "http://jetson:8080/v1/chat/completions"
    )
    assert json.loads(opener.requests[0]["request"].data)["model"] == "qwen3-4b"


def test_builtin_defaults_without_env(monkeypatch):
    monkeypatch.delenv("BRICKFOLIO_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("BRICKFOLIO_LLM_MODEL", raising=False)
    client = ChatClient()
    assert client.base_url == DEFAULT_BASE_URL
    assert client.model == DEFAULT_MODEL
    assert client.endpoint == f"{DEFAULT_BASE_URL}/v1/chat/completions"


def test_explicit_args_beat_env(monkeypatch):
    monkeypatch.setenv("BRICKFOLIO_LLM_BASE_URL", "http://env:1")
    monkeypatch.setenv("BRICKFOLIO_LLM_MODEL", "env-model")
    client = ChatClient(base_url="http://explicit:2", model="explicit-model")
    assert client.base_url == "http://explicit:2"
    assert client.model == "explicit-model"


def test_endpoint_normalization():
    # Trailing slashes and an /v1 suffix are tolerated; the route is
    # never doubled.
    plain = ChatClient(base_url="http://h:8080")
    assert plain.endpoint == "http://h:8080/v1/chat/completions"
    assert ChatClient(base_url="http://h:8080/").endpoint == plain.endpoint
    assert ChatClient(base_url="http://h:8080/v1").endpoint == plain.endpoint


def test_base_url_without_scheme_is_rejected_immediately():
    # Fail at construction, not later as urllib's "unknown url type".
    with pytest.raises(LLMError) as info:
        ChatClient(base_url="llm.local:8080")
    assert "http://" in str(info.value)