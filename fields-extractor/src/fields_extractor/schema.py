"""Extraction schema (see fields-extractor/specs/extraction-schema.md).

`OUTPUT_SCHEMA` constrains the LLM response (structured outputs); `validate` applies the
module's own checks (spec overview, Validation), so every provider is held to the same rules.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _decimal(v: str) -> str:
    if not _DECIMAL.match(v):
        raise ValueError(f"{v!r} is not a plain decimal like 1234.56 (no symbols or separators)")
    try:
        Decimal(v)
    except InvalidOperation as e:
        raise ValueError(f"{v!r} is not a decimal") from e
    return v


def _date(v: str) -> str:
    try:
        if len(v) != 10:
            raise ValueError
        date.fromisoformat(v)
    except ValueError as e:
        raise ValueError(f"{v!r} is not a valid YYYY-MM-DD date") from e
    return v


def _currency(v: str) -> str:
    if not _CURRENCY.match(v):
        raise ValueError(f"{v!r} is not a 3-letter uppercase ISO 4217 code")
    return v


def _non_empty(v: str) -> str:
    if not v.strip():
        raise ValueError("must not be empty")
    return v


Text = Annotated[str, AfterValidator(_non_empty)]
DecimalStr = Annotated[str, AfterValidator(_decimal)]
DateStr = Annotated[str, AfterValidator(_date)]
Currency = Annotated[str, AfterValidator(_currency)]


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    line_no: int
    description: Text
    quantity: DecimalStr | None
    unit_price: DecimalStr | None
    amount: DecimalStr


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    supplier_name: Text
    recipient_name: str | None
    invoice_number: Text
    issue_date: DateStr
    due_date: DateStr | None
    currency: Currency
    subtotal: DecimalStr | None
    tax_total: DecimalStr | None
    grand_total: DecimalStr
    tax_rate: DecimalStr | None
    lines: list[Line] = Field(min_length=1)

    @model_validator(mode="after")
    def _line_numbers(self):
        numbers = [line.line_no for line in self.lines]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(f"line_no must run 1..{len(numbers)} in order, got {_shorten(numbers)}")
        return self


class InvalidOutput(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate(text: str) -> Extraction:
    """Parse and validate the model's JSON text. Raises InvalidOutput with readable errors."""
    try:
        return Extraction.model_validate_json(text)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "(root)"
            msg = err["msg"].removeprefix("Value error, ")
            errors.append(f"{loc}: {msg}")
        raise InvalidOutput(errors) from e


def line_sum_mismatch(x: Extraction) -> str | None:
    """Feedback text when the line amounts don't add up to the printed subtotal, else None."""
    if x.subtotal is None:
        return None
    total = sum(Decimal(line.amount) for line in x.lines)
    if total == Decimal(x.subtotal):
        return None
    return (
        f"the line amounts sum to {total} but the printed subtotal is {x.subtotal}; check for totals-area rows "
        "(discount, shipping, tax, deposit) listed as line items and for missing line items"
    )


def plausibility(x: Extraction) -> list[str]:
    """Checks that only warn (spec overview, Validation). Returns the names of failed checks."""
    failed = []
    if x.subtotal is not None and sum(Decimal(line.amount) for line in x.lines) != Decimal(x.subtotal):
        failed.append("lines_sum_to_subtotal")
    if (
        x.subtotal is not None
        and x.tax_total is not None
        and Decimal(x.subtotal) + Decimal(x.tax_total) != Decimal(x.grand_total)
    ):
        failed.append("subtotal_plus_tax_is_total")
    return failed


def _shorten(numbers: list[int]) -> str:
    return str(numbers) if len(numbers) <= 12 else f"{numbers[:6]}...{numbers[-3:]}"


_STR = {"type": "string"}
_NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_DATE = {"type": "string", "format": "date"}
_NULLABLE_DATE = {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]}


def _obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


OUTPUT_SCHEMA = _obj(
    {
        "supplier_name": _STR,
        "recipient_name": _NULLABLE_STR,
        "invoice_number": _STR,
        "issue_date": _DATE,
        "due_date": _NULLABLE_DATE,
        "currency": _STR,
        "subtotal": _NULLABLE_STR,
        "tax_total": _NULLABLE_STR,
        "grand_total": _STR,
        "tax_rate": _NULLABLE_STR,
        "lines": {
            "type": "array",
            "items": _obj(
                {
                    "line_no": {"type": "integer"},
                    "description": _STR,
                    "quantity": _NULLABLE_STR,
                    "unit_price": _NULLABLE_STR,
                    "amount": _STR,
                }
            ),
        },
    }
)
