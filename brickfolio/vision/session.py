"""Scan sessions: photo -> candidates -> confirmed inventory.

A ``ScanSession`` drives the interactive review loop over one or more
pile photos:

1. ``add_photo(image_path, detector, identifier)`` — the detector carves
   the photo into ``Region`` boxes; the identifier (any callable shaped
   like ``brickognize.identify_part``: one image path ->
   ``List[PartCandidate]``) recognizes parts. When Pillow is importable
   each region is cropped to a temporary file and identified on its own;
   without Pillow — or with no regions, or when cropping fails — the
   whole photo is identified once. Candidates accumulate across photos.
2. ``accept(index, ...)`` — write a candidate into the collection via
   ``Database.add_user_parts``.
3. ``reject(index)`` — drop a candidate without recording it.
4. ``save(path)`` / ``load(path, db)`` — persist the pending review
   state as JSON so a scan can continue in a later process.

NOTE: the session file records *pending* candidates only. Accepted
parts live in the database (the source of truth for the collection);
the file keeps an "accepted" log purely so ``summary()`` can still
report what the session contributed after a reload.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple, Union

from ..db import Database
from ..models import InventoryItem
from .brickognize import PartCandidate
from .detector import Detector, Region

# Optional Pillow: per-region crops need an imaging library, but the
# project rule is stdlib-only, so Pillow stays an optional accelerator —
# without it, whole-photo identification still works end to end.
try:
    from PIL import Image  # type: ignore[import-not-found]

    _HAS_PILLOW = True
except ImportError:
    Image = None  # WHY None (not absent): _crop_regions can probe it safely
    _HAS_PILLOW = False

__all__ = [
    "DEFAULT_COLOR_ID",
    "PhotoCandidates",
    "ScanSession",
    "SESSION_FORMAT_VERSION",
]

# Brickognize identifies shape, not color. When the caller has no color
# to pass, Rebrickable id "0" (Black — the canonical example in
# models.Color) is the documented fallback; callers should pass
# color_id explicitly whenever they know it.
DEFAULT_COLOR_ID = "0"

# Bumped whenever the on-disk session format changes incompatibly.
SESSION_FORMAT_VERSION = 1


@dataclass
class PhotoCandidates:
    """Per-photo scan results: the photo, its Regions, its candidates.

    NOTE: deliberately NOT frozen. Unlike the immutable contracts in
    ``brickfolio.models`` (API replies, catalog rows), this is working
    review state — accept()/reject() remove entries from
    ``candidates`` as decisions are made, and freezing would force
    copy-on-every-decision churn.
    """

    photo: str
    candidates: List[PartCandidate] = field(default_factory=list)
    regions: List[Region] = field(default_factory=list)


class ScanSession:
    """Interactive photo-scan state layered over one ``Database``.

    The flat indices used by ``accept``/``reject``/``summary`` run over
    every still-pending candidate of every photo, in photo order —
    print ``summary()`` after each decision and its numbers stay in
    sync with the calls.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self.photos: List[PhotoCandidates] = []
        # WHAT this session contributed to the collection (the db stays
        # the source of truth; this log only feeds summary()/save()).
        self.accepted: List[InventoryItem] = []
        self.rejected: int = 0

    # ---- scanning -----------------------------------------------------------

    def add_photo(
        self,
        image_path: Union[str, os.PathLike],
        detector: Detector,
        identifier: Callable[[Union[str, os.PathLike]], List[PartCandidate]],
    ) -> PhotoCandidates:
        """Scan one photo: detect regions, identify, accumulate candidates.

        Identifier strategy (per spec): per-region when Pillow is
        importable, else once for the whole photo. Any error from the
        detector or identifier propagates — the caller (CLI) decides how
        to report a failed photo; a half-scanned photo is never
        silently recorded.

        Returns the ``PhotoCandidates`` entry appended to ``self.photos``.
        """
        photo = os.fspath(image_path)
        regions = list(detector.detect(photo))

        candidates: List[PartCandidate] = []
        identified_per_region = False
        if _HAS_PILLOW and regions:
            # NOTE: crops are short-lived — the identifier only needs
            # the bytes, so the scratch directory vanishes right after.
            with tempfile.TemporaryDirectory(prefix="brickfolio-crop-") as crop_dir:
                crop_paths = _crop_regions(photo, regions, crop_dir)
                if crop_paths:
                    for crop_path in crop_paths:
                        candidates.extend(identifier(crop_path))
                    identified_per_region = True
        if not identified_per_region:
            # WHY not `elif`: three cases funnel here — no Pillow, no
            # regions, or crops that failed — and in every one the photo
            # must still be identified exactly once, whole.
            candidates = list(identifier(photo))

        entry = PhotoCandidates(photo=photo, candidates=candidates, regions=regions)
        self.photos.append(entry)
        return entry

    # ---- review loop ---------------------------------------------------------

    def accept(
        self,
        index: int,
        color_id: Union[str, int, None] = None,
        quantity: int = 1,
    ) -> InventoryItem:
        """Confirm pending candidate ``index`` into the collection.

        Writes one ``InventoryItem`` through ``db.add_user_parts``
        (quantities accumulate with existing stock) and returns it.
        ``color_id=None`` falls back to ``DEFAULT_COLOR_ID`` — pass an
        explicit color whenever it is known.
        """
        if quantity < 1:
            raise ValueError(f"quantity must be >= 1, got {quantity}")
        _, candidate = self._pop_pending(index)
        # NOTE: the db stores color ids as TEXT; accept an int (``4``)
        # and canonicalize to the same string key the rest of the
        # project uses, so accept(color_id=4) and accept(color_id="4")
        # land on the same inventory line.
        color = DEFAULT_COLOR_ID if color_id is None else str(color_id)
        item = InventoryItem(
            part_num=candidate.part_num, color_id=color, quantity=quantity
        )
        self.db.add_user_parts([item])
        self.accepted.append(item)
        return item

    def reject(self, index: int) -> PartCandidate:
        """Drop pending candidate ``index`` without recording it.

        Returns the dropped candidate so callers can say what went.
        """
        _, candidate = self._pop_pending(index)
        self.rejected += 1
        return candidate

    def summary(self) -> str:
        """One status line plus one indexed line per pending candidate.

        The ``[i]`` numbers are exactly the indices ``accept``/``reject``
        take — render this after every decision.
        """
        pending = [
            (entry.photo, candidate)
            for entry in self.photos
            for candidate in entry.candidates
        ]
        lines = [
            f"{len(self.photos)} photo(s), {len(pending)} candidate(s) pending, "
            f"{len(self.accepted)} accepted, {self.rejected} rejected"
        ]
        for i, (photo, candidate) in enumerate(pending):
            lines.append(
                f"[{i}] {candidate.part_num:<10} {candidate.name:<28} "
                f"{candidate.score:5.0%}  {photo}"
            )
        return "\n".join(lines)

    # ---- persistence -----------------------------------------------------------

    def save(self, path: Union[str, os.PathLike]) -> None:
        """Write the session (pending candidates + decision log) as JSON."""
        payload = {
            "version": SESSION_FORMAT_VERSION,
            "photos": [
                {
                    "photo": entry.photo,
                    "regions": [dataclasses.asdict(r) for r in entry.regions],
                    "candidates": [dataclasses.asdict(c) for c in entry.candidates],
                }
                for entry in self.photos
            ],
            "accepted": [dataclasses.asdict(a) for a in self.accepted],
            "rejected": self.rejected,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")

    @classmethod
    def load(
        cls, path: Union[str, os.PathLike], db: Database
    ) -> "ScanSession":
        """Restore a session saved by ``save``.

        ``db`` backs subsequent ``accept`` calls — it is a load argument
        because the session file intentionally stores no db config.
        NOTE: loading never replays ``accepted`` into the database; the
        db is the record of the collection, the file only the review
        state.

        WHY tolerant parsing: session files are user-editable JSON that
        may outlive format changes, so unusable entries are skipped
        rather than losing the whole session (same defensive-JSON style
        as the Brickognize parser).
        """
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}: session file must be a JSON object")

        session = cls(db)
        for photo_payload in payload.get("photos") or []:
            entry = _photo_from_json(photo_payload)
            if entry is not None:
                session.photos.append(entry)
        for item_payload in payload.get("accepted") or []:
            item = _accepted_from_json(item_payload)
            if item is not None:
                session.accepted.append(item)
        rejected = payload.get("rejected", 0)
        # NOTE: bools are ints in Python — exclude them explicitly.
        if isinstance(rejected, int) and not isinstance(rejected, bool):
            session.rejected = rejected
        return session

    # ---- private -----------------------------------------------------------

    def _pop_pending(self, index: int) -> Tuple[PhotoCandidates, PartCandidate]:
        """Remove and return the pending candidate at flat ``index``.

        Indices run over every photo's pending candidates in order.
        Negative indices are rejected outright — review UIs show
        non-negative numbers, and Python's wrap-around semantics would
        silently pick the wrong part.
        """
        if index < 0:
            raise IndexError(f"no pending candidate at index {index}")
        for entry in self.photos:
            if index < len(entry.candidates):
                return entry, entry.candidates.pop(index)
            index -= len(entry.candidates)
        raise IndexError(f"no pending candidate at index {index}")


# --- private helpers ---------------------------------------------------------


def _crop_regions(
    image_path: str, regions: List[Region], dest_dir: str
) -> List[str]:
    """Crop each usable Region of ``image_path`` to its own file in ``dest_dir``.

    Returns the crop paths, in region order — the caller identifies each
    crop separately. Empty list means "cropping is impossible" (Pillow
    missing, no usable region, unreadable image, zero-size or failing
    save), which the session turns into whole-photo identification.

    NOTE: all-or-nothing per photo — one failed crop drops back to
    single-shot identification of the whole photo, never a confusing
    mix of per-region and whole-photo candidates for one photo.
    NOTE: regions with w/h <= 0 are skipped (a zero-area crop is
    meaningless); PIL pads out-of-bounds boxes with black instead of
    raising, so slightly-overshooting detectors are tolerated.
    """
    if Image is None:  # Pillow flag forced True in a Pillow-less process
        return []
    usable = [r for r in regions if r.w > 0 and r.h > 0]
    if not usable:
        return []
    # Keep the source extension so identify_part derives the same MIME
    # type for the crop as for the original photo.
    ext = os.path.splitext(image_path)[1] or ".png"
    paths: List[str] = []
    try:
        with Image.open(image_path) as img:
            for i, region in enumerate(usable):
                box = (region.x, region.y, region.x + region.w, region.y + region.h)
                dest = os.path.join(dest_dir, f"crop-{i:03d}{ext}")
                img.crop(box).save(dest)
                paths.append(dest)
    except (OSError, ValueError):
        # OSError covers unreadable/corrupt images and disk trouble;
        # ValueError covers zero-size crops PIL refuses to save.
        return []
    return paths


def _photo_from_json(payload: object) -> Optional[PhotoCandidates]:
    """One saved photo entry -> PhotoCandidates; None when unusable."""
    if not isinstance(payload, dict):
        return None
    photo = payload.get("photo")
    if not isinstance(photo, str) or not photo:
        return None
    regions: List[Region] = []
    for raw in payload.get("regions") or []:
        if not isinstance(raw, dict):
            continue
        try:
            regions.append(
                Region(int(raw["x"]), int(raw["y"]), int(raw["w"]), int(raw["h"]))
            )
        except (KeyError, TypeError, ValueError):
            continue
    candidates: List[PartCandidate] = []
    for raw in payload.get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        part_num = raw.get("part_num")
        if not isinstance(part_num, str) or not part_num:
            continue  # a candidate without a part number is useless
        name = raw.get("name")
        candidates.append(
            PartCandidate(
                part_num=part_num,
                name=name if isinstance(name, str) else "",
                score=_score(raw.get("score")),
            )
        )
    return PhotoCandidates(photo=photo, candidates=candidates, regions=regions)


def _accepted_from_json(payload: object) -> Optional[InventoryItem]:
    """One saved accepted-line -> InventoryItem; None when unusable."""
    if not isinstance(payload, dict):
        return None
    part_num = payload.get("part_num")
    if not isinstance(part_num, str) or not part_num:
        return None
    try:
        quantity = int(payload.get("quantity", 1))
    except (TypeError, ValueError):
        return None
    if quantity < 1:
        return None
    color_id = payload.get("color_id", DEFAULT_COLOR_ID)
    return InventoryItem(
        part_num=part_num,
        color_id=str(color_id),
        quantity=quantity,
    )


def _score(raw: Any) -> float:
    """Coerce a saved score back to a float in [0.0, 1.0]."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return min(max(value, 0.0), 1.0)