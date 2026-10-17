"""Shared pytest fixtures for brickfolio."""

import pytest

from brickfolio.db import Database


@pytest.fixture()
def db(tmp_path):
    """A fresh Database on a throwaway file; closed after each test."""
    database = Database(tmp_path / "test.db")
    yield database
    database.close()