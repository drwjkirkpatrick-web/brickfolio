"""Minimal OpenAI-compatible chat client for brickfolio (Prompt 9).

The inventory-constrained generator (brickfolio.generator) needs exactly
one capability from a language model: turn a list of chat messages into
a string of text.  Rather than adopt an SDK -- the project rule is
stdlib-only -- this module speaks the OpenAI *chat completions* wire
format directly with urllib::

    POST {base_url}/v1/chat/completions
    Content-Type: application/json
    {"model": ..., "messages": [{"role": ..., "content": ...}], ...}

Every local server implements that contract (llama.cpp's llama-server,
LM Studio, Ollama's OpenAI endpoint, vLLM), which is what makes "an LLM
designs a build from your pile" possible on a Jetson-class box with no
cloud account and no new dependency.

Configuration defaults come from the environment, because a deployment
is a machine fact, not a code fact:

    BRICKFOLIO_LLM_BASE_URL   e.g. http://orin.local:8080 (llama-server)
    BRICKFOLIO_LLM_MODEL      name of the served model

NOTE: tests never touch the network.  ``opener=`` is the same test seam
as brickfolio.vision.brickognize: any object with
``open(request, timeout=...)`` stands in for a real transport.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Sequence

__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "LLMError", "ChatClient"]

# llama-server's out-of-the-box address: the brickfolio deployment story
# is "a local model on the Jetson", so the no-configuration fallback is
# a local server, not a cloud endpoint.
DEFAULT_BASE_URL = "http://127.0.0.1:8080"

# Last-resort model name.  A chat-completions server requires *some*
# model string in the body; the real name belongs in BRICKFOLIO_LLM_MODEL.
DEFAULT_MODEL = "brickfolio"

# The one route appended to any base URL (OpenAI's chat-completions path).
_CHAT_PATH = "/v1/chat/completions"


class LLMError(Exception):
    """A chat call failed: bad configuration, HTTP error, network or
    timeout trouble, or a reply that is not a usable chat-completions
    payload.

    WHY one error type: the generator's repair loop catches exactly this
    and turns it into a failed
    :class:`~brickfolio.generator.GenerationResult`, while genuine code
    bugs still surface as other exceptions.  (Same design as
    BrickognizeError in the vision client.)
    """


class ChatClient:
    """A one-method OpenAI-compatible chat client over stdlib urllib.

    Args:
        base_url: ``scheme://host[:port]`` of the server.  A trailing
            ``/`` or a ``/v1`` suffix is tolerated (see
            :func:`_chat_endpoint`).  ``None`` reads
            ``BRICKFOLIO_LLM_BASE_URL`` from the environment, falling
            back to :data:`DEFAULT_BASE_URL` (a local llama-server).
        model: model name sent in the request body.  ``None`` reads
            ``BRICKFOLIO_LLM_MODEL`` from the environment, falling back
            to :data:`DEFAULT_MODEL`.
        timeout: seconds to wait for connect+read.
        opener: optional transport replacement (any object with
            ``open(request, timeout=...)``).  Production leaves it None;
            tests inject fakes so no socket is ever opened.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60,
        opener: Any = None,
    ) -> None:
        if base_url is None:
            base_url = os.environ.get("BRICKFOLIO_LLM_BASE_URL") or DEFAULT_BASE_URL
        if model is None:
            model = os.environ.get("BRICKFOLIO_LLM_MODEL") or DEFAULT_MODEL
        if not base_url.startswith(("http://", "https://")):
            # WHY fail here and not at request time: a scheme-less URL
            # would surface later as urllib's cryptic "unknown url type".
            raise LLMError(
                f"LLM base_url must start with http:// or https://, got {base_url!r}"
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.endpoint = _chat_endpoint(self.base_url)
        self._opener = opener

    def chat(
        self, messages: Sequence[Dict[str, Any]], temperature: float = 0.6
    ) -> str:
        """Send one chat turn; return the assistant's reply text.

        Args:
            messages: OpenAI-style ``[{"role": ..., "content": ...}]``.
            temperature: sampling randomness; 0.6 is the creative-but-
                sane default for design work.

        Returns:
            The first choice's ``message.content``.  An empty string is
            returned, not raised: an LLM that answered nothing is a
            repair-loop problem, not a wire-format problem.

        Raises:
            LLMError: the request failed (bad base URL, HTTP error,
                timeout, network trouble) or the reply is not a
                chat-completions payload.
        """
        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
        }
        try:
            body = json.dumps(payload).encode("utf-8")
        except TypeError as exc:
            raise LLMError(f"chat messages are not JSON-serializable: {exc}") from exc

        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "brickfolio-generator/0.1",
            },
            method="POST",
        )
        # WHY set Content-Length explicitly: the default opener would add
        # it inside its HTTP handler, but a Request handed straight to an
        # injected (fake) opener never passes through that handler.
        request.add_header("Content-Length", str(len(body)))

        if self._opener is None:
            # NOTE: attribute access (not "from urllib.request import
            # build_opener") so tests can monkeypatch urllib.request.build_opener
            # to verify this default wiring without opening sockets.
            opener: Any = urllib.request.build_opener()
        else:
            opener = self._opener

        try:
            response = opener.open(request, timeout=self.timeout)
            raw = response.read()
            status = _status_of(response)
        except urllib.error.HTTPError as exc:
            raise LLMError(_http_error_message(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # NOTE: URLError wraps most transport failures (including
            # timeouts); TimeoutError/OSError catch the unwrapped ones.
            # HTTPError is a URLError subclass, so it must be caught first.
            raise LLMError(f"LLM request failed: {_reason_of(exc)}") from exc

        # The default opener raises HTTPError for 4xx/5xx itself; this
        # check covers injected openers that hand the response back
        # regardless of status.
        if status is not None and not 200 <= status < 300:
            raise LLMError(f"LLM returned HTTP {status} for {self.endpoint}")

        return _content_of(raw)


# --- private helpers ---------------------------------------------------------


def _chat_endpoint(base_url: str) -> str:
    """Append the chat-completions path to a normalized base URL.

    WHY tolerate a trailing ``/v1``: providers document base URLs both
    with and without the version prefix; appending blindly would turn
    ``http://host:8080/v1`` into ``.../v1/v1/chat/completions``.
    """
    base = base_url.rstrip("/")
    if base.casefold().endswith("/v1"):
        base = base[: -len("/v1")]
    return base + _CHAT_PATH


def _status_of(response: Any) -> Optional[int]:
    """Best-effort HTTP status from a response (real or fake); None if unknowable."""
    status: Any = getattr(response, "status", None)
    if status is None:
        getter = getattr(response, "getcode", None)
        if callable(getter):
            try:
                status = getter()
            except Exception:
                return None
    if status is None:
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    """Human-readable message that keeps the server's error body, if any."""
    try:
        detail = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        detail = ""
    suffix = f" ({detail})" if detail else ""
    return f"LLM returned HTTP {exc.code} {exc.reason}{suffix}"


def _reason_of(exc: BaseException) -> Any:
    """URLError stores its cause in .reason; other errors speak for themselves."""
    reason = getattr(exc, "reason", None)
    return reason if reason is not None else exc


def _content_of(raw: bytes) -> str:
    """Extract the reply text from a chat-completions response body.

    Defensive on purpose: a model server is an external input, and the
    generator must see LLMError -- never a KeyError -- when the payload
    shape is off.
    """
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = text[:120].strip() or repr(raw[:120])
        raise LLMError(
            f"LLM returned non-JSON response ({exc.msg}): {snippet!r}"
        ) from exc
    if not isinstance(payload, dict):
        raise LLMError(f"LLM reply is not a JSON object: {text[:120]!r}")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LLMError(f"LLM reply has no choices: {text[:120]!r}")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise LLMError(f"LLM reply has no message content: {text[:120]!r}")
    return content