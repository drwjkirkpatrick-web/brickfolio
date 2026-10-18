"""Tests for the CLI (run against a throwaway database)."""

from pathlib import Path

from brickfolio.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def run(db_path, *args):
    return main(["--db", str(db_path), *args])


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
    for cmd in ("builds", "generate", "demo"):
        assert run(tmp_path / "stub.db", cmd) == 0
    out = capsys.readouterr().out
    assert out.count("not implemented yet") == 3


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


# ---- photo (Prompt 6 wiring) ------------------------------------------------


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