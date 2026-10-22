//! Validation of an extracted invoice without a golden set (tester/backend/specs/validation.md).

use crate::{
    compare::{Invoice, Line, Value},
    sources::RequiredFields,
};
use rust_decimal::{Decimal, RoundingStrategy};
use serde::Serialize;
use std::str::FromStr;

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct FieldResult {
    /// Line position (1-based) for line fields, None for header fields.
    pub position: Option<i64>,
    pub field: String,
    /// `present` or `missing`.
    pub outcome: &'static str,
    pub actual: Value,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct CheckResult {
    pub check: &'static str,
    /// Line position for `line_amount`, None otherwise.
    pub position: Option<i64>,
    /// `pass`, `fail` or `not_applicable`.
    pub outcome: &'static str,
    /// The computed side.
    pub expected: Value,
    /// The extracted side.
    pub actual: Value,
}

#[derive(Debug, Clone, Serialize)]
pub struct Validation {
    pub fields: Vec<FieldResult>,
    pub checks: Vec<CheckResult>,
    pub passed: bool,
}

pub fn validate(invoice: &Invoice, required: &RequiredFields) -> Validation {
    let mut lines = invoice.lines.clone();
    lines.sort_by_key(|l| l.line_no);

    let mut fields = Vec::new();
    for name in &required.invoice {
        fields.push(field(None, name, header_value(invoice, name)));
    }
    for (i, line) in lines.iter().enumerate() {
        for name in &required.line {
            fields.push(field(Some(i as i64 + 1), name, line_value(line, name)));
        }
    }

    let mut checks = Vec::new();
    let amounts: Option<Vec<Decimal>> = lines.iter().map(|l| dec(&l.amount)).collect();
    checks.push(match (amounts.filter(|a| !a.is_empty()), dec(&invoice.subtotal)) {
        (Some(a), Some(subtotal)) => check("lines_sum", None, a.into_iter().sum(), subtotal),
        _ => not_applicable("lines_sum", None),
    });
    checks.push(match (dec(&invoice.subtotal), dec(&invoice.tax_total), dec(&invoice.grand_total)) {
        (Some(s), Some(t), Some(g)) => check("totals", None, s + t, g),
        _ => not_applicable("totals", None),
    });
    for (i, line) in lines.iter().enumerate() {
        let p = Some(i as i64 + 1);
        checks.push(match (dec(&line.quantity), dec(&line.unit_price), dec(&line.amount)) {
            (Some(q), Some(u), Some(a)) => check("line_amount", p, q * u, a),
            _ => not_applicable("line_amount", p),
        });
    }

    let passed = fields.iter().all(|f| f.outcome == "present") && checks.iter().all(|c| c.outcome != "fail");
    Validation { fields, checks, passed }
}

fn field(position: Option<i64>, name: &str, actual: Value) -> FieldResult {
    let present = actual.as_deref().is_some_and(|v| !v.trim().is_empty());
    FieldResult { position, field: name.to_string(), outcome: if present { "present" } else { "missing" }, actual }
}

/// computed is rounded half up to the decimal places of actual, then compared (validation.md, Arithmetic checks).
fn check(name: &'static str, position: Option<i64>, computed: Decimal, actual: Decimal) -> CheckResult {
    let expected = computed.round_dp_with_strategy(actual.scale(), RoundingStrategy::MidpointAwayFromZero);
    CheckResult {
        check: name,
        position,
        outcome: if expected == actual { "pass" } else { "fail" },
        expected: Some(expected.to_string()),
        actual: Some(actual.to_string()),
    }
}

fn not_applicable(name: &'static str, position: Option<i64>) -> CheckResult {
    CheckResult { check: name, position, outcome: "not_applicable", expected: None, actual: None }
}

fn dec(v: &Value) -> Option<Decimal> {
    v.as_deref().and_then(|s| Decimal::from_str(s.trim()).ok())
}

fn header_value(i: &Invoice, name: &str) -> Value {
    match name {
        "supplier_name" => i.supplier_name.clone(),
        "recipient_name" => i.recipient_name.clone(),
        "invoice_number" => i.invoice_number.clone(),
        "issue_date" => i.issue_date.clone(),
        "due_date" => i.due_date.clone(),
        "currency" => i.currency.clone(),
        "subtotal" => i.subtotal.clone(),
        "tax_total" => i.tax_total.clone(),
        "grand_total" => i.grand_total.clone(),
        "tax_rate" => i.tax_rate.clone(),
        _ => None, // names are validated when the sources file is read
    }
}

fn line_value(l: &Line, name: &str) -> Value {
    match name {
        "line_no" => Some(l.line_no.to_string()),
        "description" => l.description.clone(),
        "quantity" => l.quantity.clone(),
        "unit_price" => l.unit_price.clone(),
        "amount" => l.amount.clone(),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(s: &str) -> Value {
        Some(s.to_string())
    }

    fn line(no: i64, qty: Option<&str>, price: Option<&str>, amount: &str) -> Line {
        Line { line_no: no, description: v("Item"), quantity: qty.map(String::from), unit_price: price.map(String::from), amount: v(amount) }
    }

    fn invoice() -> Invoice {
        Invoice {
            supplier_name: v("Supplier"),
            invoice_number: v("FR 1"),
            issue_date: v("2026-10-21"),
            currency: v("EUR"),
            subtotal: v("280.49"),
            tax_total: v("0.00"),
            grand_total: v("280.49"),
            lines: vec![line(2, Some("2"), Some("13.00"), "26.00"), line(1, Some("1"), Some("254.49"), "254.49")],
            ..Default::default()
        }
    }

    fn outcomes(v: &Validation, name: &str) -> Vec<&'static str> {
        v.checks.iter().filter(|c| c.check == name).map(|c| c.outcome).collect()
    }

    #[test]
    fn consistent_invoice_passes() {
        let r = validate(&invoice(), &RequiredFields::default());
        assert!(r.passed, "{r:?}");
        assert_eq!(outcomes(&r, "lines_sum"), ["pass"]);
        assert_eq!(outcomes(&r, "totals"), ["pass"]);
        assert_eq!(outcomes(&r, "line_amount"), ["pass", "pass"]);
        // 7 header fields + 3 per line
        assert_eq!(r.fields.len(), 13);
        assert!(r.fields.iter().all(|f| f.outcome == "present"));
    }

    #[test]
    fn missing_required_field_fails() {
        let mut i = invoice();
        i.tax_total = None;
        i.lines[0].quantity = None;
        let r = validate(&i, &RequiredFields::default());
        assert!(!r.passed);
        let missing: Vec<(Option<i64>, &str)> =
            r.fields.iter().filter(|f| f.outcome == "missing").map(|f| (f.position, f.field.as_str())).collect();
        // lines are sorted by line_no: the line with line_no 2 is position 2
        assert_eq!(missing, [(None, "tax_total"), (Some(2), "quantity")]);
        // checks with a null input are not applicable, not failed
        assert_eq!(outcomes(&r, "totals"), ["not_applicable"]);
        assert_eq!(outcomes(&r, "line_amount"), ["pass", "not_applicable"]);
    }

    #[test]
    fn empty_text_is_missing_and_unlisted_fields_are_not_checked() {
        let mut i = invoice();
        i.supplier_name = v("  ");
        i.due_date = None; // not in the default list
        let r = validate(&i, &RequiredFields::default());
        assert_eq!(r.fields.iter().find(|f| f.field == "supplier_name").unwrap().outcome, "missing");
        assert!(r.fields.iter().all(|f| f.field != "due_date"));
    }

    #[test]
    fn diverging_amounts_fail_with_both_values() {
        let mut i = invoice();
        i.subtotal = v("250.00");
        i.grand_total = v("300.00");
        let r = validate(&i, &RequiredFields::default());
        assert!(!r.passed);
        let sum = r.checks.iter().find(|c| c.check == "lines_sum").unwrap();
        assert_eq!((sum.outcome, sum.expected.as_deref(), sum.actual.as_deref()), ("fail", Some("280.49"), Some("250.00")));
        let totals = r.checks.iter().find(|c| c.check == "totals").unwrap();
        assert_eq!((totals.outcome, totals.expected.as_deref(), totals.actual.as_deref()), ("fail", Some("250.00"), Some("300.00")));
    }

    #[test]
    fn line_amount_rounds_half_up_to_printed_decimals() {
        let mut i = invoice();
        // 3 x 0.335 = 1.005 -> 1.01
        i.lines = vec![line(1, Some("3"), Some("0.335"), "1.01")];
        i.subtotal = v("1.01");
        i.grand_total = v("1.01");
        assert!(validate(&i, &RequiredFields::default()).passed);
        i.lines[0].amount = v("1.00");
        let r = validate(&i, &RequiredFields::default());
        let c = r.checks.iter().find(|c| c.check == "line_amount").unwrap();
        assert_eq!((c.outcome, c.position, c.expected.as_deref()), ("fail", Some(1), Some("1.01")));
    }

    #[test]
    fn custom_required_fields() {
        let req = RequiredFields { invoice: vec!["due_date".into(), "tax_rate".into()], line: vec!["line_no".into()] };
        let mut i = invoice();
        i.tax_rate = v("0");
        let r = validate(&i, &req);
        let got: Vec<(&str, &str)> = r.fields.iter().map(|f| (f.field.as_str(), f.outcome)).collect();
        assert_eq!(got, [("due_date", "missing"), ("tax_rate", "present"), ("line_no", "present"), ("line_no", "present")]);
    }
}
