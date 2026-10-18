"""Tests for scan sessions (Prompt 7) — fully offline, no network.

Two fakes drive everything at the module boundaries, per the project
testing strategy:

* detectors — ``StaticDetector`` from the detector module doubles as
  the fake for fixed region lists; ``FakeDetector`` below scripts
  different regions per photo (and proves the ABC is subclassable);
* identifiers — ``FakeIdentifier`` records every call and answers by
  path, standing in for ``brickognize.identify_part`` with its exact
  duck shape: one positional image path -> List[PartCandidate].

Pillow is optional by spec, so the per-region path is tested by
monkeypatching the Pillow flag and the crop seam (deterministic on any
machine, installed or not); only the real crop code itself uses
``pytest.importorskip`` and simply skips where Pillow is absent.
"""

import dataclasses
import json
from pathlib import Path

import pytest

from brickfolio.db import Database
from brickfolio.models import InventoryItem
from brickfolio.vision import session as session_module
from brickfolio.vision.brickognize import PartCandidate
from brickfolio.vision.detector import (
    Detector,
    DetectorError,
    ManualRegionsDetector,
    Region,
    StaticDetector,
)
from brickfolio.vision.session import (
    DEFAULT_COLOR_ID,
    PhotoCandidates,
    ScanSession,
)

CANDS_A = [
    PartCandidate("3001", "Brick 2 x 4", 0.9),
    PartCandidate("3010", "Brick 1 x 4", 0.42),
]
CANDS_B = [PartCandidate("3024", "Plate 1 x 1", 0.13)]


# --- fakes at the module boundaries --------------------------------------------


class FakeDetector(Detector):
    """Scripted per-image regions; default for unknown photos."""

    def __init__(self, by_image=None, default=()):
        self.by_image = {str(k): list(v) for k, v in (by_image or {}).items()}
        self.default = list(default)
        self.calls = []

    def detect(self, image_path):
        path = str(image_path)
        self.calls.append(path)
        return list(self.by_image.get(path, self.default))


class FakeIdentifier:
    """Records each image it identifies; answers per path or a default."""

    def __init__(self, by_path=None, default=None):
        self.by_path = {str(k): list(v) for k, v in (by_path or {}).items()}
        self.default = list(default or [])
        self.calls = []

    def __call__(self, image_path):
        path = str(image_path)
        self.calls.append(path)
        return list(self.by_path.get(path, self.default))


class MeasuringIdentifier(FakeIdentifier):
    """Pillow-only fake: records (path, pixel size) of each image it sees.

    WHY measurements inside the fake: session crops live in a temporary
    directory that is deleted before add_photo returns, so the files
    cannot be inspected after the fact.
    """

    def __call__(self, image_path):
        from PIL import Image

        with Image.open(image_path) as img:
            size = img.size
        self.calls.append((str(image_path), size))
        return [PartCandidate("3001", "Brick 2 x 4", 0.5)]


def write_photo(tmp_path, name="pile.jpg", data=b"fake-photo-bytes"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _write_sidecar(photo_path, payload):
    sidecar = Path(str(photo_path) + ".regions.json")
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    return sidecar


# --- Region contract -----------------------------------------------------------


def test_region_fields():
    region = Region(10, 20, 30, 40)
    assert (region.x, region.y, region.w, region.h) == (10, 20, 30, 40)


def test_region_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Region(0, 0, 1, 1).x = 5


def test_detector_abc_rejects_direct_instantiation():
    # WHY assert this: the ABC is the plug-in seam; direct instantiation
    # would mean a half-configured detector silently scans nothing.
    with pytest.raises(TypeError):
        Detector()


# --- StaticDetector --------------------------------------------------------------


def test_static_detector_returns_given_regions():
    regions = [Region(0, 0, 10, 10), Region(20, 5, 30, 40)]
    assert StaticDetector(regions).detect("pile.jpg") == regions


def test_static_detector_result_is_a_copy():
    detector = StaticDetector([Region(0, 0, 10, 10)])
    result = detector.detect("pile.jpg")
    result.append(Region(1, 1, 1, 1))
    assert detector.detect("pile.jpg") == [Region(0, 0, 10, 10)]


def test_static_detector_accepts_bare_tuples():
    detector = StaticDetector([(0, 0, 10, 10), Region(1, 2, 3, 4)])
    assert detector.regions == [Region(0, 0, 10, 10), Region(1, 2, 3, 4)]


# --- ManualRegionsDetector ---------------------------------------------------------


def test_manual_detector_reads_object_entries(tmp_path):
    photo = write_photo(tmp_path)
    _write_sidecar(photo, {"regions": [{"x": 1, "y": 2, "w": 30, "h": 40}]})
    assert ManualRegionsDetector().detect(photo) == [Region(1, 2, 30, 40)]


def test_manual_detector_reads_bare_list_of_arrays(tmp_path):
    photo = write_photo(tmp_path)
    _write_sidecar(photo, [[0, 0, 10, 10], [5, 5, 20, 25]])
    assert ManualRegionsDetector().detect(photo) == [
        Region(0, 0, 10, 10),
        Region(5, 5, 20, 25),
    ]


def test_manual_detector_missing_sidecar_raises(tmp_path):
    photo = write_photo(tmp_path)
    with pytest.raises(DetectorError) as excinfo:
        ManualRegionsDetector().detect(photo)
    assert "pile.jpg.regions.json" in str(excinfo.value)


def test_manual_detector_invalid_json_raises(tmp_path):
    photo = write_photo(tmp_path)
    Path(str(photo) + ".regions.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(DetectorError):
        ManualRegionsDetector().detect(photo)


@pytest.mark.parametrize(
    "payload",
    [
        "junk",  # not a list / dict
        {},  # no "regions" key
        {"regions": "no"},  # regions not a list
        [None],  # unusable entry
        [{"x": 1}],  # missing keys
        [[1, 2, 3]],  # wrong arity
        [{"x": "a", "y": 0, "w": 1, "h": 1}],  # non-numeric field
    ],
)
def test_manual_detector_bad_payloads_raise(tmp_path, payload):
    photo = write_photo(tmp_path)
    _write_sidecar(photo, payload)
    with pytest.raises(DetectorError):
        ManualRegionsDetector().detect(photo)


# --- add_photo: identification strategy --------------------------------------------


def test_whole_photo_identification_without_pillow(monkeypatch, db, tmp_path):
    monkeypatch.setattr(session_module, "_HAS_PILLOW", False)
    photo = write_photo(tmp_path)
    identifier = FakeIdentifier(default=CANDS_A)
    session = ScanSession(db)

    entry = session.add_photo(photo, StaticDetector([Region(0, 0, 10, 10)]), identifier)

    # No Pillow -> exactly one identification, on the original photo.
    assert identifier.calls == [str(photo)]
    assert entry == PhotoCandidates(
        photo=str(photo),
        candidates=CANDS_A,
        regions=[Region(0, 0, 10, 10)],  # regions recorded even when unused
    )
    assert session.photos == [entry]


def test_per_region_identification_with_pillow(monkeypatch, db, tmp_path):
    monkeypatch.setattr(session_module, "_HAS_PILLOW", True)
    crops = ["/tmp/brickfolio-crop/crop-000.jpg", "/tmp/brickfolio-crop/crop-001.jpg"]
    monkeypatch.setattr(
        session_module, "_crop_regions", lambda image, regions, dest: list(crops)
    )
    photo = write_photo(tmp_path)
    identifier = FakeIdentifier(
        by_path={
            crops[0]: [PartCandidate("3001", "Brick 2 x 4", 0.9)],
            crops[1]: [PartCandidate("3024", "Plate 1 x 1", 0.13)],
        }
    )
    session = ScanSession(db)

    entry = session.add_photo(
        photo, StaticDetector([Region(0, 0, 10, 10), Region(20, 20, 30, 30)]), identifier
    )

    assert identifier.calls == crops  # one call per region, in order
    assert [c.part_num for c in entry.candidates] == ["3001", "3024"]
    assert entry.regions == [Region(0, 0, 10, 10), Region(20, 20, 30, 30)]


def test_no_regions_identifies_whole_photo(monkeypatch, db, tmp_path):
    monkeypatch.setattr(session_module, "_HAS_PILLOW", True)

    def no_crops(*args):
        raise AssertionError("crops must not happen without regions")

    monkeypatch.setattr(session_module, "_crop_regions", no_crops)
    photo = write_photo(tmp_path)
    identifier = FakeIdentifier(default=CANDS_B)
    session = ScanSession(db)

    entry = session.add_photo(photo, StaticDetector([]), identifier)

    assert identifier.calls == [str(photo)]
    assert entry.candidates == CANDS_B
    assert entry.regions == []


def test_crop_failure_falls_back_to_whole_photo(monkeypatch, db, tmp_path):
    monkeypatch.setattr(session_module, "_HAS_PILLOW", True)
    monkeypatch.setattr(
        session_module, "_crop_regions", lambda image, regions, dest: []
    )
    photo = write_photo(tmp_path)
    identifier = FakeIdentifier(default=CANDS_B)
    session = ScanSession(db)

    session.add_photo(photo, StaticDetector([Region(0, 0, 10, 10)]), identifier)

    assert identifier.calls == [str(photo)]


def test_candidates_accumulate_across_photos(monkeypatch, db, tmp_path):
    monkeypatch.setattr(session_module, "_HAS_PILLOW", False)
    photo_a = write_photo(tmp_path, "a.jpg")
    photo_b = write_photo(tmp_path, "b.jpg")
    identifier = FakeIdentifier(by_path={str(photo_a): CANDS_A, str(photo_b): CANDS_B})
    session = ScanSession(db)

    session.add_photo(photo_a, StaticDetector([]), identifier)
    session.add_photo(photo_b, StaticDetector([]), identifier)

    assert len(session.photos) == 2
    pending = [c.part_num for entry in session.photos for c in entry.candidates]
    assert pending == ["3001", "3010", "3024"]


def test_fake_detector_drives_multi_photo_session(db, tmp_path):
    # FakeDetector (not StaticDetector) scripting different regions per
    # photo — the ABC subclass path end to end.
    photo_a = write_photo(tmp_path, "a.jpg")
    photo_b = write_photo(tmp_path, "b.jpg")
    detector = FakeDetector(
        by_image={str(photo_a): [Region(0, 0, 5, 5)], str(photo_b): []}
    )
    identifier = FakeIdentifier(default=CANDS_A)
    session = ScanSession(db)

    entry_a = session.add_photo(photo_a, detector, identifier)
    entry_b = session.add_photo(photo_b, detector, identifier)

    assert detector.calls == [str(photo_a), str(photo_b)]
    assert entry_a.regions == [Region(0, 0, 5, 5)]
    assert entry_b.regions == []
    assert [c.part_num for e in session.photos for c in e.candidates] == [
        "3001",
        "3010",
        "3001",
        "3010",
    ]


# --- real Pillow crop code (skipped without Pillow) --------------------------------


def _real_png(tmp_path, name="pile.png", size=(100, 80)):
    pytest.importorskip("PIL")
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", size, (200, 30, 30)).save(path)
    return path


def test_crop_regions_writes_one_file_per_region(tmp_path):
    photo = _real_png(tmp_path)
    dest = tmp_path / "crops"
    dest.mkdir()
    regions = [Region(0, 0, 50, 40), Region(50, 40, 50, 40)]

    paths = session_module._crop_regions(str(photo), regions, str(dest))

    assert len(paths) == 2
    for path, region in zip(paths, regions):
        assert Path(path).suffix == ".png"  # source extension kept
        with open(path, "rb") as fh:
            assert fh.read(8) == b"\x89PNG\r\n\x1a\n"


def test_crop_regions_skips_non_positive_and_survives_garbage(tmp_path):
    photo = _real_png(tmp_path)  # pytest.importorskip happens in here
    from PIL import Image

    dest = tmp_path / "crops"
    dest.mkdir()

    # zero-area regions are skipped, not fatal
    paths = session_module._crop_regions(
        str(photo), [Region(0, 0, 0, 0), Region(10, 10, 20, 20)], str(dest)
    )
    assert len(paths) == 1
    with Image.open(paths[0]) as crop:
        assert crop.size == (20, 20)

    # all-non-positive -> no crops -> caller falls back to whole photo
    assert session_module._crop_regions(str(photo), [Region(0, 0, 0, 5)], str(dest)) == []

    # unreadable image -> [] (fallback), not a crash
    garbage = write_photo(tmp_path, "garbage.jpg")
    assert session_module._crop_regions(str(garbage), [Region(0, 0, 10, 10)], str(dest)) == []


def test_session_identifies_each_real_crop(db, tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "_HAS_PILLOW", True)
    photo = _real_png(tmp_path)
    identifier = MeasuringIdentifier()
    session = ScanSession(db)
    regions = [Region(10, 10, 20, 20), Region(40, 30, 60, 50)]

    entry = session.add_photo(photo, StaticDetector(regions), identifier)

    assert [size for _, size in identifier.calls] == [(20, 20), (60, 50)]
    assert all("crop-" in path for path, _ in identifier.calls)
    assert entry.candidates == [PartCandidate("3001", "Brick 2 x 4", 0.5)] * 2


# --- accept / reject -------------------------------------------------------------


def _session_with_two_candidates(db, monkeypatch, tmp_path):
    monkeypatch.setattr(session_module, "_HAS_PILLOW", False)
    photo = write_photo(tmp_path)
    identifier = FakeIdentifier(by_path={str(photo): CANDS_A})
    session = ScanSession(db)
    session.add_photo(photo, StaticDetector([]), identifier)
    return session, photo


def test_accept_writes_default_color_to_db(db, tmp_path, monkeypatch):
    session, _ = _session_with_two_candidates(db, monkeypatch, tmp_path)

    item = session.accept(0)

    assert item == InventoryItem("3001", DEFAULT_COLOR_ID, 1)
    assert DEFAULT_COLOR_ID == "0"  # the documented fallback color
    assert db.list_user_parts() == [InventoryItem("3001", "0", 1)]
    assert db.total_user_parts() == 1
    assert session.accepted == [InventoryItem("3001", "0", 1)]


def test_accept_with_color_and_quantity(db, tmp_path, monkeypatch):
    session, _ = _session_with_two_candidates(db, monkeypatch, tmp_path)

    item = session.accept(1, color_id="4", quantity=3)

    assert item == InventoryItem("3010", "4", 3)
    assert db.list_user_parts() == [InventoryItem("3010", "4", 3)]


def test_accept_coerces_int_color_id(db, tmp_path, monkeypatch):
    session, _ = _session_with_two_candidates(db, monkeypatch, tmp_path)
    assert session.accept(0, color_id=4).color_id == "4"
    assert db.list_user_parts() == [InventoryItem("3001", "4", 1)]


def test_accept_accumulates_quantity_across_photos(db, tmp_path, monkeypatch):
    # Flat indices run across photos: pending starts as
    # [a:3001, a:3010, b:3001, b:3010].
    monkeypatch.setattr(session_module, "_HAS_PILLOW", False)
    photo_a = write_photo(tmp_path, "a.jpg")
    photo_b = write_photo(tmp_path, "b.jpg")
    identifier = FakeIdentifier(default=CANDS_A)
    session = ScanSession(db)
    session.add_photo(photo_a, StaticDetector([]), identifier)
    session.add_photo(photo_b, StaticDetector([]), identifier)

    session.accept(0, quantity=2)  # a:3001
    session.accept(1, quantity=5)  # b:3001 — index 1 after the first pop

    assert db.list_user_parts() == [InventoryItem("3001", "0", 7)]


def test_accept_then_reject_shrinks_pending(db, tmp_path, monkeypatch):
    session, _ = _session_with_two_candidates(db, monkeypatch, tmp_path)

    session.accept(0)
    assert [c.part_num for e in session.photos for c in e.candidates] == ["3010"]

    session.reject(0)
    assert all(not e.candidates for e in session.photos)
    assert session.rejected == 1


def test_reject_leaves_db_untouched(db, tmp_path, monkeypatch):
    session, _ = _session_with_two_candidates(db, monkeypatch, tmp_path)

    dropped = session.reject(0)

    assert dropped == PartCandidate("3001", "Brick 2 x 4", 0.9)
    assert db.list_user_parts() == []
    assert db.total_user_parts() == 0
    assert session.rejected == 1


def test_accept_reject_bad_index(db, tmp_path, monkeypatch):
    session, _ = _session_with_two_candidates(db, monkeypatch, tmp_path)

    for bad in (2, -1, 99):
        with pytest.raises(IndexError):
            session.accept(bad)
    with pytest.raises(IndexError):
        session.reject(-1)

    # Nothing decided, nothing written, candidate list untouched.
    assert db.list_user_parts() == []
    assert session.photos[0].candidates == CANDS_A


def test_accept_rejects_bad_quantity(db, tmp_path, monkeypatch):
    session, _ = _session_with_two_candidates(db, monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        session.accept(0, quantity=0)

    assert db.list_user_parts() == []
    assert session.photos[0].candidates == CANDS_A  # still pending


# --- summary ------------------------------------------------------------------------


def test_summary_lists_pending_candidates_with_indices(db, tmp_path, monkeypatch):
    session, photo = _session_with_two_candidates(db, monkeypatch, tmp_path)

    text = session.summary()
    assert "1 photo(s)" in text
    assert "2 candidate(s) pending" in text
    assert "0 accepted" in text
    assert "0 rejected" in text
    lines = text.splitlines()
    assert "[0] 3001" in lines[1]
    assert "Brick 2 x 4" in lines[1]
    assert "90%" in lines[1]
    assert str(photo) in lines[1]

    session.accept(0)
    session.reject(0)
    text = session.summary()
    assert "0 candidate(s) pending" in text
    assert "1 accepted" in text
    assert "1 rejected" in text


def test_empty_session_summary(db):
    text = ScanSession(db).summary()
    assert text == "0 photo(s), 0 candidate(s) pending, 0 accepted, 0 rejected"


# --- save / load ----------------------------------------------------------------------


def _populated_session(db, monkeypatch, tmp_path):
    """Two photos, one accepted (color "4" x2), one rejected; pending: a:3001."""
    monkeypatch.setattr(session_module, "_HAS_PILLOW", False)
    photo_a = write_photo(tmp_path, "a.jpg")
    photo_b = write_photo(tmp_path, "b.jpg")
    identifier = FakeIdentifier(by_path={str(photo_a): CANDS_A, str(photo_b): CANDS_B})
    session = ScanSession(db)
    session.add_photo(photo_a, StaticDetector([]), identifier)
    session.add_photo(photo_b, StaticDetector([Region(0, 0, 10, 10)]), identifier)
    session.accept(1, color_id="4", quantity=2)  # a:3010
    session.reject(1)  # b:3024 — flat indices shift after the accept pops one
    return session


def test_save_load_round_trip(db, tmp_path, monkeypatch):
    session = _populated_session(db, monkeypatch, tmp_path)
    path = tmp_path / "session.json"
    session.save(path)

    reloaded = ScanSession.load(path, db)

    assert reloaded.photos == session.photos  # pending candidates + regions
    assert reloaded.accepted == session.accepted
    assert reloaded.rejected == session.rejected
    assert reloaded.summary() == session.summary()

    # The reloaded session is live: decisions keep working against the db.
    reloaded.accept(0)  # the pending a:3001
    assert db.list_user_parts() == [
        InventoryItem("3001", "0", 1),
        InventoryItem("3010", "4", 2),
    ]


def test_load_restores_review_state_not_db(db, tmp_path, monkeypatch):
    session = _populated_session(db, monkeypatch, tmp_path)
    path = tmp_path / "session.json"
    session.save(path)

    fresh_db = Database(tmp_path / "fresh.db")
    try:
        reloaded = ScanSession.load(path, fresh_db)
        assert reloaded.photos == session.photos
        assert reloaded.accepted == session.accepted  # log restored...
        assert fresh_db.list_user_parts() == []  # ...but the db NOT replayed
    finally:
        fresh_db.close()


def test_saved_json_shape(db, tmp_path, monkeypatch):
    session = _populated_session(db, monkeypatch, tmp_path)
    path = tmp_path / "session.json"
    session.save(path)

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["version"] == 1
    assert payload["photos"][0]["photo"].endswith("a.jpg")
    assert payload["photos"][0]["candidates"] == [
        {"part_num": "3001", "name": "Brick 2 x 4", "score": 0.9}
    ]
    assert payload["photos"][1]["regions"] == [{"x": 0, "y": 0, "w": 10, "h": 10}]
    assert payload["accepted"] == [
        {"part_num": "3010", "color_id": "4", "quantity": 2}
    ]
    assert payload["rejected"] == 1


def test_save_load_empty_session(db, tmp_path):
    session = ScanSession(db)
    path = tmp_path / "empty.json"
    session.save(path)

    reloaded = ScanSession.load(path, db)

    assert reloaded.photos == []
    assert reloaded.accepted == []
    assert reloaded.rejected == 0


def test_load_tolerates_odd_entries(db, tmp_path):
    # Hand-edited / future-format files degrade entry-by-entry instead
    # of losing the whole session (defensive-JSON project style).
    payload = {
        "version": 1,
        "photos": [
            {
                "photo": "a.jpg",
                "candidates": [
                    {"part_num": "3001", "name": "Brick 2 x 4", "score": 0.9},
                    {"name": "no part number"},  # skipped
                    "junk",  # skipped
                ],
            },
            {"candidates": []},  # no photo -> skipped
            {"photo": "b.jpg", "regions": ["bad", {"x": 1, "y": 2, "w": 3, "h": 4}]},
        ],
        "accepted": [
            {"part_num": "3001", "color_id": "4", "quantity": 2},
            {"color_id": "4"},  # no part_num -> skipped
        ],
        "rejected": "not-an-int",
        "unknown_future_key": True,
    }
    path = tmp_path / "odd.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    session = ScanSession.load(path, db)

    assert [e.photo for e in session.photos] == ["a.jpg", "b.jpg"]
    assert session.photos[0].candidates == [PartCandidate("3001", "Brick 2 x 4", 0.9)]
    assert session.photos[1].regions == [Region(1, 2, 3, 4)]
    assert session.accepted == [InventoryItem("3001", "4", 2)]
    assert session.rejected == 0


def test_load_empty_object_gives_empty_session(db, tmp_path):
    path = tmp_path / "bare.json"
    path.write_text("{}", encoding="utf-8")
    session = ScanSession.load(path, db)
    assert session.photos == []
    assert session.summary().startswith("0 photo(s)")


def test_load_missing_file_raises(db, tmp_path):
    with pytest.raises(FileNotFoundError):
        ScanSession.load(tmp_path / "nope.json", db)


def test_load_rejects_non_object_payload(db, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        ScanSession.load(path, db)