"""End-to-end smoke test (infra/README.md, Usage): upload inv_00000029 through the ingestor, wait for
its row in PostgreSQL and compare key fields with the golden set. Standard library only.

With the real LLM this costs about $0.0004; in fake mode it is free (the canned extraction is exactly
this invoice's golden data).
"""

import gzip
import json
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVOICE = "inv_00000029"
RUN = ROOT / "tester" / "datasets" / "run-001"
TIMEOUT = 180  # seconds; the real LLM takes a few seconds per invoice


def upload(pdf: Path) -> dict:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{pdf.name}\"\r\n"
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + pdf.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("http://127.0.0.1:8080/documents", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def query(sql: str) -> str:
    out = subprocess.run(
        ["docker", "compose", "--env-file", ".env", "exec", "-T", "postgres",
         "psql", "-U", "postgres", "-d", "invoices", "-tA", "-F", "|", "-c", sql],
        cwd=Path(__file__).parent, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def golden() -> dict:
    with gzip.open(RUN / "golden/invoices/unit-000000.jsonl.gz") as f:
        row = next(r for r in map(json.loads, f) if r["invoice_id"] == INVOICE)
    with gzip.open(RUN / "golden/line_items/unit-000000.jsonl.gz") as f:
        lines = sum(1 for r in map(json.loads, f) if r["invoice_id"] == INVOICE and r["parent_line_no"] is None)
    value = lambda v: v.get("__decimal__") or v.get("__date__") if isinstance(v, dict) else v  # noqa: E731
    return {"supplier_name": row["issuer_name"], "invoice_number": row["invoice_number"],
            "issue_date": value(row["issue_date"]), "currency": row["currency"],
            "grand_total": value(row["grand_total"]), "lines": str(lines)}


def main() -> int:
    pdf = RUN / f"documents/unit-000000/{INVOICE}.pdf"
    if not pdf.exists():
        print(f"{pdf} not found (tester/datasets symlink)")
        return 1
    expected = golden()
    accepted = upload(pdf)
    doc = accepted["document_id"]
    print(f"uploaded {INVOICE}: document_id {doc}")
    sql = (f"SELECT i.supplier_name, i.invoice_number, i.issue_date, i.currency, i.grand_total, count(l.*) "
           f"FROM invoices i JOIN invoice_lines l ON l.invoice_id = i.id WHERE i.document_id = '{doc}' "
           f"GROUP BY i.id")
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        row = query(sql)
        if row:
            break
        time.sleep(2)
    else:
        print(f"no row for {doc} after {TIMEOUT}s; see `make logs`")
        return 1
    got = dict(zip(["supplier_name", "invoice_number", "issue_date", "currency", "grand_total", "lines"], row.split("|")))
    bad = {k: (got[k], v) for k, v in expected.items() if got[k] != v}
    for k, v in expected.items():
        print(f"  {k:15} {got[k]!r:32} {'ok' if k not in bad else f'expected {v!r}'}")
    print("smoke test passed" if not bad else "smoke test FAILED")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
