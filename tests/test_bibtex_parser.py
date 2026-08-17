"""Tests for BibTeX parser."""

import pytest

from src.bibtex_parser import parse_bibtex


SIMPLE_ARTICLE = """
@article{smith2023,
  author = {Smith, John and Doe, Jane},
  title = {A Study on PUFs},
  journal = {IEEE Transactions on Hardware Security},
  year = {2023},
  volume = {10},
  pages = {1--10},
  doi = {10.1234/example},
  abstract = {This paper studies physical unclonable functions.},
  keywords = {PUF, hardware security}
}
"""


MULTILINE_ABSTRACT = """
@inproceedings{doe2024,
  author = {Doe, Jane and Smith, John},
  title = {Side-Channel Attacks on Arbiter PUFs},
  booktitle = {Proceedings of CHES},
  year = {2024},
  abstract = {We present a novel side-channel attack on arbiter PUFs
              using power analysis techniques. Our method achieves 99% accuracy.},
  doi = {10.5678/example2}
}
"""


MULTIPLE_ENTRIES = """
@article{one2022,
  author = {One, Alice},
  title = {First Paper},
  year = {2022},
  abstract = {Abstract one.}
}
@article{two2023,
  author = {Two, Bob},
  title = {Second Paper},
  year = {2023},
  abstract = {Abstract two.}
}
"""


EMPTY_FIELDS = """
@article{empty2021,
  author = {Author, Name},
  title = {Paper with missing fields},
  year = {2021},
}
"""


def test_parse_single_article():
    entries = parse_bibtex(SIMPLE_ARTICLE)
    assert len(entries) == 1
    e = entries[0]
    assert e["entry_type"] == "article"
    assert e["cite_key"] == "smith2023"
    assert e["author"] == "Smith, John and Doe, Jane"
    assert e["title"] == "A Study on PUFs"
    assert e["journal"] == "IEEE Transactions on Hardware Security"
    assert e["year"] == "2023"
    assert e["doi"] == "10.1234/example"
    assert "physical unclonable functions" in e["abstract"]


def test_parse_multiline_abstract():
    entries = parse_bibtex(MULTILINE_ABSTRACT)
    assert len(entries) == 1
    e = entries[0]
    assert "side-channel attack" in e["abstract"]
    assert "arbiter PUFs" in e["abstract"]


def test_parse_multiple_entries():
    entries = parse_bibtex(MULTIPLE_ENTRIES)
    assert len(entries) == 2
    assert entries[0]["title"] == "First Paper"
    assert entries[1]["title"] == "Second Paper"


def test_parse_empty_fields():
    entries = parse_bibtex(EMPTY_FIELDS)
    assert len(entries) == 1
    assert entries[0]["title"] == "Paper with missing fields"


def test_parse_returns_list():
    result = parse_bibtex("")
    assert isinstance(result, list)
    assert len(result) == 0


UNBALANCED_BRACES = """
@inproceedings{pham2024,
  title = {{SRAM}-based {Physically} {Unclonable} {Function} using {Lightweight} {Hamming}-{Code} {Fuzzy} {Extractor} for {Energy} {Harvesting} {Beat} {Sensors}},
  year = {2024},
  doi = {10.1109/ATC63255.2024.10908150}
}
"""


def test_parse_unbalanced_braces_terminates():
    # Regression: brace-unbalanced values must not hang _strip_outer_braces.
    entries = parse_bibtex(UNBALANCED_BRACES)
    assert len(entries) == 1
    assert entries[0]["doi"] == "10.1109/ATC63255.2024.10908150"
