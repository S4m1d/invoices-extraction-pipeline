"""On-demand OCR check over run003 light scans (`make corpus-test`); needs tesseract.

Dataset root: $CORPUS_DIR or ../../tester/datasets (see tester/backend/specs/dataset-format.md).
"""

import os
import shutil
from pathlib import Path

import pytest

from text_extractor.convert import convert

pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed"),
]

ROOT = Path(os.environ.get("CORPUS_DIR", Path(__file__).parents[3] / "tester" / "datasets"))
RUN = ROOT / "run003"
DOC = "0f8fad5b-d9cb-469f-a165-70867728950e"


@pytest.fixture(scope="module")
def results():
    if not RUN.is_dir():
        pytest.skip(f"{RUN} not found")
    return {f.stem: convert(f.read_bytes(), DOC) for f in sorted((RUN / "documents/unit-000000").glob("*.pdf"))}


def test_every_scanned_document_converts_with_all_pages_ocred(results):
    assert len(results) == 10
    for iid, res in results.items():
        n = res.info.page_count
        assert res.ocr_pages == list(range(1, n + 1)), iid
        assert sum(line.endswith(" (OCR)") for line in res.markdown.splitlines() if line.startswith("## ")) == n
        assert all(res.info.ocr_chars_per_page), iid
