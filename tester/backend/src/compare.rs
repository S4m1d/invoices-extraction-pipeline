//! Comparison of an extracted invoice with its golden record (tester/backend/specs/comparison.md).

use rust_decimal::{Decimal, RoundingStrategy};
use serde::{Deserialize, Serialize};
use std::str::FromStr;
use unicode_normalization::UnicodeNormalization;

/// A value as stored by the integrator or the golden set: text, or absent.
pub type Value = Option<String>;

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Invoice {
    pub supplier_name: Value,
    pub recipient_name: Value,
    pub invoice_number: Value,
    pub issue_date: Value,
    pub due_date: Value,
    pub currency: Value,
    pub subtotal: Value,
    pub tax_total: Value,
    pub grand_total: Value,
    /// Not compared with the golden set; may be a required field of a documents source.
    #[serde(default)]
    pub tax_rate: Value,
    pub lines: Vec<Line>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Line {
    pub line_no: i64,
    pub description: Value,
    pub quantity: Value,
    pub unit_price: Value,
    pub amount: Value,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Match,
    Mismatch,
    Missing,
    Unexpected,
}

impl Outcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Outcome::Match => "match",
            Outcome::Mismatch => "mismatch",
            Outcome::Missing => "missing",
            Outcome::Unexpected => "unexpected",
        }
    }
}

#[derive(Debug, Clone, Copy)]
enum Kind {
    Text,
    Exact,
    Decimal,
    UnitPrice,
}

#[derive(Debug, Clone, Serialize)]
pub struct FieldResult {
    /// Line position (1-based) for line fields, None for header fields.
    pub position: Option<i64>,
    pub field: &'static str,
    pub outcome: Outcome,
    pub expected: Value,
    pub actual: Value,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LineOutcome {
    Compared,
    Missing,
    Extra,
}

impl LineOutcome {
    pub fn as_str(self) -> &'static str {
        match self {
            LineOutcome::Compared => "compared",
            LineOutcome::Missing => "missing",
            LineOutcome::Extra => "extra",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct LineResult {
    pub position: i64,
    pub golden_line_no: Option<i64>,
    pub extracted_line_no: Option<i64>,
    pub outcome: LineOutcome,
}

#[derive(Debug, Clone, Serialize)]
pub struct Comparison {
    pub fields: Vec<FieldResult>,
    pub lines: Vec<LineResult>,
    pub passed: bool,
}

/// Compares extracted with golden. Golden lines must be the top-level ones in printed order (golden.rs);
/// extracted lines are ordered by line_no here. Without a printed unit price, a null extracted
/// unit_price is not compared (specs/comparison.md, Lines).
pub fn compare(extracted: &Invoice, golden: &Invoice, unit_price_printed: bool) -> Comparison {
    let header: [(&'static str, Kind, &Value, &Value); 9] = [
        ("supplier_name", Kind::Text, &extracted.supplier_name, &golden.supplier_name),
        ("recipient_name", Kind::Text, &extracted.recipient_name, &golden.recipient_name),
        ("invoice_number", Kind::Text, &extracted.invoice_number, &golden.invoice_number),
        ("issue_date", Kind::Exact, &extracted.issue_date, &golden.issue_date),
        ("due_date", Kind::Exact, &extracted.due_date, &golden.due_date),
        ("currency", Kind::Exact, &extracted.currency, &golden.currency),
        ("subtotal", Kind::Decimal, &extracted.subtotal, &golden.subtotal),
        ("tax_total", Kind::Decimal, &extracted.tax_total, &golden.tax_total),
        ("grand_total", Kind::Decimal, &extracted.grand_total, &golden.grand_total),
    ];
    let mut fields: Vec<FieldResult> = header
        .iter()
        .map(|(name, kind, actual, expected)| field(None, name, *kind, actual, expected))
        .collect();

    let mut ext = extracted.lines.clone();
    let gold = &golden.lines;
    ext.sort_by_key(|l| l.line_no);
    let mut lines = Vec::new();
    for i in 0..ext.len().max(gold.len()) {
        let position = i as i64 + 1;
        match (ext.get(i), gold.get(i)) {
            (Some(x), Some(g)) => {
                lines.push(LineResult {
                    position,
                    golden_line_no: Some(g.line_no),
                    extracted_line_no: Some(x.line_no),
                    outcome: LineOutcome::Compared,
                });
                let p = Some(position);
                fields.push(field(p, "description", Kind::Text, &x.description, &g.description));
                fields.push(field(p, "quantity", Kind::Decimal, &x.quantity, &g.quantity));
                if unit_price_printed || x.unit_price.is_some() {
                    fields.push(field(p, "unit_price", Kind::UnitPrice, &x.unit_price, &g.unit_price));
                }
                fields.push(field(p, "amount", Kind::Decimal, &x.amount, &g.amount));
            }
            (None, Some(g)) => lines.push(LineResult {
                position,
                golden_line_no: Some(g.line_no),
                extracted_line_no: None,
                outcome: LineOutcome::Missing,
            }),
            (Some(x), None) => lines.push(LineResult {
                position,
                golden_line_no: None,
                extracted_line_no: Some(x.line_no),
                outcome: LineOutcome::Extra,
            }),
            (None, None) => unreachable!(),
        }
    }
    let passed = fields.iter().all(|f| f.outcome == Outcome::Match)
        && lines.iter().all(|l| l.outcome == LineOutcome::Compared);
    Comparison { fields, lines, passed }
}

fn field(position: Option<i64>, name: &'static str, kind: Kind, actual: &Value, expected: &Value) -> FieldResult {
    let outcome = match (actual, expected) {
        (None, None) => Outcome::Match,
        (None, Some(_)) => Outcome::Missing,
        (Some(_), None) => Outcome::Unexpected,
        (Some(a), Some(e)) => {
            if equal(kind, a, e) {
                Outcome::Match
            } else {
                Outcome::Mismatch
            }
        }
    };
    FieldResult { position, field: name, outcome, expected: expected.clone(), actual: actual.clone() }
}

fn equal(kind: Kind, actual: &str, expected: &str) -> bool {
    match kind {
        Kind::Text => normalize_text(actual) == normalize_text(expected),
        Kind::Exact => actual.trim() == expected.trim(),
        Kind::Decimal => match (dec(actual), dec(expected)) {
            (Some(a), Some(e)) => a == e,
            _ => actual.trim() == expected.trim(),
        },
        Kind::UnitPrice => match (dec(actual), dec(expected)) {
            // Golden has 4 decimals where invoices print 2: round golden to the printed precision.
            (Some(a), Some(e)) => {
                a == e || e.round_dp_with_strategy(a.scale(), RoundingStrategy::MidpointAwayFromZero) == a
            }
            _ => actual.trim() == expected.trim(),
        },
    }
}

/// Trim, collapse whitespace runs to one space, Unicode NFC. Case-sensitive.
pub fn normalize_text(s: &str) -> String {
    s.nfc().collect::<String>().split_whitespace().collect::<Vec<_>>().join(" ")
}

fn dec(s: &str) -> Option<Decimal> {
    Decimal::from_str(s.trim()).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(s: &str) -> Value {
        Some(s.to_string())
    }

    fn line(no: i64, desc: &str, qty: Option<&str>, price: Option<&str>, amount: &str) -> Line {
        Line { line_no: no, description: v(desc), quantity: qty.map(String::from), unit_price: price.map(String::from), amount: v(amount) }
    }

    /// inv_00000029 (extraction-schema.md example = golden).
    fn example() -> Invoice {
        Invoice {
            supplier_name: v("Willowmere Halcyon Ltd"),
            recipient_name: v("Vireo Labs"),
            invoice_number: v("INV-2025-000029"),
            issue_date: v("2025-07-25"),
            due_date: v("2025-08-24"),
            currency: v("GBP"),
            subtotal: v("774.05"),
            tax_total: v("154.81"),
            grand_total: v("928.86"),
            tax_rate: None,
            lines: vec![
                line(1, "Premium support add-on", Some("1"), Some("268.21"), "268.21"),
                line(2, "Additional user seats", Some("6"), Some("30.27"), "181.62"),
                line(3, "Growth plan — monthly subscription", Some("1"), Some("324.22"), "324.22"),
            ],
        }
    }

    fn outcome(c: &Comparison, position: Option<i64>, name: &str) -> Outcome {
        c.fields.iter().find(|f| f.position == position && f.field == name).unwrap().outcome
    }

    #[test]
    fn identical_passes() {
        let c = compare(&example(), &example(), true);
        assert!(c.passed);
        assert_eq!(c.fields.len(), 9 + 3 * 4);
    }

    #[test]
    fn text_trims_and_collapses_but_keeps_case() {
        let mut x = example();
        x.recipient_name = v("  Vireo   Labs ");
        assert!(compare(&x, &example(), true).passed);
        x.recipient_name = v("vireo labs");
        let c = compare(&x, &example(), true);
        assert_eq!(outcome(&c, None, "recipient_name"), Outcome::Mismatch);
        assert!(!c.passed);
    }

    #[test]
    fn text_is_nfc_normalized() {
        let mut x = example();
        let mut g = example();
        g.supplier_name = v("Caf\u{e9}"); // precomposed é
        x.supplier_name = v("Cafe\u{301}"); // e + combining acute
        assert_eq!(outcome(&compare(&x, &g, true), None, "supplier_name"), Outcome::Match);
    }

    #[test]
    fn decimals_compare_numerically() {
        let mut x = example();
        x.grand_total = v("928.860");
        x.lines[0].quantity = v("1.0");
        assert!(compare(&x, &example(), true).passed);
        x.grand_total = v("928.87");
        assert_eq!(outcome(&compare(&x, &example(), true), None, "grand_total"), Outcome::Mismatch);
    }

    #[test]
    fn unit_price_rounds_golden_to_printed_decimals() {
        let mut g = example();
        let mut x = example();
        g.lines[0].unit_price = v("0.1290");
        x.lines[0].unit_price = v("0.13");
        assert_eq!(outcome(&compare(&x, &g, true), Some(1), "unit_price"), Outcome::Match);
        g.lines[0].unit_price = v("0.1250"); // half up -> 0.13
        assert_eq!(outcome(&compare(&x, &g, true), Some(1), "unit_price"), Outcome::Match);
        g.lines[0].unit_price = v("0.1249");
        assert_eq!(outcome(&compare(&x, &g, true), Some(1), "unit_price"), Outcome::Mismatch);
        // only unit_price is rounded; amounts are exact
        g = example();
        g.lines[0].amount = v("268.2100");
        x = example();
        x.lines[0].amount = v("268.21");
        assert_eq!(outcome(&compare(&x, &g, true), Some(1), "amount"), Outcome::Match);
        g.lines[0].amount = v("268.2149");
        assert_eq!(outcome(&compare(&x, &g, true), Some(1), "amount"), Outcome::Mismatch);
    }

    #[test]
    fn missing_fails_and_unexpected_fails() {
        let mut x = example();
        x.lines[1].unit_price = None;
        let c = compare(&x, &example(), true);
        assert_eq!(outcome(&c, Some(2), "unit_price"), Outcome::Missing);
        assert!(!c.passed);

        let mut g = example();
        g.due_date = None;
        let c = compare(&example(), &g, true);
        assert_eq!(outcome(&c, None, "due_date"), Outcome::Unexpected);
        assert!(!c.passed);

        let mut both = example();
        both.due_date = None;
        assert!(compare(&both, &both, true).passed); // both null = match
    }

    #[test]
    fn unit_price_not_printed_is_not_compared_when_null() {
        let mut x = example();
        x.lines[1].unit_price = None;
        let c = compare(&x, &example(), false);
        assert!(!c.fields.iter().any(|f| f.position == Some(2) && f.field == "unit_price"));
        assert!(c.passed);

        x.lines[1].unit_price = v("99.00"); // extracted anyway: compared as usual
        let c = compare(&x, &example(), false);
        assert_eq!(outcome(&c, Some(2), "unit_price"), Outcome::Mismatch);
        assert!(!c.passed);
    }

    #[test]
    fn golden_lines_keep_their_given_order() {
        let mut g = example();
        g.lines.swap(0, 2); // printed order differs from golden line_no order
        let mut x = example();
        x.lines.swap(0, 2);
        for (i, l) in x.lines.iter_mut().enumerate() {
            l.line_no = i as i64 + 1;
        }
        let c = compare(&x, &g, true);
        assert!(c.passed);
        assert_eq!(c.lines[0].golden_line_no, Some(3));
    }

    #[test]
    fn lines_compared_by_position() {
        let mut x = example();
        x.lines.swap(0, 1);
        x.lines[0].line_no = 1;
        x.lines[1].line_no = 2;
        let c = compare(&x, &example(), true);
        assert_eq!(outcome(&c, Some(1), "description"), Outcome::Mismatch);
        assert!(!c.passed);
    }

    #[test]
    fn line_order_follows_line_no_not_storage_order() {
        let mut x = example();
        x.lines.reverse(); // same line_no values, stored in another order
        assert!(compare(&x, &example(), true).passed);
    }

    #[test]
    fn missing_and_extra_lines() {
        let mut x = example();
        x.lines.pop();
        let c = compare(&x, &example(), true);
        assert_eq!(c.lines[2].outcome, LineOutcome::Missing);
        assert_eq!(c.lines[2].golden_line_no, Some(3));
        assert!(!c.passed);

        let mut x = example();
        x.lines.push(line(4, "Shipping & Handling", None, None, "34.94"));
        let c = compare(&x, &example(), true);
        assert_eq!(c.lines[3].outcome, LineOutcome::Extra);
        assert_eq!(c.lines[3].extracted_line_no, Some(4));
        assert!(!c.passed);
    }
}
