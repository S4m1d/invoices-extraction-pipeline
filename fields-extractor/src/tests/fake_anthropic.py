"""A fake Anthropic Messages API (streaming only) for tests. Standard library only.

In tests: start FakeAnthropic(), queue responses, point the SDK at .url.
Standalone (Docker smoke test): `python -m tests.fake_anthropic --port 8089` answers every
request with EXAMPLE_OUTPUT.
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# extraction-schema.md example (inv_00000029)
EXAMPLE_OUTPUT = {
    "supplier_name": "Willowmere Halcyon Ltd",
    "recipient_name": "Vireo Labs",
    "invoice_number": "INV-2025-000029",
    "issue_date": "2025-07-25",
    "due_date": "2025-08-24",
    "currency": "GBP",
    "subtotal": "774.05",
    "tax_total": "154.81",
    "grand_total": "928.86",
    "tax_rate": "20",
    "lines": [
        {
            "line_no": 1,
            "description": "Premium support add-on",
            "quantity": "1",
            "unit_price": "268.21",
            "amount": "268.21",
        },
        {
            "line_no": 2,
            "description": "Additional user seats",
            "quantity": "6",
            "unit_price": "30.27",
            "amount": "181.62",
        },
        {
            "line_no": 3,
            "description": "Growth plan — monthly subscription",
            "quantity": "1",
            "unit_price": "324.22",
            "amount": "324.22",
        },
    ],
}


def text(output, stop_reason="end_turn", model="claude-haiku-5-5", stop_details=None):
    """A successful streamed response whose text is `output` (dict → JSON)."""
    if not isinstance(output, str):
        output = json.dumps(output)
    return {"kind": "stream", "text": output, "stop_reason": stop_reason, "model": model, "stop_details": stop_details}


def refusal(category="cyber"):
    return text(
        "", stop_reason="refusal", stop_details={"type": "refusal", "category": category, "explanation": "declined"}
    )


def error(status, error_type="api_error", message="error", headers=None, details=None):
    return {
        "kind": "error",
        "status": status,
        "type": error_type,
        "message": message,
        "headers": headers or {},
        "details": details,
    }


def spend_cap():
    """Tier monthly spend cap (429, documented body)."""
    return error(
        429,
        "rate_limit_error",
        "You have reached your API usage limits: your organization has crossed its monthly API usage "
        "threshold, set based on your organization's API tier. You will regain access on 2026-11-01 at "
        "00:00 UTC.",
        details={"error_code": "enforced_spend_limit_reached"},
    )


def spend_limit(workspace=False):
    """A spend limit set on the organization or workspace (400, documented message prefix)."""
    scope = "workspace API" if workspace else "API"
    return error(
        400,
        "invalid_request_error",
        f"You have reached your specified {scope} usage limits. You will regain access on 2026-11-01 at 00:00 UTC.",
    )


def no_credit(status=400):
    return error(
        status,
        "invalid_request_error" if status == 400 else "billing_error",
        "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to "
        "upgrade or purchase credits.",
    )


class FakeAnthropic:
    def __init__(self, port=0, default=None):
        self.responses: list[dict] = []
        self.default = default  # used when the queue is empty
        self.requests: list[dict] = []  # {"headers": {...}, "body": {...}}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                fake.requests.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                resp = fake.responses.pop(0) if fake.responses else fake.default
                if resp is None:
                    resp = error(500, message="no response queued")
                if resp["kind"] == "error":
                    err = {"type": resp["type"], "message": resp["message"]}
                    if resp.get("details"):
                        err["details"] = resp["details"]
                    data = json.dumps({"type": "error", "error": err, "request_id": "req_fake"})
                    self.send_response(resp["status"])
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    for k, v in resp["headers"].items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(data.encode())
                    return
                if not body.get("stream"):  # non-streaming request (usage limit check)
                    data = json.dumps(
                        {
                            "id": "msg_fake",
                            "type": "message",
                            "role": "assistant",
                            "model": resp.get("model", "fake"),
                            "content": [{"type": "text", "text": "OK"}],
                            "stop_reason": "end_turn",
                            "stop_sequence": None,
                            "usage": {"input_tokens": 10, "output_tokens": 1},
                        }
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data.encode())
                    return
                self._stream(resp, body)

            def _stream(self, resp, request):
                model = resp["model"]
                events = [
                    (
                        "message_start",
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_fake",
                                "type": "message",
                                "role": "assistant",
                                "model": model,
                                "content": [],
                                "stop_reason": None,
                                "stop_sequence": None,
                                "usage": {"input_tokens": 100, "output_tokens": 1},
                            },
                        },
                    ),
                ]
                if resp["text"]:
                    events += [
                        (
                            "content_block_start",
                            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                        ),
                        (
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": resp["text"]},
                            },
                        ),
                        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ]
                delta = {"stop_reason": resp["stop_reason"], "stop_sequence": None}
                if resp["stop_details"]:
                    delta["stop_details"] = resp["stop_details"]
                events += [
                    (
                        "message_delta",
                        {
                            "type": "message_delta",
                            "delta": delta,
                            "usage": {"output_tokens": max(1, len(resp["text"]) // 4)},
                        },
                    ),
                    ("message_stop", {"type": "message_stop"}),
                ]
                payload = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self):
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8089)
    ap.add_argument("--status", type=int, help="answer every request with this HTTP error status")
    ap.add_argument(
        "--spend-limit-for",
        type=float,
        metavar="SECONDS",
        help="answer with the spend-limit error until this many seconds after the first request",
    )
    args = ap.parse_args()
    default = error(args.status, message=f"fake {args.status}") if args.status else text(EXAMPLE_OUTPUT)
    fake = FakeAnthropic(args.port, default=default)
    if args.spend_limit_for:
        import time

        first = []

        class Limited(list):
            """Pops spend-limit errors until the time is up, then falls through to the default."""

            def pop(self, i=-1):
                first.append(first[0] if first else time.monotonic())
                return spend_limit()

            def __bool__(self):
                return not first or time.monotonic() - first[0] < args.spend_limit_for

        fake.responses = Limited()
    print(f"fake anthropic on :{args.port}", flush=True)
    fake.server.serve_forever()
