"""Tests for the CLI (run against a throwaway database)."""

from pathlib import Path

from brickfolio.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def run(db_path, *args):
    return main(["--db", str(db_path), *args])


def _first_set_num() -> str:
    """First data row of the fixture sets.csv — a build we can `show`."""
    lines = (FIXTURES / "rebrickable" / "sets.csv").read_text().splitlines()
    return lines[1].split(",")[0]


def test_inventory_add_list_remove(tmp_path, capsys):
    db_path = tmp_path / "cli.db"
    assert run(db_path, "inventory", "add", "3001", "0", "5") == 0
    assert run(db_path, "inventory", "add", "3004", "4", "1") == 0
    assert run(db_path, "inventory", "list") == 0
    out = capsys.readouterr().out
    assert "3001" in out and "3004" in out and "6 parts" in out

    assert run(db_path, "inventory", "remove", "3001", "0", "2") == 0
    assert run(db_path, "inventory", "list") == 0
    out = capsys.readouterr().out
    assert "3001" in out and "3" in out


def test_inventory_remove_error(tmp_path, capsys):
    db_path = tmp_path / "cli.db"
    assert run(db_path, "inventory", "add", "3001", "0", "1") == 0
    assert run(db_path, "inventory", "remove", "3001", "0", "5") == 1
    err = capsys.readouterr().err
    assert "only 1 x" in err


def test_empty_inventory_lists_friendly_hint(tmp_path, capsys):
    assert run(tmp_path / "empty.db", "inventory", "list") == 0
    assert "empty" in capsys.readouterr().out.lower()


def test_stub_commands_report_pending(tmp_path, capsys):
    for cmd in ("generate", "demo"):
        assert run(tmp_path / "stub.db", cmd) == 0
    out = capsys.readouterr().out
    assert out.count("not implemented yet") == 2


# ---- catalog (Prompt 2 wiring) ---------------------------------------------


def test_catalog_import_and_stats(tmp_path, capsys):
    db_path = tmp_path / "cli.db"
    fixtures = FIXTURES / "rebrickable"
    assert run(db_path, "catalog", "import", "--dir", str(fixtures)) == 0
    out = capsys.readouterr().out
    assert "Imported" in out and "skipped" in out

    assert run(db_path, "catalog", "stats") == 0
    out = capsys.readouterr().out
    for table in ("parts", "sets", "inventory_parts", "user_parts"):
        assert table in out


def test_catalog_import_missing_dir_notes_errors(tmp_path, capsys):
    assert run(tmp_path / "cli.db", "catalog", "import", "--dir",
               str(tmp_path / "nope")) == 0
    out = capsys.readouterr().out
    assert "note:" in out  # every missing file becomes exactly one note


# ---- photo identify (Prompt 6 wiring) ----------------------------------------


def test_photo_identify_prints_candidates(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod
    from brickfolio.vision.brickognize import PartCandidate

    monkeypatch.setattr(
        cli_mod,
        "identify_part",
        lambda image, **kw: [PartCandidate("3001", "Brick 2 x 4", 0.93)],
    )
    assert run(tmp_path / "cli.db", "photo", "identify", "x.jpg") == 0
    out = capsys.readouterr().out
    assert "3001" in out and "Brick 2 x 4" in out and "0.93" in out


def test_photo_identify_no_candidates(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod

    monkeypatch.setattr(cli_mod, "identify_part", lambda image, **kw: [])
    assert run(tmp_path / "cli.db", "photo", "identify", "x.jpg") == 0
    assert "No parts recognized" in capsys.readouterr().out


def test_photo_identify_error(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod
    from brickfolio.vision.brickognize import BrickognizeError

    def boom(image, **kw):
        raise BrickognizeError("HTTP 500 from Brickognize")

    monkeypatch.setattr(cli_mod, "identify_part", boom)
    assert run(tmp_path / "cli.db", "photo", "identify", "x.jpg") == 1
    assert "HTTP 500" in capsys.readouterr().err


# ---- builds (Prompt 3 wiring) -------------------------------------------------


def test_builds_list_empty_hint(tmp_path, capsys):
    assert run(tmp_path / "cli.db", "builds", "list") == 0
    assert "No builds" in capsys.readouterr().out


def test_builds_list_and_search(tmp_path, capsys):
    db_path = tmp_path / "cli.db"
    assert run(db_path, "catalog", "import", "--dir",
               str(FIXTURES / "rebrickable")) == 0
    capsys.readouterr()

    assert run(db_path, "builds", "list", "--limit", "3") == 0
    out = capsys.readouterr().out
    assert "Build" in out and _first_set_num() in out

    assert run(db_path, "builds", "search", "Burger") == 0
    out = capsys.readouterr().out
    assert "Burger" in out

    assert run(db_path, "builds", "search", "zzz-no-match") == 0
    assert "No builds match" in capsys.readouterr().out


def test_builds_show_lists_parts(tmp_path, capsys):
    db_path = tmp_path / "cli.db"
    assert run(db_path, "catalog", "import", "--dir",
               str(FIXTURES / "rebrickable")) == 0
    capsys.readouterr()

    assert run(db_path, "builds", "show", _first_set_num()) == 0
    out = capsys.readouterr().out
    assert "Part" in out and "Qty" in out


def test_builds_show_unknown_errors(tmp_path, capsys):
    assert run(tmp_path / "cli.db", "builds", "show", "NOPE-1") == 1
    assert "no build" in capsys.readouterr().err


# ---- photo scan (Prompt 7 wiring) ----------------------------------------------


def test_photo_scan_review_only_adds_nothing(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod
    from brickfolio.vision.brickognize import PartCandidate

    monkeypatch.setattr(
        cli_mod,
        "identify_part",
        lambda image, **kw: [PartCandidate("3001", "Brick 2 x 4", 0.9)],
    )
    db_path = tmp_path / "cli.db"
    assert run(db_path, "photo", "scan", "x.jpg") == 0
    out = capsys.readouterr().out
    assert "[0]" in out and "3001" in out  # summary prints indexed candidates
    assert "Accepted" not in out

    assert run(db_path, "inventory", "list") == 0
    assert "empty" in capsys.readouterr().out.lower()  # nothing was recorded


def test_photo_scan_all_adds_to_inventory(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod
    from brickfolio.vision.brickognize import PartCandidate

    monkeypatch.setattr(
        cli_mod,
        "identify_part",
        lambda image, **kw: [PartCandidate("3001", "Brick 2 x 4", 0.9)],
    )
    db_path = tmp_path / "cli.db"
    assert run(db_path, "photo", "scan", "x.jpg", "--all") == 0
    out = capsys.readouterr().out
    assert "Accepted 1 candidate(s)" in out

    assert run(db_path, "inventory", "list") == 0
    out = capsys.readouterr().out
    assert "3001" in out and "1 parts" in out


def test_photo_scan_accept_specific_index(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod
    from brickfolio.vision.brickognize import PartCandidate

    monkeypatch.setattr(
        cli_mod,
        "identify_part",
        lambda image, **kw: [
            PartCandidate("3001", "Brick 2 x 4", 0.9),
            PartCandidate("3004", "Brick 1x2", 0.8),
        ],
    )
    db_path = tmp_path / "cli.db"
    assert run(db_path, "photo", "scan", "x.jpg", "--accept", "1") == 0
    out = capsys.readouterr().out
    assert "Accepted 1 candidate(s)" in out

    assert run(db_path, "inventory", "list") == 0
    out = capsys.readouterr().out  # only candidate 1 (3004) was accepted
    assert "3004" in out and "3001" not in out


def test_photo_scan_bad_accept_index_fails(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod
    from brickfolio.vision.brickognize import PartCandidate

    monkeypatch.setattr(
        cli_mod,
        "identify_part",
        lambda image, **kw: [PartCandidate("3001", "Brick 2 x 4", 0.9)],
    )
    assert run(tmp_path / "cli.db", "photo", "scan", "x.jpg", "--accept", "5") == 1
    err = capsys.readouterr().err
    assert "index 5" in err


def test_photo_scan_detector_error(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod

    monkeypatch.setattr(cli_mod, "identify_part", lambda image, **kw: [])
    # --regions with no sidecar file -> ManualRegionsDetector raises
    assert run(tmp_path / "cli.db", "photo", "scan", "x.jpg", "--regions") == 1
    err = capsys.readouterr().err
    assert "Error scanning" in err and "x.jpg" in err


def test_photo_scan_saves_session(tmp_path, capsys, monkeypatch):
    import json

    import brickfolio.cli as cli_mod
    from brickfolio.vision.brickognize import PartCandidate

    monkeypatch.setattr(
        cli_mod,
        "identify_part",
        lambda image, **kw: [PartCandidate("3001", "Brick 2 x 4", 0.9)],
    )
    session_path = tmp_path / "session.json"
    assert run(tmp_path / "cli.db", "photo", "scan", "x.jpg",
               "--session", str(session_path)) == 0
    out = capsys.readouterr().out
    assert "Session saved" in out
    payload = json.loads(session_path.read_text())
    assert payload["photos"][0]["candidates"][0]["part_num"] == "3001"