"""Examples taken from the specs, so tests and specs cannot drift apart."""

import json
from pathlib import Path

ROOT = Path(__file__).parents[3]


def extraction_example() -> dict:
    text = (ROOT / "fields-extractor/specs/extraction-schema.md").read_text()
    return json.loads(text.split("## Example\n\n```json\n", 1)[1].split("\n```")[0])


def layout_example() -> str:
    text = (ROOT / "text-extractor/specs/output-format.md").read_text()
    return text.split("````markdown\n", 1)[1].split("\n````")[0]
