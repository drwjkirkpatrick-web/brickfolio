"""Tests for the Brickognize client — every single one offline.

Strategy: ``identify_part`` accepts an ``opener`` precisely so these
tests can substitute a fake transport. The fakes below mirror the two
contracts the client relies on — an opener's ``open(request, timeout=…)``
and a response's ``read()`` + status — and record the outgoing request
so the hand-built multipart body can be byte-compared. No socket is
ever created; happy paths replay the fixture JSON in
tests/fixtures/brickognize/ (canonical + variant key shapes).
"""

import dataclasses
import io
import json
import socket
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

from brickfolio.vision import identify_part as reexported_identify_part
from brickfolio.vision.brickognize import (
    DEFAULT_ENDPOINT,
    BrickognizeError,
    PartCandidate,
    identify_part,
)

FIXTURES = Path(__file__).parent / "fixtures" / "brickognize"


class FakeResponse:
    """Bytes + status — just enough of http.client.HTTPResponse."""

    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def getcode(self):
        return self.status


class FakeOpener:
    """Records requests; replies with a canned response or a canned error."""

    def __init__(self, response=None, exc=None):
        self._response = response or FakeResponse(b'{"items": []}')
        self._exc = exc
        self.calls = []  # [(request, timeout), ...]

    def open(self, req, timeout=None):
        self.calls.append((req, timeout))
        if self._exc is not None:
            raise self._exc
        return self._response


def _write_image(tmp_path, name="pile.jpg", data=b"fake-image-bytes"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _header(req, name):
    # urllib normalizes header keys with .capitalize() ("Content-type");
    # headers set via add_header land in a separate unredirected dict.
    name = name.capitalize()
    return req.headers.get(name) or req.unredirected_hdrs.get(name)


def _fixture(name):
    return (FIXTURES / name).read_bytes()


# --- happy paths (fixture replay) ------------------------------------------


def test_canonical_payload(tmp_path):
    opener = FakeOpener(FakeResponse(_fixture("canonical.json")))
    assert identify_part(_write_image(tmp_path), opener=opener) == [
        PartCandidate("3001", "Brick 2 x 4", 0.9),
        PartCandidate("3010", "Brick 1 x 4", 0.42),
        PartCandidate("3024", "Plate 1 x 1", 0.13),
    ]


def test_variant_key_payload(tmp_path):
    opener = FakeOpener(FakeResponse(_fixture("variant_keys.json")))
    assert identify_part(_write_image(tmp_path), opener=opener) == [
        PartCandidate("3005", "Brick 1 x 1", 0.975),  # "97.5" → 0–1
        PartCandidate("3622", "Brick 1 x 3", 0.125),  # "12.5" → 0–1
    ]


# --- request construction (byte-exact) --------------------------------------


def test_request_shape_and_multipart_body(tmp_path):
    data = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    image = _write_image(tmp_path, "pile.JPG", data)
    opener = FakeOpener(FakeResponse(b'{"items": []}'))
    identify_part(image, endpoint="https://fake.test/predict/parts/", opener=opener)

    (request, timeout), = opener.calls
    assert request.full_url == "https://fake.test/predict/parts/"
    assert request.get_method() == "POST"
    assert timeout == 30  # default forwarded to the transport

    content_type = _header(request, "content-type")
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.split("boundary=")[1]

    # WHY byte-exact: the RFC 7578 framing (CRLFs, delimiters, part
    # headers) is the riskiest hand-rolled piece — assert all of it.
    expected = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="query_image"; filename="pile.JPG"\r\n'
        "Content-Type: image/jpeg\r\n"
        "\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("ascii")
    assert request.data == expected
    assert _header(request, "content-length") == str(len(expected))


@pytest.mark.parametrize(
    "name,expected",
    [
        ("photo.png", "image/png"),
        ("photo.webp", "image/webp"),
        ("photo.jpeg", "image/jpeg"),
        ("photo.bin", "application/octet-stream"),
    ],
)
def test_part_content_type_follows_extension(tmp_path, name, expected):
    opener = FakeOpener()
    identify_part(_write_image(tmp_path, name), opener=opener)
    body = opener.calls[0][0].data
    assert f"Content-Type: {expected}\r\n".encode() in body


def test_endpoint_and_timeout_forwarded(tmp_path):
    opener = FakeOpener(FakeResponse(b'{"items": []}'))
    identify_part(
        _write_image(tmp_path),
        endpoint="https://fake.test/predict/",
        timeout=7,
        opener=opener,
    )
    request, timeout = opener.calls[0]
    assert request.full_url == "https://fake.test/predict/"
    assert timeout == 7


def test_default_opener_and_endpoint_used(monkeypatch, tmp_path):
    opener = FakeOpener(FakeResponse(b'{"items": []}'))
    monkeypatch.setattr(urllib.request, "build_opener", lambda: opener)
    assert identify_part(_write_image(tmp_path)) == []
    request, _ = opener.calls[0]
    assert request.full_url == DEFAULT_ENDPOINT


# --- error paths ------------------------------------------------------------


def test_missing_image_file_raises():
    with pytest.raises(BrickognizeError) as excinfo:
        identify_part("/no/such/pile.jpg", opener=FakeOpener())
    assert "cannot read" in str(excinfo.value)


def test_http_error_raises_with_server_detail(tmp_path):
    err = urllib.error.HTTPError(
        "https://api.brickognize.com/predict/parts/",
        503,
        "Service Unavailable",
        Message(),
        io.BytesIO(b'{"detail": "overloaded"}'),
    )
    with pytest.raises(BrickognizeError) as excinfo:
        identify_part(_write_image(tmp_path), opener=FakeOpener(exc=err))
    message = str(excinfo.value)
    assert "503" in message
    assert "overloaded" in message


def test_bad_response_status_raises(tmp_path):
    opener = FakeOpener(FakeResponse(b'{"items": []}', status=500))
    with pytest.raises(BrickognizeError) as excinfo:
        identify_part(_write_image(tmp_path), opener=opener)
    assert "500" in str(excinfo.value)


@pytest.mark.parametrize(
    "exc,expected_in_message",
    [
        (urllib.error.URLError(socket.timeout("connection timed out")), "timed out"),
        (TimeoutError("request timed out"), "timed out"),
        (urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")), "refused"),
    ],
)
def test_transport_errors_raise(tmp_path, exc, expected_in_message):
    with pytest.raises(BrickognizeError) as excinfo:
        identify_part(_write_image(tmp_path), opener=FakeOpener(exc=exc))
    assert expected_in_message in str(excinfo.value)


def test_non_json_response_raises_with_snippet(tmp_path):
    opener = FakeOpener(FakeResponse(b"<html>Bad Gateway</html>"))
    with pytest.raises(BrickognizeError) as excinfo:
        identify_part(_write_image(tmp_path), opener=opener)
    message = str(excinfo.value)
    assert "non-JSON" in message
    assert "Bad Gateway" in message


# --- defensive JSON parsing ---------------------------------------------------

# Empty or odd-but-valid payloads degrade to [] instead of raising.
# NOTE: socket.timeout is a TimeoutError alias — both shapes kept
# for realism with what urllib raises on a stalled connection.
@pytest.mark.parametrize(
    "body",
    [
        _fixture("empty.json"),
        _fixture("odd.json"),
        b'{"items": []}',
        b"{}",
        b'{"items": "not-a-list"}',
        b'"bare string"',
        b"[1, 2, 3]",
        b"null",
    ],
)
def test_empty_or_odd_payloads_return_empty(tmp_path, body):
    assert identify_part(_write_image(tmp_path), opener=FakeOpener(FakeResponse(body))) == []


def test_odd_items_are_skipped(tmp_path):
    body = json.dumps(
        {
            "items": [
                {"id": 3001, "name": "Brick 2 x 4", "score": 42},
                {"no_id": True, "name": "ghost entry"},  # no id → skipped
                "junk",  # not a dict → skipped
                {"num": "3024", "description": "Plate 1 x 1"},  # alias keys, no score
                {"part_num": "  98765  ", "name": "Odd spacing"},  # stripped
            ]
        }
    ).encode()
    assert identify_part(_write_image(tmp_path), opener=FakeOpener(FakeResponse(body))) == [
        PartCandidate("3001", "Brick 2 x 4", 0.42),  # int id, percent score
        PartCandidate("3024", "Plate 1 x 1", 0.0),
        PartCandidate("98765", "Odd spacing", 0.0),
    ]


def test_score_normalization(tmp_path):
    body = json.dumps(
        {
            "items": [
                {"id": "a", "name": "n", "score": 0.975},
                {"id": "b", "name": "n", "score": "0.42"},
                {"id": "c", "name": "n", "score": 97.5},  # percent scale
                {"id": "d", "name": "n", "score": 250},  # clamped to 1.0
                {"id": "e", "name": "n", "score": -2},  # clamped to 0.0
                {"id": "f", "name": "n", "score": None},
                {"id": "g", "name": "n", "score": "junk"},
                {"id": "h", "name": "n", "score": float("nan")},
            ]
        }
    ).encode()
    scores = [
        c.score
        for c in identify_part(_write_image(tmp_path), opener=FakeOpener(FakeResponse(body)))
    ]
    assert scores == [0.975, 0.42, 0.975, 1.0, 0.0, 0.0, 0.0, 0.0]


# --- shared-contract conventions ----------------------------------------------


def test_part_candidate_is_frozen():
    candidate = PartCandidate("3001", "Brick 2 x 4", 0.9)
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.score = 1.0


def test_reexported_from_vision_package():
    assert reexported_identify_part is identify_part