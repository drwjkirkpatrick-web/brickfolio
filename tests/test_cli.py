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
    assert run(tmp_path / "stub.db", "demo") == 0
    assert capsys.readouterr().out.count("not implemented yet") == 1


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


# ---- generate (Prompt 9 wiring) ------------------------------------------------

VALID_LDR = "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3004.dat"
BAD_LDR = "1 4 0 0 0 1 0 0 0 1 0 0 0 1 9999.dat"  # 9999 is not in stock


class _FakeGenerateClient:
    """ChatClient stand-in: scripted replies, no network."""

    def __init__(self, base_url=None, model=None, timeout=60, opener=None,
                 replies=None):
        self.replies = list(replies or [VALID_LDR])
        self.seen_messages = None

    def chat(self, messages, temperature=0.6):
        self.seen_messages = messages
        return self.replies.pop(0) if self.replies else VALID_LDR


def _stock_db(path):
    assert run(path, "inventory", "add", "3004", "4", "2") == 0
    return path


def test_generate_empty_inventory(tmp_path, capsys):
    assert run(tmp_path / "cli.db", "generate", "a tiny car") == 1
    assert "Inventory is empty" in capsys.readouterr().out


def test_generate_writes_valid_model(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod

    fake = _FakeGenerateClient()
    monkeypatch.setattr(cli_mod, "ChatClient", lambda **kw: fake)
    db_path = _stock_db(tmp_path / "cli.db")
    capsys.readouterr()

    out_path = tmp_path / "car.ldr"
    assert run(db_path, "generate", "a tiny car", "--out", str(out_path)) == 0
    out = capsys.readouterr().out
    assert "VALID" in out and "3004" in out and "Model written" in out
    text = out_path.read_text()
    assert "brickfolio:" in text and "3004.dat" in text and "1 4" in text


def test_generate_repair_round_then_valid(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod

    fake = _FakeGenerateClient(replies=[BAD_LDR, VALID_LDR])
    monkeypatch.setattr(cli_mod, "ChatClient", lambda **kw: fake)
    db_path = _stock_db(tmp_path / "cli.db")
    capsys.readouterr()

    assert run(db_path, "generate", "a tiny car", "--rounds", "2") == 0
    out = capsys.readouterr().out
    assert "VALID" in out and "2 round(s)" in out
    # the repair prompt quoted the validator's own error back to the LLM
    assert any("9999" in m.get("content", "") for m in fake.seen_messages)


def test_generate_exhausts_rounds_invalid(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod

    fake = _FakeGenerateClient(replies=[BAD_LDR, BAD_LDR])
    monkeypatch.setattr(cli_mod, "ChatClient", lambda **kw: fake)
    db_path = _stock_db(tmp_path / "cli.db")
    capsys.readouterr()

    assert run(db_path, "generate", "a tiny car", "--rounds", "2") == 1
    out = capsys.readouterr().out
    assert "INVALID" in out and "9999" in out


def test_generate_llm_error(tmp_path, capsys, monkeypatch):
    import brickfolio.cli as cli_mod
    from brickfolio.llm import LLMError

    class DeadClient(_FakeGenerateClient):
        def chat(self, messages, temperature=0.6):
            raise LLMError("connection refused")

    monkeypatch.setattr(cli_mod, "ChatClient", lambda **kw: DeadClient())
    db_path = _stock_db(tmp_path / "cli.db")
    capsys.readouterr()

    assert run(db_path, "generate", "a tiny car") == 1
    assert "LLM call failed" in capsys.readouterr().out


# ---- builds recommend (Prompt 5 wiring) ---------------------------------------


def test_builds_recommend_empty_catalog_hint(tmp_path, capsys):
    assert run(tmp_path / "cli.db", "builds", "recommend") == 0
    assert "No builds" in capsys.readouterr().out


def test_builds_recommend_ready_build(tmp_path, capsys):
    from brickfolio.builds import build_parts, iter_builds
    from brickfolio.db import Database
    from brickfolio.models import InventoryItem

    db_path = tmp_path / "cli.db"
    assert run(db_path, "catalog", "import", "--dir",
               str(FIXTURES / "rebrickable")) == 0
    db = Database(db_path)
    first = next(iter_builds(db))
    for bp in build_parts(db, first.build_num):
        if not bp.is_spare:
            db.add_user_parts(
                [InventoryItem(bp.part_num, bp.color_id, bp.quantity)]
            )
    db.close()
    capsys.readouterr()

    assert run(db_path, "builds", "recommend", "--top", "5") == 0
    out = capsys.readouterr().out
    assert "READY" in out and first.build_num in out


def test_builds_recommend_verbose_missing(tmp_path, capsys):
    from brickfolio.builds import build_parts, iter_builds
    from brickfolio.db import Database

    db_path = tmp_path / "cli.db"
    assert run(db_path, "catalog", "import", "--dir",
               str(FIXTURES / "rebrickable")) == 0
    db = Database(db_path)
    first = next(iter_builds(db))
    first_part = build_parts(db, first.build_num)[0].part_num
    db.close()
    capsys.readouterr()  # inventory stays empty: every part missing

    assert run(db_path, "builds", "recommend", "--verbose", "--top", "10") == 0
    out = capsys.readouterr().out
    assert "0%" in out and first.build_num in out and first_part in out