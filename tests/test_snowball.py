"""Tests for snowball module."""

import sqlite3

import pytest

from src.db_schema import create_schema
from src.snowball import _normalise_reference, _ensure_source_snowball


def test_normalise_reference_with_doi_and_title():
    ref = {
        "DOI": "10.1234/test",
        "title": "A Paper on PUFs",
        "authors": [{"name": "Smith, John"}],
        "year": 2023,
        "abstract": "An abstract.",
    }
    result = _normalise_reference(ref)
    assert result is not None
    assert result["doi"] == "10.1234/test"
    assert result["title"] == "A Paper on PUFs"
    assert result["authors"] == "Smith, John"
    assert result["year"] == 2023


def test_normalise_reference_missing_title_and_doi():
    ref = {"authors": [{"name": "Smith, John"}]}
    result = _normalise_reference(ref)
    assert result is None


def test_normalise_reference_list_title():
    ref = {"DOI": "10.1234/test", "title": ["Title One", "Title Two"], "year": 2023}
    result = _normalise_reference(ref)
    assert result["title"] == "Title One"


def test_ensure_source_snowball():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    sid1 = _ensure_source_snowball(conn)
    sid2 = _ensure_source_snowball(conn)
    assert sid1 == sid2
    assert sid1 > 0
