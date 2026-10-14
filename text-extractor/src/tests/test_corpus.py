"""On-demand regression over run-001 (`make corpus-test`).

Dataset root: $CORPUS_DIR or ../../tester/datasets (see tester/backend/specs/dataset-format.md).
"""

import collections
import gzip
import json
import os
import re
from pathlib import Path

import pytest

from text_extractor.convert import convert

pytestmark = pytest.mark.corpus

ROOT = Path(os.environ.get("CORPUS_DIR", Path(__file__).parents[3] / "tester" / "datasets"))
RUN = ROOT / "run-001"
SPEC = Path(__file__).parents[2] / "specs" / "output-format.md"
DOC = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _jsonl(path):
    with gzip.open(path) as f:
        return [json.loads(line) for line in f]


@pytest.fixture(scope="module")
def corpus():
    if not RUN.is_dir():
        pytest.skip(f"{RUN} not found")
    invoices = {r["invoice_id"]: r for r in _jsonl(RUN / "golden/invoices/unit-000000.jsonl.gz")}
    lines = collections.defaultdict(list)
    for r in _jsonl(RUN / "golden/line_items/unit-000000.jsonl.gz"):
        lines[r["invoice_id"]].append(r)
    markdown = {iid: convert((RUN / f"documents/unit-000000/{iid}.pdf").read_bytes(), DOC).markdown for iid in invoices}
    return invoices, lines, markdown


def tokens(text: str) -> list[str]:
    """Words in printed form: no thousands separators, '_' as space.

    Hyphenated words are split after the hyphen ("add-on" -> "add-", "on"), because a cell may wrap
    there and put other columns between the two halves.
    """
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text).replace("_", " ")
    text = re.sub(r"(?<=\w)-(?=\w)", "- ", text)
    return text.split()


def appears(needle: list[str], hay: list[str], window: int = 40) -> bool:
    """needle's words occur in order, within `window` extra words (wrapped cells put other columns in between)."""
    for start in (i for i, t in enumerate(hay) if t == needle[0]):
        pos = start
        for t in needle:
            try:
                pos = hay.index(t, pos, start + len(needle) + window) + 1
            except ValueError:
                break
        else:
            return True
    return False


def test_every_document_converts(corpus):
    invoices, _, markdown = corpus
    assert len(markdown) == len(invoices) == 200


def test_line_descriptions_are_present(corpus):
    _, lines, markdown = corpus
    missing = []
    for iid, items in lines.items():
        hay = tokens(markdown[iid])
        found = {id(r): appears(tokens(r["description"]), hay) for r in items}
        children = [r for r in items if r["parent_line_no"] is not None]
        tiers_printed = any(found[id(r)] for r in children)
        for r in items:
            is_unprinted_tier = r["parent_line_no"] is not None and not tiers_printed
            if not found[id(r)] and not is_unprinted_tier:
                missing.append((iid, r["line_no"], r["description"]))
    assert missing == []


def test_no_watermark_letters(corpus):
    invoices, _, markdown = corpus
    single_letters = {
        iid: re.findall(r"(?<!\S)[A-Z](?!\S)", markdown[iid]) for iid, r in invoices.items() if r["has_watermark"]
    }
    assert len(single_letters) == 27
    assert {iid: s for iid, s in single_letters.items() if s} == {}


def test_spec_example_matches(corpus):
    _, _, markdown = corpus
    spec = SPEC.read_text()
    example = spec.split("````markdown\n", 1)[1].split("\n````", 1)[0]
    assert markdown["inv_00000029"].rstrip("\n") == example
