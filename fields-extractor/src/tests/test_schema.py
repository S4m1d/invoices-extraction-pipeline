import copy
import json

import pytest

from fields_extractor import schema
from tests.spec_examples import extraction_example

EXAMPLE = extraction_example()


def with_(**changes) -> dict:
    x = copy.deepcopy(EXAMPLE)
    x.update(changes)
    return x


def line(**changes) -> dict:
    ln = copy.deepcopy(EXAMPLE["lines"][0])
    ln.update(changes)
    return ln


def errors_of(doc: dict) -> list[str]:
    with pytest.raises(schema.InvalidOutput) as exc:
        schema.validate(json.dumps(doc))
    return exc.value.errors


def test_spec_example_is_valid():
    x = schema.validate(json.dumps(EXAMPLE))
    assert x.model_dump(mode="json") == EXAMPLE


@pytest.mark.parametrize("value", ["1234.56", "-5.00", "0.0025", "0", "10"])
def test_valid_decimals(value):
    schema.validate(json.dumps(with_(grand_total=value)))


@pytest.mark.parametrize("value", ["1,234.50", "$5", "5.", ".5", "1e3", " 5", "5 ", "", "1.234,50", "--5"])
def test_invalid_decimals(value):
    errs = errors_of(with_(grand_total=value))
    assert errs and errs[0].startswith("grand_total:")


def test_numbers_must_be_strings():
    assert errors_of(with_(grand_total=928.86))


@pytest.mark.parametrize("value", ["2025-02-30", "25/07/2025", "2025-7-25", "2025-07-25T00:00:00", ""])
def test_invalid_dates(value):
    assert any(e.startswith("issue_date:") for e in errors_of(with_(issue_date=value)))


def test_due_date_may_be_null():
    schema.validate(json.dumps(with_(due_date=None)))


@pytest.mark.parametrize("value", ["gbp", "GB", "GBPX", "£", ""])
def test_invalid_currency(value):
    assert any(e.startswith("currency:") for e in errors_of(with_(currency=value)))


@pytest.mark.parametrize("field", ["supplier_name", "invoice_number"])
def test_required_text_not_empty(field):
    assert any(e.startswith(f"{field}:") for e in errors_of(with_(**{field: "  "})))


def test_line_description_not_empty():
    assert any("description" in e for e in errors_of(with_(lines=[line(description="")])))


def test_lines_not_empty():
    assert any(e.startswith("lines:") for e in errors_of(with_(lines=[])))


@pytest.mark.parametrize("numbers", [[1, 2, 4], [2, 3], [1, 1], [0, 1]])
def test_line_numbers_run_1_to_n(numbers):
    lines = [line(line_no=n) for n in numbers]
    assert any("line_no must run" in e for e in errors_of(with_(lines=lines)))


def test_extra_field_rejected():
    assert any("extra" in e.lower() for e in errors_of(with_(sku="X")))


def test_missing_field_rejected():
    doc = copy.deepcopy(EXAMPLE)
    del doc["currency"]
    assert any(e.startswith("currency:") for e in errors_of(doc))


def test_wrong_type_rejected():
    assert any("line_no" in e for e in errors_of(with_(lines=[line(line_no="1")])))


def test_not_json():
    with pytest.raises(schema.InvalidOutput):
        schema.validate("not json")


def test_quantity_and_unit_price_nullable():
    schema.validate(json.dumps(with_(lines=[line(quantity=None, unit_price=None)], subtotal="268.21")))


def test_errors_are_readable():
    errs = errors_of(with_(currency="gbp", lines=[line(amount="$10")]))
    assert "currency: 'gbp' is not a 3-letter uppercase ISO 4217 code" in errs
    assert any(e.startswith("lines.0.amount: '$10' is not a plain decimal") for e in errs)


def test_plausibility_ok_on_example():
    assert schema.plausibility(schema.validate(json.dumps(EXAMPLE))) == []


def test_plausibility_detects_both():
    x = schema.validate(json.dumps(with_(subtotal="700.00", grand_total="1000.00")))
    assert schema.plausibility(x) == ["lines_sum_to_subtotal", "subtotal_plus_tax_is_total"]


def test_plausibility_skips_missing_totals():
    x = schema.validate(json.dumps(with_(subtotal=None, tax_total=None)))
    assert schema.plausibility(x) == []


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from _objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v)


def test_output_schema_follows_structured_output_rules():
    objects = list(_objects(schema.OUTPUT_SCHEMA))
    assert len(objects) == 2  # invoice and line
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    unsupported = {"minimum", "maximum", "multipleOf", "minLength", "maxLength", "pattern", "minItems"}
    assert not unsupported & set(json.dumps(schema.OUTPUT_SCHEMA).replace('"', " ").split())


def test_output_schema_matches_model_fields():
    assert set(schema.OUTPUT_SCHEMA["properties"]) == set(schema.Extraction.model_fields)
    line_schema = schema.OUTPUT_SCHEMA["properties"]["lines"]["items"]
    assert set(line_schema["properties"]) == set(schema.Line.model_fields)


def test_line_sum_mismatch():
    assert schema.line_sum_mismatch(schema.validate(json.dumps(EXAMPLE))) is None
    x = schema.validate(json.dumps(with_(subtotal="700.00")))
    assert schema.line_sum_mismatch(x).startswith("the line amounts sum to 774.05 but the printed subtotal is 700.00")
    assert schema.line_sum_mismatch(schema.validate(json.dumps(with_(subtotal=None)))) is None
