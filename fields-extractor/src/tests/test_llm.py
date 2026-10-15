"""Real LLM tests (`make llm-test`): call the Anthropic API, cost money, on demand.

Needs ANTHROPIC_API_KEY. The sample test also needs the run-001 dataset (tester/datasets) and
text-extractor's environment to convert PDFs (`make sync` in text-extractor), or
TEXT_EXTRACTOR_PYTHON pointing to a Python that can import text_extractor.

Accuracy is reported, not enforced (judging quality is the tester's job); tests fail only on
clear regressions: output that fails validation, a refusal, truncation.
"""

import collections
import gzip
import json
import os
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from fields_extractor.llm import PROMPT_VERSION
from fields_extractor.llm.anthropic_provider import AnthropicProvider
from fields_extractor.llm.pricing import Pricing
from fields_extractor.processor import Processor
from tests.spec_examples import extraction_example, layout_example

pytestmark = pytest.mark.llm

ROOT = Path(__file__).parents[3]
RUN = Path(os.environ.get("CORPUS_DIR", ROOT / "tester" / "datasets")) / "run-001"
MODEL = "claude-haiku-5-5"
SAMPLE_SIZE = 10
HEADER = {  # extraction field -> golden field (extraction-schema.md)
    "supplier_name": "issuer_name",
    "recipient_name": "recipient_name",
    "invoice_number": "invoice_number",
    "issue_date": "issue_date",
    "due_date": "due_date",
    "currency": "currency",
    "subtotal": "subtotal",
    "tax_total": "tax_total",
    "grand_total": "grand_total",
}
LINE = {"description": "description", "quantity": "quantity", "unit_price": "unit_price", "amount": "extended_amount"}


@pytest.fixture(scope="module")
def provider():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.fail("ANTHROPIC_API_KEY is not set (put it in fields-extractor/.env)")
    usage = []
    p = AnthropicProvider(MODEL, "low", 4, on_usage=lambda m, i, o: usage.append((m, i, o)))
    p.usage = usage
    return p


def extract(provider, markdown, attempts=None):
    """The processor's own extraction logic (validation, feedback retry, line-sum retry)."""
    x, tries = Processor(None, None, provider)._extract(markdown)
    if attempts is not None:
        attempts.extend(tries)
    return x, len(tries)


def cost(usage):
    pricing = Pricing(MODEL)
    return sum((pricing.cost(m, i, o) for m, i, o in usage), Decimal(0))


def test_spec_example_matches_golden(provider):
    x, attempts = extract(provider, layout_example())
    assert x.model_dump(mode="json") == extraction_example()


# --- run-001 sample ---------------------------------------------------------------------------


def _golden():
    if not RUN.is_dir():
        pytest.skip(f"{RUN} not found")
    with gzip.open(RUN / "golden/invoices/unit-000000.jsonl.gz") as f:
        invoices = {r["invoice_id"]: r for r in map(json.loads, f)}
    lines = collections.defaultdict(list)
    with gzip.open(RUN / "golden/line_items/unit-000000.jsonl.gz") as f:
        for r in map(json.loads, f):
            if r["parent_line_no"] is None:  # top-level lines only (extraction-schema.md)
                lines[r["invoice_id"]].append(r)
    return invoices, lines


def select_sample(invoices: dict, lines: dict, size: int = SAMPLE_SIZE) -> list[str]:
    """Deterministic sample: every template and currency, a tiered, a fr-CA and a multi-page telecom invoice."""
    with gzip.open(RUN / "golden/line_items/unit-000000.jsonl.gz") as f:
        tiered = {r["invoice_id"] for r in map(json.loads, f) if r["parent_line_no"] is not None}
    ids = sorted(invoices)
    picked: list[str] = []

    def pick(pred):
        for i in ids:
            if i not in picked and pred(invoices[i]):
                picked.append(i)
                return

    pick(lambda r: r["language"] == "fr-CA")
    pick(lambda r: r["invoice_id"] in tiered)
    pick(lambda r: r["template_id"].startswith("telecom") and len(lines[r["invoice_id"]]) < 80)
    for template in sorted({r["template_id"] for r in invoices.values()}):
        if not any(invoices[i]["template_id"] == template for i in picked):
            pick(lambda r, t=template: r["template_id"] == t)
    for currency in sorted({r["currency"] for r in invoices.values()}):
        if not any(invoices[i]["currency"] == currency for i in picked):
            pick(lambda r, c=currency: r["currency"] == c)
    while len(picked) < size:
        pick(lambda r: True)
    return picked[:size]


def layouts(invoice_ids: list[str]) -> dict[str, str]:
    """Convert PDFs with text-extractor (its own environment, so the modules stay independent)."""
    python = os.environ.get("TEXT_EXTRACTOR_PYTHON") or str(ROOT / "text-extractor/src/.venv/bin/python")
    if not Path(python).exists():
        pytest.skip(f"{python} not found: run `make sync` in text-extractor or set TEXT_EXTRACTOR_PYTHON")
    script = (
        "import json, sys\nfrom text_extractor.convert import convert\n"
        "paths = json.loads(sys.argv[1])\n"
        "print(json.dumps({i: convert(open(p, 'rb').read(), i).markdown for i, p in paths.items()}))"
    )
    paths = {i: str(RUN / f"documents/unit-000000/{i}.pdf") for i in invoice_ids}
    out = subprocess.run(
        [python, "-c", script, json.dumps(paths)],
        cwd=ROOT / "text-extractor/src",
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def _value(golden):
    if isinstance(golden, dict):
        return golden.get("__decimal__") or golden.get("__date__")
    return golden


def _same(extracted, golden) -> bool:
    golden = _value(golden)
    if extracted is None or golden is None:
        return extracted is None and golden is None
    try:
        return Decimal(str(extracted)) == Decimal(str(golden))
    except Exception:
        return " ".join(str(extracted).split()).casefold() == " ".join(str(golden).split()).casefold()


def match_lines(extracted: list, golden: list) -> dict:
    """Order-independent matching (telecom prints lines in a different order than golden line_no).

    Pass 1 pairs lines with the same amount and description, pass 2 the remaining ones with the same
    amount. Returns counts and the paired lines for field comparison.
    """
    left = list(extracted)
    pairs, amount_only, missing = [], [], []
    for strict in (True, False):
        rest = []
        for g in golden if strict else missing:
            hit = next(
                (
                    x
                    for x in left
                    if _same(x.amount, g["extended_amount"]) and (not strict or _same(x.description, g["description"]))
                ),
                None,
            )
            if hit is None:
                rest.append(g)
                continue
            left.remove(hit)
            (pairs if strict else amount_only).append((hit, g))
        missing = rest
    return {"matched": pairs, "amount_only": amount_only, "missing": missing, "extra": left}


def test_run001_sample(provider):
    invoices, lines = _golden()
    sample = select_sample(invoices, lines)
    texts = layouts(sample)
    out_dir = Path(os.environ.get("LLM_TEST_OUTPUT_DIR") or Path(tempfile.gettempdir()) / "fields-extractor-llm-test")
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = collections.Counter()
    totals = collections.Counter()
    rows, misses, line_notes, attempts_total = [], [], [], 0
    start = len(provider.usage)
    for iid in sample:
        g, gl = invoices[iid], sorted(lines[iid], key=lambda r: r["line_no"])
        tries = []
        try:
            x, attempts = extract(provider, texts[iid], tries)  # fails the test on refusal, truncation, invalid twice
        finally:
            (out_dir / f"{iid}.attempts.json").write_text(
                json.dumps(
                    [
                        {"stop_reason": a.stop_reason, "validation_errors": a.validation_errors, "output": a.output}
                        for a in tries
                    ],
                    indent=2,
                    ensure_ascii=False,
                )
            )
        attempts_total += attempts
        got = x.model_dump(mode="json")
        (out_dir / f"{iid}.layout.md").write_text(texts[iid])
        (out_dir / f"{iid}.extracted.json").write_text(json.dumps(got, indent=2, ensure_ascii=False))
        (out_dir / f"{iid}.golden.json").write_text(
            json.dumps(
                {
                    "invoice": {f: _value(g[gf]) for f, gf in HEADER.items()},
                    "lines": [{f: _value(r[gf]) for f, gf in LINE.items()} | {"line_no": r["line_no"]} for r in gl],
                },
                indent=2,
                ensure_ascii=False,
            )
        )

        ok = {f: _same(got[f], g[gf]) for f, gf in HEADER.items()}
        fields.update(f for f, good in ok.items() if good)
        misses += [
            f"  {iid} {f}: got {got[f]!r}, expected {_value(g[HEADER[f]])!r}" for f, good in ok.items() if not good
        ]

        m = match_lines(x.lines, gl)
        totals.update(
            golden=len(gl),
            matched=len(m["matched"]),
            amount_only=len(m["amount_only"]),
            missing=len(m["missing"]),
            extra=len(m["extra"]),
        )
        for xl, gline in m["matched"] + m["amount_only"]:
            totals.update(
                paired=1,
                quantity=_same(xl.quantity, gline["quantity"]),
                unit_price=_same(xl.unit_price, gline["unit_price"]),
            )
        if m["missing"] or m["extra"] or m["amount_only"]:
            examples = [f"missing {r['description']!r} {_value(r['extended_amount'])}" for r in m["missing"][:2]]
            examples += [f"extra {xl.description!r} {xl.amount}" for xl in m["extra"][:2]]
            examples += [
                f"description {xl.description!r} vs {gline['description']!r}" for xl, gline in m["amount_only"][:2]
            ]
            line_notes.append(
                f"  {iid}: matched {len(m['matched'])}, amount only {len(m['amount_only'])}, "
                f"missing {len(m['missing'])}, extra {len(m['extra'])}; " + "; ".join(examples)
            )
        rows.append((iid, g["template_id"], g["currency"], len(gl), len(x.lines), sum(ok.values()), attempts))

    used = provider.usage[start:]
    paired = totals["paired"] or 1
    report = [
        "",
        f"run-001 sample: {len(sample)} invoices, model {MODEL}, prompt {PROMPT_VERSION}; outputs in {out_dir}",
        f"{'invoice':14} {'template':22} {'cur':4} {'lines':>5} {'got':>5} {'header':>7} {'tries':>5}",
        *(f"{i:14} {t:22} {c:4} {n:5} {m:5} {h:4}/{len(HEADER)} {a:5}" for i, t, c, n, m, h, a in rows),
        "header field accuracy: " + ", ".join(f"{f} {fields[f]}/{len(sample)}" for f in HEADER),
        "header misses:",
        *(misses or ["  none"]),
        f"lines (order-independent): golden {totals['golden']}, matched (description + amount) {totals['matched']}, "
        f"amount only {totals['amount_only']}, missing {totals['missing']}, extra {totals['extra']}",
        f"paired lines: quantity {totals['quantity']}/{totals['paired']} ({totals['quantity'] / paired:.0%}), "
        f"unit_price {totals['unit_price']}/{totals['paired']} ({totals['unit_price'] / paired:.0%})",
        "line problems:",
        *(line_notes or ["  none"]),
        f"requests {len(used)} (feedback retries {attempts_total - len(sample)}), "
        f"tokens in {sum(u[1] for u in used)} / out {sum(u[2] for u in used)}, cost ${cost(used):.4f}",
    ]
    print("\n".join(report))
