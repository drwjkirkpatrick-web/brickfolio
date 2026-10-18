"""Region detection: carving a photo into candidate part locations.

The recognition client (``brickfolio.vision.brickognize.identify_part``)
answers "what part is in this picture?" — perfect for one part on a clean
background, but a pile photo holds many parts. This module defines the
seam where detection happens: a ``Detector`` turns one photo into a list
of ``Region`` rectangles (one Region ≈ one part location), and the scan
session (``brickfolio.vision.session``) feeds each region to the
identifier separately.

WHY an abstract base class: v1 ships only trivial detectors — a JSON
sidecar for hand annotation (``ManualRegionsDetector``) and a fixed list
for tests and demos (``StaticDetector``). The real pile detector —
likely YOLO trained on synthetic LDraw pile renders — plugs in later as
one more ``Detector`` subclass without touching session code.

NOTE: this module is deliberately Pillow-free. Regions are plain pixel
rectangles; what happens *around* them (cropping) is the session's
optional-Pillow concern.
"""

from __future__ import annotations

import abc
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Union

__all__ = [
    "Detector",
    "DetectorError",
    "ManualRegionsDetector",
    "Region",
    "StaticDetector",
]


@dataclass(frozen=True)
class Region:
    """A rectangle of interest in a photo, in pixels from the top-left.

    ``(x, y)`` is the box's top-left corner; ``w``/``h`` its size.

    NOTE: frozen to match the shared-contract style of
    ``brickfolio.models`` — a detection is an immutable fact from one
    detector run, not a mutable scratchpad.
    """

    x: int
    y: int
    w: int
    h: int


class DetectorError(Exception):
    """A detector failed: missing/unreadable sidecar, malformed JSON,
    or region entries that cannot be interpreted."""


class Detector(abc.ABC):
    """ABC: turn one photo into the Regions worth identifying.

    WHY abstract: a real pile detector (YOLO on synthetic LDraw pile
    renders) plugs in later without touching session code — the session
    only ever calls ``detect`` and consumes ``Region`` objects.
    """

    @abc.abstractmethod
    def detect(self, image_path: Union[str, os.PathLike]) -> List[Region]:
        """Return the Regions of ``image_path`` (any order)."""


class ManualRegionsDetector(Detector):
    """Detector reading a hand-annotated ``<image>.regions.json`` sidecar.

    For ``pile.jpg`` it loads ``pile.jpg.regions.json`` sitting next to
    the photo. Accepted shapes — tolerant on purpose, because the file
    is written by humans or small scripts, not an API contract:

    * a bare JSON list, or a dict with a ``"regions"`` list;
    * each entry either ``{"x": 1, "y": 2, "w": 3, "h": 4}`` or the
      4-item array ``[1, 2, 3, 4]``.

    WHY loud errors here (unlike the defensive Brickognize parser): the
    sidecar is a local, hand-authored file. A typo that silently scans
    fewer regions is far worse than a clear crash at load time.
    """

    def detect(self, image_path: Union[str, os.PathLike]) -> List[Region]:
        sidecar = Path(os.fspath(image_path) + ".regions.json")
        if not sidecar.is_file():
            raise DetectorError(f"no regions sidecar: {sidecar}")
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DetectorError(f"cannot read regions sidecar {sidecar}: {exc}") from exc
        return _regions_from_payload(payload, source=str(sidecar))


class StaticDetector(Detector):
    """Returns a fixed Region list regardless of the photo (tests/demos).

    Accepts ``Region`` instances or bare ``(x, y, w, h)`` sequences so
    demo code stays short. ``detect`` hands out a fresh list each call,
    so callers cannot mutate the detector's state through the result.
    """

    def __init__(self, regions: Iterable[Union[Region, Sequence[int]]] = ()) -> None:
        self.regions: List[Region] = [
            r if isinstance(r, Region) else Region(*r) for r in regions
        ]

    def detect(self, image_path: Union[str, os.PathLike]) -> List[Region]:
        return list(self.regions)


# --- private helpers ---------------------------------------------------------


def _regions_from_payload(payload: object, source: str) -> List[Region]:
    """Tolerantly locate the region list in a parsed sidecar payload."""
    entries = payload.get("regions") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise DetectorError(
            f"{source}: expected a JSON list of regions, or an object "
            f'with a "regions" list'
        )
    regions: List[Region] = []
    for i, entry in enumerate(entries):
        region = _region_from_entry(entry)
        if region is None:
            raise DetectorError(
                f"{source}: region #{i} is neither {{x, y, w, h}} nor "
                f"[x, y, w, h]: {entry!r}"
            )
        regions.append(region)
    return regions


def _region_from_entry(entry: object) -> Union[Region, None]:
    """One sidecar entry -> Region; None when the entry is unusable."""
    if isinstance(entry, dict):
        try:
            return Region(
                int(entry["x"]), int(entry["y"]), int(entry["w"]), int(entry["h"])
            )
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(entry, (list, tuple)) and len(entry) == 4:
        try:
            return Region(*(int(value) for value in entry))
        except (TypeError, ValueError):
            return None
    return None