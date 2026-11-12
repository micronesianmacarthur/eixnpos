"""
Receipt generation module for eixnPOS.

Provides text and PDF receipt generation for completed sales transactions.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from .models import Sale


@dataclass
class ReceiptConfig:
    """Configuration for receipt generation."""

    store_name: str = "eixnPOS Store"
    store_phone: str = ""
    store_email: str = ""
    store_address: str = ""
    store_website: str = ""
    return_policy: str = "Returns accepted within 30 days with receipt."
    tax_rate: Decimal = Decimal("0.00")  # Default no tax
    currency_symbol: str = "$"
    receipt_width: int = 40  # Characters for text receipt


class ReceiptGenerator:
    """
    Generates receipts for completed sales transactions.

    Supports both text and PDF output formats.
    """

    def __init__(self, config: Optional[ReceiptConfig] = None):
        self.config = config or ReceiptConfig()

    def generate_receipt(self, transaction: "Sale", format: str = "text") -> str:
        """
        Generate a receipt for a completed sale.

        Args:
            transaction: The Sale object to generate a receipt for.
            format: Output format - "text" or "pdf".

        Returns:
            Receipt content as string (text) or base64-encoded PDF.
        """
        if format == "pdf":
            return self._generate_pdf(transaction)
        return self._generate_text(transaction)

    def _generate_text(self, sale: "Sale") -> str:
        """Generate a plain text receipt."""
        lines = []
        width = self.config.receipt_width
        currency = self.config.currency_symbol

        def add_line(text: str = "", align: str = "left"):
            if align == "center":
                lines.append(text.center(width))
            elif align == "right":
                lines.append(text.rjust(width))
            else:
                lines.append(text.ljust(width))

        def add_kv(key: str, value: str):
            key_part = key.ljust(width - len(value) - 1)
            lines.append(f"{key_part} {value}")

        # Header
        add_line("=" * width)
        add_line(self.config.store_name, "center")
        if self.config.store_address:
            for addr_line in self.config.store_address.split("\n"):
                add_line(addr_line.strip(), "center")
        if self.config.store_phone:
            add_line(f"Tel: {self.config.store_phone}", "center")
        if self.config.store_email:
            add_line(self.config.store_email, "center")
        add_line("=" * width)

        # Sale info
        add_line(f"Sale #{sale.number}")
        add_line(f"Date: {sale.created_at.strftime('%Y-%m-%d %H:%M')}")
        if sale.completed_at:
            add_line(f"Completed: {sale.completed_at.strftime('%Y-%m-%d %H:%M')}")
        add_line(f"Cashier: {sale.cashier.get_full_name() or sale.cashier.username}")
        if sale.customer:
            add_line(f"Customer: {sale.customer.name}")
        add_line("-" * width)

        # Line items header
        add_line("ITEM".ljust(20) + "QTY".rjust(4) + "PRICE".rjust(8) + "TOTAL".rjust(8))
        add_line("-" * width)

        # Line items
        for line in sale.lines.select_related("product").all():
            name = line.product.name
            if len(name) > 18:
                name = name[:17] + "…"
            qty_str = str(line.quantity)
            price_str = f"{currency}{line.unit_price:.2f}"
            total_str = f"{currency}{line.line_total:.2f}"

            add_line(f"{name.ljust(20)}{qty_str.rjust(4)}{price_str.rjust(8)}{total_str.rjust(8)}")

            # Discount line if applicable
            if line.discount_amount > 0:
                discount_type = "Pct" if line.discount_type == "percent" else "Fix"
                disc_line = f"  Discount ({discount_type} {line.discount_value})"
                disc_amt = f"-{currency}{line.discount_amount:.2f}"
                add_line(f"{disc_line.ljust(24)}{disc_amt.rjust(16)}")

        add_line("-" * width)

        # Totals
        add_kv("Subtotal:", f"{currency}{sale.subtotal:.2f}")
        if sale.discount_total > 0:
            add_kv("Discounts:", f"-{currency}{sale.discount_total:.2f}")

        # Tax calculation (simple percentage-based)
        taxable_amount = sale.total
        tax_amount = (taxable_amount * self.config.tax_rate).quantize(Decimal("0.01"))
        if tax_amount > 0:
            add_kv(f"Tax ({self.config.tax_rate * 100:.0f}%):", f"{currency}{tax_amount:.2f}")

        add_line("=" * width)
        add_kv("TOTAL:", f"{currency}{sale.total:.2f}")
        add_line("=" * width)

        # Payments
        add_line("PAYMENTS:")
        add_line("-" * width)
        for payment in sale.payments.all():
            method_label = payment.get_method_display()
            amount_str = f"{currency}{payment.amount:.2f}"
            add_line(f"{method_label.ljust(width - len(amount_str) - 1)} {amount_str}")

            if payment.method == "cash" and payment.tendered is not None:
                tendered_str = f"{currency}{payment.tendered:.2f}"
                change_str = f"{currency}{payment.change_due:.2f}"
                add_kv("  Tendered:", tendered_str)
                add_kv("  Change:", change_str)

        add_line("=" * width)

        # Return policy
        if self.config.return_policy:
            add_line("")
            add_line(self.config.return_policy, "center")
            add_line("")

        # Footer
        add_line("Thank you for your business!", "center")
        if self.config.store_website:
            add_line(self.config.store_website, "center")
        add_line("=" * width)

        return "\n".join(lines)

    def _generate_pdf(self, sale: "Sale") -> str:
        """
        Generate a PDF receipt (base64 encoded).

        Requires reportlab or fpdf2 to be installed.
        """
        try:
            import base64
            from io import BytesIO

            # Try reportlab first
            try:
                from reportlab.lib.pagesizes import letter
                from reportlab.lib.units import inch
                from reportlab.pdfgen import canvas

                buffer = BytesIO()
                c = canvas.Canvas(buffer, pagesize=letter)
                width, height = letter
                margin = 0.75 * inch
                y = height - margin
                currency = self.config.currency_symbol

                def draw_text(text, x, y, font="Helvetica", size=10, align="left"):
                    c.setFont(font, size)
                    if align == "center":
                        c.drawCentredString(x, y, text)
                    elif align == "right":
                        c.drawRightString(x, y, text)
                    else:
                        c.drawString(x, y, text)

                # Header
                draw_text(self.config.store_name, width / 2, y, "Helvetica-Bold", 16, "center")
                y -= 20
                if self.config.store_address:
                    for addr_line in self.config.store_address.split("\n"):
                        draw_text(addr_line.strip(), width / 2, y, size=9, align="center")
                        y -= 12
                if self.config.store_phone:
                    draw_text(f"Tel: {self.config.store_phone}", width / 2, y, size=9, align="center")
                    y -= 12
                if self.config.store_email:
                    draw_text(self.config.store_email, width / 2, y, size=9, align="center")
                    y -= 12

                y -= 10
                c.line(margin, y, width - margin, y)
                y -= 20

                # Sale info
                draw_text(f"Sale #{sale.number}", margin, y, "Helvetica-Bold", 11)
                y -= 16
                draw_text(f"Date: {sale.created_at.strftime('%Y-%m-%d %H:%M')}", margin, y, size=9)
                y -= 14
                if sale.completed_at:
                    draw_text(f"Completed: {sale.completed_at.strftime('%Y-%m-%d %H:%M')}", margin, y, size=9)
                    y -= 14
                draw_text(f"Cashier: {sale.cashier.get_full_name() or sale.cashier.username}", margin, y, size=9)
                y -= 14
                if sale.customer:
                    draw_text(f"Customer: {sale.customer.name}", margin, y, size=9)
                    y -= 14

                y -= 6
                c.line(margin, y, width - margin, y)
                y -= 18

                # Line items header
                col_item = margin
                col_qty = width - margin - 3.5 * inch
                col_price = width - margin - 2.5 * inch
                col_total = width - margin - 1.2 * inch

                draw_text("ITEM", col_item, y, "Helvetica-Bold", 9)
                draw_text("QTY", col_qty, y, "Helvetica-Bold", 9, "right")
                draw_text("PRICE", col_price, y, "Helvetica-Bold", 9, "right")
                draw_text("TOTAL", col_total, y, "Helvetica-Bold", 9, "right")
                y -= 14
                c.line(margin, y, width - margin, y)
                y -= 16

                # Line items
                for line in sale.lines.select_related("product").all():
                    if y < margin + 50:
                        c.showPage()
                        y = height - margin

                    name = line.product.name
                    draw_text(name[:30], col_item, y, size=9)
                    draw_text(str(line.quantity), col_qty, y, size=9, align="right")
                    draw_text(f"{currency}{line.unit_price:.2f}", col_price, y, size=9, align="right")
                    draw_text(f"{currency}{line.line_total:.2f}", col_total, y, size=9, align="right")
                    y -= 14

                    if line.discount_amount > 0:
                        discount_type = "Pct" if line.discount_type == "percent" else "Fix"
                        disc_text = f"  Discount ({discount_type} {line.discount_value})"
                        draw_text(disc_text, col_item + 10, y, "Helvetica-Oblique", 8)
                        draw_text(f"-{currency}{line.discount_amount:.2f}", col_total, y, "Helvetica-Oblique", 8, "right")
                        y -= 12

                y -= 6
                c.line(margin, y, width - margin, y)
                y -= 18

                # Totals
                right_col = width - margin - 1.2 * inch
                draw_text("Subtotal:", width - margin - 2.5 * inch, y, size=10, align="right")
                draw_text(f"{currency}{sale.subtotal:.2f}", right_col, y, size=10, align="right")
                y -= 16

                if sale.discount_total > 0:
                    draw_text("Discounts:", width - margin - 2.5 * inch, y, size=10, align="right")
                    draw_text(f"-{currency}{sale.discount_total:.2f}", right_col, y, size=10, align="right")
                    y -= 16

                taxable_amount = sale.total
                tax_amount = (taxable_amount * self.config.tax_rate).quantize(Decimal("0.01"))
                if tax_amount > 0:
                    draw_text(f"Tax ({self.config.tax_rate * 100:.0f}%):", width - margin - 2.5 * inch, y, size=10, align="right")
                    draw_text(f"{currency}{tax_amount:.2f}", right_col, y, size=10, align="right")
                    y -= 16

                c.line(width - margin - 2.5 * inch, y, width - margin, y)
                y -= 16

                draw_text("TOTAL:", width - margin - 2.5 * inch, y, "Helvetica-Bold", 12, align="right")
                draw_text(f"{currency}{sale.total:.2f}", right_col, y, "Helvetica-Bold", 12, align="right")
                y -= 24

                # Payments
                draw_text("PAYMENTS:", margin, y, "Helvetica-Bold", 10)
                y -= 16
                for payment in sale.payments.all():
                    method_label = payment.get_method_display()
                    amount_str = f"{currency}{payment.amount:.2f}"
                    draw_text(method_label, margin, y, size=10)
                    draw_text(amount_str, right_col, y, size=10, align="right")
                    y -= 14

                    if payment.method == "cash" and payment.tendered is not None:
                        draw_text("  Tendered:", margin, y, size=9)
                        draw_text(f"{currency}{payment.tendered:.2f}", right_col, y, size=9, align="right")
                        y -= 12
                        draw_text("  Change:", margin, y, size=9)
                        draw_text(f"{currency}{payment.change_due:.2f}", right_col, y, size=9, align="right")
                        y -= 12

                y -= 10
                c.line(margin, y, width - margin, y)
                y -= 20

                # Return policy
                if self.config.return_policy:
                    draw_text(self.config.return_policy, width / 2, y, "Helvetica-Oblique", 8, "center")
                    y -= 20

                draw_text("Thank you for your business!", width / 2, y, "Helvetica-Bold", 10, "center")
                if self.config.store_website:
                    y -= 16
                    draw_text(self.config.store_website, width / 2, y, "Helvetica-Oblique", 8, "center")

                c.save()
                buffer.seek(0)
                return base64.b64encode(buffer.read()).decode("ascii")

            except ImportError:
                # Fallback to fpdf2
                from fpdf import FPDF

                class ReceiptPDF(FPDF):
                    def __init__(self):
                        super().__init__(format="letter")
                        self.set_auto_page_break(auto=True, margin=20)

                pdf = ReceiptPDF()
                pdf.add_page()
                pdf.set_font("Helvetica", size=10)
                currency = self.config.currency_symbol

                # Header
                pdf.set_font("Helvetica", "B", 16)
                pdf.cell(0, 10, self.config.store_name, align="C", new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", size=9)
                if self.config.store_address:
                    for addr_line in self.config.store_address.split("\n"):
                        pdf.cell(0, 5, addr_line.strip(), align="C", new_x="LMARGIN", new_y="NEXT")
                if self.config.store_phone:
                    pdf.cell(0, 5, f"Tel: {self.config.store_phone}", align="C", new_x="LMARGIN", new_y="NEXT")
                if self.config.store_email:
                    pdf.cell(0, 5, self.config.store_email, align="C", new_x="LMARGIN", new_y="NEXT")

                pdf.ln(5)
                pdf.line(15, pdf.get_y(), 201, pdf.get_y())
                pdf.ln(5)

                # Sale info
                pdf.set_font("Helvetica", "B", 11)
                pdf.cell(0, 6, f"Sale #{sale.number}", new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", size=9)
                pdf.cell(0, 5, f"Date: {sale.created_at.strftime('%Y-%m-%d %H:%M')}", new_x="LMARGIN", new_y="NEXT")
                if sale.completed_at:
                    pdf.cell(0, 5, f"Completed: {sale.completed_at.strftime('%Y-%m-%d %H:%M')}", new_x="LMARGIN", new_y="NEXT")
                pdf.cell(0, 5, f"Cashier: {sale.cashier.get_full_name() or sale.cashier.username}", new_x="LMARGIN", new_y="NEXT")
                if sale.customer:
                    pdf.cell(0, 5, f"Customer: {sale.customer.name}", new_x="LMARGIN", new_y="NEXT")

                pdf.ln(3)
                pdf.line(15, pdf.get_y(), 201, pdf.get_y())
                pdf.ln(5)

                # Line items header
                pdf.set_font("Helvetica", "B", 9)
                pdf.cell(90, 6, "ITEM", border=0)
                pdf.cell(20, 6, "QTY", border=0, align="R")
                pdf.cell(30, 6, "PRICE", border=0, align="R")
                pdf.cell(30, 6, "TOTAL", border=0, align="R", new_x="LMARGIN", new_y="NEXT")
                pdf.line(15, pdf.get_y(), 201, pdf.get_y())
                pdf.ln(2)

                # Line items
                pdf.set_font("Helvetica", size=9)
                for line in sale.lines.select_related("product").all():
                    pdf.cell(90, 6, line.product.name[:30], border=0)
                    pdf.cell(20, 6, str(line.quantity), border=0, align="R")
                    pdf.cell(30, 6, f"{currency}{line.unit_price:.2f}", border=0, align="R")
                    pdf.cell(30, 6, f"{currency}{line.line_total:.2f}", border=0, align="R", new_x="LMARGIN", new_y="NEXT")

                    if line.discount_amount > 0:
                        pdf.set_font("Helvetica", "I", 8)
                        discount_type = "Pct" if line.discount_type == "percent" else "Fix"
                        pdf.cell(90, 5, f"  Discount ({discount_type} {line.discount_value})", border=0)
                        pdf.cell(50, 5, "", border=0)
                        pdf.cell(30, 5, f"-{currency}{line.discount_amount:.2f}", border=0, align="R", new_x="LMARGIN", new_y="NEXT")
                        pdf.set_font("Helvetica", size=9)

                pdf.ln(2)
                pdf.line(15, pdf.get_y(), 201, pdf.get_y())
                pdf.ln(5)

                # Totals
                pdf.set_font("Helvetica", size=10)
                pdf.cell(120, 7, "", border=0)
                pdf.cell(30, 7, "Subtotal:", border=0, align="R")
                pdf.cell(30, 7, f"{currency}{sale.subtotal:.2f}", border=0, align="R", new_x="LMARGIN", new_y="NEXT")

                if sale.discount_total > 0:
                    pdf.cell(120, 7, "", border=0)
                    pdf.cell(30, 7, "Discounts:", border=0, align="R")
                    pdf.cell(30, 7, f"-{currency}{sale.discount_total:.2f}", border=0, align="R", new_x="LMARGIN", new_y="NEXT")

                taxable_amount = sale.total
                tax_amount = (taxable_amount * self.config.tax_rate).quantize(Decimal("0.01"))
                if tax_amount > 0:
                    pdf.cell(120, 7, "", border=0)
                    pdf.cell(30, 7, f"Tax ({self.config.tax_rate * 100:.0f}%):", border=0, align="R")
                    pdf.cell(30, 7, f"{currency}{tax_amount:.2f}", border=0, align="R", new_x="LMARGIN", new_y="NEXT")

                pdf.line(135, pdf.get_y(), 201, pdf.get_y())
                pdf.ln(3)

                pdf.set_font("Helvetica", "B", 12)
                pdf.cell(120, 8, "", border=0)
                pdf.cell(30, 8, "TOTAL:", border=0, align="R")
                pdf.cell(30, 8, f"{currency}{sale.total:.2f}", border=0, align="R", new_x="LMARGIN", new_y="NEXT")

                pdf.ln(8)

                # Payments
                pdf.set_font("Helvetica", "B", 10)
                pdf.cell(0, 6, "PAYMENTS:", new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", size=10)
                for payment in sale.payments.all():
                    method_label = payment.get_method_display()
                    amount_str = f"{currency}{payment.amount:.2f}"
                    pdf.cell(120, 6, method_label, border=0)
                    pdf.cell(30, 6, amount_str, border=0, align="R", new_x="LMARGIN", new_y="NEXT")

                    if payment.method == "cash" and payment.tendered is not None:
                        pdf.set_font("Helvetica", size=9)
                        pdf.cell(120, 5, "  Tendered:", border=0)
                        pdf.cell(30, 5, f"{currency}{payment.tendered:.2f}", border=0, align="R", new_x="LMARGIN", new_y="NEXT")
                        pdf.cell(120, 5, "  Change:", border=0)
                        pdf.cell(30, 5, f"{currency}{payment.change_due:.2f}", border=0, align="R", new_x="LMARGIN", new_y="NEXT")
                        pdf.set_font("Helvetica", size=10)

                pdf.ln(5)
                pdf.line(15, pdf.get_y(), 201, pdf.get_y())
                pdf.ln(5)

                # Return policy
                if self.config.return_policy:
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.multi_cell(0, 4, self.config.return_policy, align="C")
                    pdf.ln(5)

                pdf.set_font("Helvetica", "B", 10)
                pdf.cell(0, 6, "Thank you for your business!", align="C", new_x="LMARGIN", new_y="NEXT")
                if self.config.store_website:
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.cell(0, 5, self.config.store_website, align="C", new_x="LMARGIN", new_y="NEXT")

                pdf_output = pdf.output(dest="S")
                if isinstance(pdf_output, bytearray):
                    pdf_output = bytes(pdf_output)
                return base64.b64encode(pdf_output).decode("ascii")

        except ImportError:
            raise RuntimeError(
                "PDF generation requires either 'reportlab' or 'fpdf2' package. "
                "Install with: pip install reportlab OR pip install fpdf2"
            )


def get_receipt_config_from_business() -> ReceiptConfig:
    """
    Create a ReceiptConfig from the BusinessInfo singleton.

    Returns:
        ReceiptConfig populated with business information.
    """
    from business.models import BusinessInfo

    biz = BusinessInfo.get()
    return ReceiptConfig(
        store_name=biz.company_name or "eixnPOS Store",
        store_phone=biz.phone or "",
        store_email=biz.email or "",
        store_address=biz.address or "",
        store_website=biz.website or "",
        return_policy="Returns accepted within 30 days with receipt.",
        tax_rate=Decimal("0.00"),  # Could be configurable per business
    )