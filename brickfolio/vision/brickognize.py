"""Brickognize part-recognition client (free, keyless, stdlib-only).

Brickognize (https://brickognize.com) identifies a LEGO part from a
photo. This module is the thinnest possible wrapper around its HTTP API:

* the photo is POSTed as a hand-assembled multipart/form-data body
  (RFC 7578) — the stdlib has no multipart helper and the project rule
  is stdlib-only, so the MIME message is built line by line;
* the JSON reply is parsed defensively into ``PartCandidate`` objects.

NOTE: ``endpoint=`` defaults to the documented legacy endpoint
(https://api.brickognize.com/predict/parts/). Upstream marks it
"legacy" but keeps it public and keyless; because the endpoint is just
a parameter (and may carry query parameters such as
``?top_k_items=25``), switching APIs later is a call-site change, not a
rewrite here.

NOTE: tests never touch the network. ``identify_part(..., opener=fake)``
accepts anything with ``open(request, timeout=...)`` — the
``urllib.request.OpenerDirector`` duck type — so tests inject fixture
JSON in place of a live API. See tests/test_brickognize.py.
"""

from __future__ import annotations

import json
import math
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional, Union

__all__ = [
    "BrickognizeError",
    "DEFAULT_ENDPOINT",
    "PartCandidate",
    "identify_part",
]

DEFAULT_ENDPOINT = "https://api.brickognize.com/predict/parts/"

# WHY "query_image" and not the customary "file": the API's own OpenAPI
# schema (api.brickognize.com/openapi.json,
# Body_predict_parts_legacy_predict_parts__post) names the multipart
# field "query_image" — it is a repeated field there, but sending one
# occurrence is valid. A wrong field name yields a 422, so this is the
# one string that cannot be guessed.
_UPLOAD_FIELD = "query_image"

# WHY an explicit table before mimetypes: ``mimetypes`` consults the OS
# registry on some platforms, so the same file name can map to
# different MIME types on different machines. The raster types
# Brickognize accepts should map deterministically; anything else falls
# through to mimetypes, then to a safe default.
_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


class BrickognizeError(Exception):
    """A Brickognize API call failed: unreadable image, network/HTTP error,
    timeout, or a non-JSON reply.

    WHY one error type: callers (the CLI, scan sessions) catch a single
    brickfolio exception instead of reaching into urllib's error tree.
    """


@dataclass(frozen=True)
class PartCandidate:
    """One recognition guess for a photo, in the API's own order (best first).

    NOTE: frozen to match the shared-contract style of
    brickfolio.models — a candidate is an immutable fact about one API
    reply, not a mutable scratchpad.
    """

    part_num: str
    name: str
    score: float  # confidence, normalized to 0.0-1.0


def identify_part(
    image_path: Union[str, os.PathLike],
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: float = 30,
    # NOTE: deliberately untyped (`Any`), not a Protocol — this is a test
    # seam. Production passes (or builds) a urllib.request.OpenerDirector;
    # tests pass any object with open(request, timeout=...). Their real
    # signatures differ enough that a Protocol only fights the checker.
    opener: Any = None,
) -> List[PartCandidate]:
    """POST one photo to Brickognize; return its part guesses (best first).

    Args:
        image_path: photo to identify; the file extension picks the MIME
            type for the multipart part header.
        endpoint: Brickognize predict URL; may carry query parameters.
        timeout: seconds to wait for connect+read.
        opener: optional transport replacement (any object with
            ``open(request, timeout=...)``). Tests inject fakes here so
            this function is exercised without any network.

    Returns:
        Parsed candidates; an empty list for empty or odd-but-valid
        payloads rather than an exception.

    Raises:
        BrickognizeError: the image is unreadable, the request fails
            (HTTP error, timeout, network trouble), or the reply is
            not JSON.
    """
    filename = os.path.basename(os.fspath(image_path)) or "image"
    try:
        with open(image_path, "rb") as fh:
            image_bytes = fh.read()
    except OSError as exc:
        raise BrickognizeError(
            f"cannot read image {os.fspath(image_path)!r}: {exc}"
        ) from exc

    boundary = uuid.uuid4().hex
    body = _multipart_body(
        field=_UPLOAD_FIELD,
        filename=filename,
        content_type=_content_type_for(filename),
        data=image_bytes,
        boundary=boundary,
    )
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": "brickfolio-vision/0.1",
        },
        method="POST",
    )
    # WHY set Content-Length explicitly: the default opener would add it
    # inside its HTTP handler, but a Request handed straight to an
    # injected (fake) opener never passes through that handler.
    request.add_header("Content-Length", str(len(body)))

    if opener is None:
        # NOTE: attribute access (not "from urllib.request import
        # build_opener") so tests can monkeypatch urllib.request.build_opener
        # to verify this default wiring without opening sockets.
        opener = urllib.request.build_opener()

    try:
        response = opener.open(request, timeout=timeout)
        raw = response.read()
        status = _status_of(response)
    except urllib.error.HTTPError as exc:
        raise BrickognizeError(_http_error_message(exc)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # NOTE: URLError wraps most transport failures (including
        # timeouts); TimeoutError/OSError catch the unwrapped ones.
        # HTTPError is an URLError subclass, so it must be caught first.
        raise BrickognizeError(f"Brickognize request failed: {_reason_of(exc)}") from exc

    # The default opener raises HTTPError for 4xx/5xx itself; this check
    # covers injected openers that hand the response back regardless.
    if status is not None and not 200 <= status < 300:
        raise BrickognizeError(f"Brickognize returned HTTP {status} for {endpoint}")

    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = text[:120].strip() or repr(raw[:120])
        raise BrickognizeError(
            f"Brickognize returned non-JSON response ({exc.msg}): {snippet!r}"
        ) from exc

    return _parse_candidates(payload)


# --- private helpers ---------------------------------------------------------


def _multipart_body(field: str, filename: str, content_type: str, data: bytes, boundary: str) -> bytes:
    """Assemble a one-file multipart/form-data body per RFC 7578.

    NOTE: CRLF line endings and the leading/trailing double-dash
    delimiters are mandated by the RFC — one byte wrong here means a
    400 from every HTTP framework, so this is deliberately explicit
    rather than clever.
    """
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n"
        f"\r\n"
    ).encode("utf-8")
    closer = f"\r\n--{boundary}--\r\n".encode("ascii")
    return header + data + closer


def _content_type_for(filename: str) -> str:
    """MIME type for an upload filename (extension-based, deterministic)."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in _IMAGE_TYPES:
        return _IMAGE_TYPES[ext]
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


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
    return f"Brickognize returned HTTP {exc.code} {exc.reason}{suffix}"


def _reason_of(exc: BaseException) -> Any:
    """URLError stores its cause in .reason; other errors speak for themselves."""
    reason = getattr(exc, "reason", None)
    return reason if reason is not None else exc


def _parse_candidates(payload: Any) -> List[PartCandidate]:
    """Defensively map a parsed JSON payload to candidates.

    WHY so tolerant: a recognition API's reply is an external input —
    an unexpected shape must degrade to "no candidates", never crash
    the pipeline. The spec pins this: empty/odd payloads return [].
    """
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    candidates: List[PartCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Key aliases: the live API uses id/name (per its OpenAPI
        # schema), but part_num/num and description are also accepted so
        # other producers — or a future API — still yield candidates.
        part_num = _first_str(item, "part_num", "id", "num")
        if not part_num:
            continue  # an entry with no identifying number is useless
        name = _first_str(item, "name", "description") or ""
        candidates.append(
            PartCandidate(
                part_num=part_num,
                name=name,
                score=_coerce_score(item.get("score")),
            )
        )
    # NOTE: API order is preserved (Brickognize returns best match
    # first); any re-ranking policy belongs to the caller, not here.
    return candidates


def _first_str(item: dict, *keys: str) -> Optional[str]:
    """First key in ``keys`` holding a usable string/number value.

    NOTE: JSON numbers are accepted and stringified — sloppy producers
    send part numbers as 3001 instead of "3001". bools are excluded:
    # JSON ``true`` is a flag, not an identifier.
    """
    for key in keys:
        value = item.get(key)
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
        elif isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def _coerce_score(raw: Any) -> float:
    """Return a float in [0.0, 1.0] for whatever the API sent as a score.

    The live API sends 0-1 floats (per its OpenAPI schema), but scores
    arrive as strings or 0-100 percentages elsewhere; any value above 1
    is treated as a percentage. Unparseable/NaN/inf input scores 0.0 —
    a guess without a usable confidence still beats dropping the part.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(value) or math.isinf(value):
        return 0.0
    if value > 1.0:
        value /= 100.0
    return min(max(value, 0.0), 1.0)