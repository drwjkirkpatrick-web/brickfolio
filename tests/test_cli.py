"""Tests for the CLI (run against a throwaway database)."""

from brickfolio.cli import main


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
    for cmd in ("catalog", "builds", "photo", "generate", "demo"):
        assert run(tmp_path / "stub.db", cmd) == 0
    out = capsys.readouterr().out
    assert out.count("not implemented yet") == 5