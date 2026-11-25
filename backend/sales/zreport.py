"""
Z-report generation for a closing session.

Consumes the structured pandas summary from :func:`sales.summary
.compute_session_summary` and renders a printable Z-report showing the
figures an operator needs to close the shift: revenue (net sales), cost of
goods, profit/loss and the supporting roll-ups (discounts, tax, payments
and category breakdown).

The content is built as plain lines first (``build_zreport_lines``) so the
figures are trivially testable and also usable as a text receipt; the PDF
renderer (``build_zreport_pdf``) draws the same lines with ``fpdf2``, which
is the PDF engine the project already depends on (reportlab is optional and
not installed; see ``sales.receipt``).

Edge cases from ``compute_session_summary`` carry through: an empty session
still yields a valid report with zeroed totals, and non-numeric amounts are
coerced to zero (counted in the summary ``warnings``) so one corrupt row
cannot break the report.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Sequence

# fpdf2 is a dependency of this project (used by sales.receipt as its PDF
# backend). Import lazily so importing this module does not raise where the
# optional heavy dependency is missing.
_FPDF_IMPORT_ERROR: Exception | None = None
try:
    from fpdf import FPDF
except Exception as exc:  # pragma: no cover - environment specific
    FPDF = None  # type: ignore
    _FPDF_IMPORT_ERROR = exc


def _money(value: Any, default: str = "0.00") -> str:
    """Return a 2-dp string for a summary monetary value (already a string)."""
    return str(value) if value is not None else default


def _percent(margin: Any) -> str:
    """Render a margin ratio (0..1) as a percentage string, or ``n/a``."""
    if margin is None:
        return "n/a"
    return f"{float(margin) * 100:.2f}%"


def build_zreport_lines(data: Dict[str, Any]) -> List[str]:
    """Build the Z-report content from a :func:`compute_session_summary` result.

    Returns a list of plain text lines. The first group is the session
    header (store + session identity), then the monetary summary (revenue,
    cost of goods, profit/loss), then payment and category breakdowns.
    """
    session = data["session"]
    summary = data["summary"]

    lines: List[str] = []
    lines.append("Z-REPORT")
    lines.append(f"Store: {data.get('store_name', 'eixnPOS')}")
    lines.append(f"Session #{session['id']}")
    lines.append(f"Cashier: {session['cashier']}")
    lines.append(f"Opened: {session['opened_at']}")
    lines.append(f"Closed: {session['closed_at'] or 'open'}")
    lines.append(f"Sales: {summary['sale_count']}   Items: {summary['item_count']}")
    lines.append("-" * 40)

    lines.append("SUMMARY")
    lines.append(f"Gross sales:          {_money(summary['gross_sales'])}")
    lines.append(f"Discounts:            {_money(summary['discounts'])}")
    lines.append(f"Tax:                  {_money(summary['tax_total'])}")
    lines.append(f"Revenue (net sales):  {_money(summary['revenue'])}")
    lines.append(f"Cost of goods:        {_money(summary['cost_of_goods'])}")
    lines.append(f"Profit/Loss:          {_money(summary['profit_loss'])}")
    lines.append(f"Profit margin:        {_percent(summary.get('profit_margin'))}")
    lines.append("-" * 40)

    lines.append("PAYMENTS")
    by_payment = summary.get("by_payment") or {}
    if by_payment:
        for method, amount in by_payment.items():
            lines.append(f"{str(method).title():<20} {_money(amount)}")
    else:
        lines.append("No payments recorded.")
    lines.append("-" * 40)

    lines.append("BY CATEGORY")
    by_category = summary.get("by_category") or {}
    if by_category:
        for category, amount in by_category.items():
            lines.append(f"{str(category):<20} {_money(amount)}")
    else:
        lines.append("No itemised sales.")
    lines.append("-" * 40)

    warnings = data.get("warnings") or {}
    if any(int(warnings.get(k, 0)) for k in ("invalid_sale_amounts", "invalid_line_amounts", "invalid_payment_amounts")):
        lines.append("WARNING: non-numeric amounts were coerced to zero; see system log.")
    return lines


def _pdf_available() -> None:
    if FPDF is None:
        raise RuntimeError(
            "PDF generation requires the 'fpdf2' package: pip install fpdf2"
        )


def build_zreport_pdf(data: Dict[str, Any], creation_date: datetime | None = None) -> bytes:
    """Render a Z-report as a letter-size PDF (bytes) from summary data.

    The PDF embeds the same lines as :func:`build_zreport_lines`, so it is
    guaranteed to show revenue (net sales) and profit/loss alongside the
    session identity.

    Args:
        data: a :func:`compute_session_summary` result (plus ``store_name``).
        creation_date: an optional aware datetime to pin as the document's
            ``/CreationDate`` (fpdf2 hashes it into ``/ID`` too). Pass the
            filing time (the shift's ``closed_at``) so the PDF archived at
            close and a later re-download of the same snapshot are byte-for-
            byte identical. When ``None`` (the default), fpdf2 stamps the
            wall-clock at render time, so two renders of the same data may
            differ; omit it only for live previews where no identity across
            downloads is claimed.
    """
    _pdf_available()
    assert FPDF is not None

    lines = build_zreport_lines(data)

    pdf = FPDF(format="letter")
    if creation_date is not None:
        pdf.set_creation_date(creation_date)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Z-REPORT", align="C")
    pdf.ln(12)
    pdf.set_font("Helvetica", size=10)
    for line in lines:
        if line == "Z-REPORT":
            continue  # already rendered as the title
        pdf.cell(0, 6, line.encode("latin-1", "replace").decode("latin-1"))
        pdf.ln(6)

    out = pdf.output()
    if isinstance(out, bytearray):
        out = bytes(out)
    return out


def build_session_zreport_pdf(
    session_id: int, store_name: str = "eixnPOS", creation_date: datetime | None = None
) -> bytes:
    """Convenience: compute a session summary and render it as a PDF.

    Args:
        session_id: id of the Session to report on.
        store_name: store display name (defaults to ``eixnPOS``).
        creation_date: forwarded to :func:`build_zreport_pdf` (pins the
            document ``/CreationDate``/``/ID``); default ``None`` renders a
            live, non-immutable preview.

    Returns:
        PDF bytes for the session's Z-report.

    Raises:
        Session.DoesNotExist if no Session matches ``session_id``.
    """
    from .summary import compute_session_summary

    data = compute_session_summary(session_id)
    data["store_name"] = store_name
    return build_zreport_pdf(data, creation_date=creation_date)
