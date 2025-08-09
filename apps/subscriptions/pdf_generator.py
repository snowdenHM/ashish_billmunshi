"""
PDF generation for invoices and reports
"""
import io
import os
from decimal import Decimal
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class PDFGenerator:
    """
    Base class for PDF generation
    """

    def __init__(self):
        self.page_size = 'A4'
        self.margin_top = 72
        self.margin_bottom = 72
        self.margin_left = 72
        self.margin_right = 72

    def generate_pdf(self, template_name: str, context: Dict[str, Any], filename: str = None) -> bytes:
        """
        Generate PDF from template

        Args:
            template_name: Name of the template file
            context: Template context
            filename: Optional filename for the PDF

        Returns:
            bytes: PDF content
        """
        try:
            # Try using WeasyPrint (recommended for HTML/CSS to PDF)
            return self._generate_with_weasyprint(template_name, context)
        except ImportError:
            try:
                # Fallback to ReportLab
                return self._generate_with_reportlab(template_name, context)
            except ImportError:
                # Final fallback to simple text PDF
                return self._generate_simple_pdf(context)

    def _generate_with_weasyprint(self, template_name: str, context: Dict[str, Any]) -> bytes:
        """Generate PDF using WeasyPrint"""
        try:
            import weasyprint
            from django.template.loader import render_to_string

            # Render HTML template
            html_string = render_to_string(template_name, context)

            # Generate PDF
            html = weasyprint.HTML(string=html_string, base_url=settings.STATIC_URL)
            pdf_file = html.write_pdf()

            return pdf_file

        except ImportError:
            raise ImportError("WeasyPrint not installed. Install with: pip install weasyprint")

    def _generate_with_reportlab(self, template_name: str, context: Dict[str, Any]) -> bytes:
        """Generate PDF using ReportLab"""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch
            from reportlab.lib import colors

            buffer = io.BytesIO()
            doc = SimpleDocTemplate(
                buffer,
                pagesize=A4,
                rightMargin=self.margin_right,
                leftMargin=self.margin_left,
                topMargin=self.margin_top,
                bottomMargin=self.margin_bottom
            )

            story = []
            styles = getSampleStyleSheet()

            # Add content based on context (this is a simplified example)
            if 'invoice' in context:
                story.extend(self._create_invoice_content(context, styles))
            else:
                story.extend(self._create_generic_content(context, styles))

            doc.build(story)
            pdf = buffer.getvalue()
            buffer.close()

            return pdf

        except ImportError:
            raise ImportError("ReportLab not installed. Install with: pip install reportlab")

    def _generate_simple_pdf(self, context: Dict[str, Any]) -> bytes:
        """Generate simple text-based PDF fallback"""
        try:
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import A4

            buffer = io.BytesIO()
            p = canvas.Canvas(buffer, pagesize=A4)
            width, height = A4

            # Simple text-based invoice
            y = height - 100
            p.setFont("Helvetica-Bold", 16)
            p.drawString(100, y, "INVOICE")

            y -= 40
            p.setFont("Helvetica", 12)

            if 'invoice' in context:
                invoice = context['invoice']
                lines = [
                    f"Invoice Number: {invoice.invoice_number}",
                    f"Date: {invoice.issue_date.strftime('%B %d, %Y')}",
                    f"Due Date: {invoice.due_date.strftime('%B %d, %Y')}",
                    "",
                    "Bill To:",
                    f"{invoice.subscription.organization.name}",
                    "",
                    f"Subtotal: ${invoice.subtotal}",
                    f"Tax: ${invoice.tax_amount}",
                    f"Total: ${invoice.total_amount}",
                ]

                for line in lines:
                    p.drawString(100, y, line)
                    y -= 20

            p.showPage()
            p.save()

            pdf = buffer.getvalue()
            buffer.close()

            return pdf

        except ImportError:
            raise ImportError("No PDF library available")

    def _create_invoice_content(self, context: Dict[str, Any], styles) -> list:
        """Create ReportLab content for invoice"""
        from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors
        from reportlab.lib.units import inch

        story = []
        invoice = context['invoice']
        subscription = invoice.subscription
        organization = subscription.organization

        # Header
        header_style = styles['Heading1']
        story.append(Paragraph("INVOICE", header_style))
        story.append(Spacer(1, 20))

        # Invoice details
        invoice_data = [
            ['Invoice Number:', invoice.invoice_number],
            ['Invoice Date:', invoice.issue_date.strftime('%B %d, %Y')],
            ['Due Date:', invoice.due_date.strftime('%B %d, %Y')],
        ]

        invoice_table = Table(invoice_data, colWidths=[2 * inch, 3 * inch])
        invoice_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
        ]))

        story.append(invoice_table)
        story.append(Spacer(1, 30))

        # Bill to
        story.append(Paragraph("Bill To:", styles['Heading3']))
        story.append(Paragraph(organization.name, styles['Normal']))
        if organization.address:
            story.append(Paragraph(organization.address, styles['Normal']))
        story.append(Spacer(1, 20))

        # Service period
        story.append(Paragraph(
            f"Service Period: {invoice.period_start.strftime('%B %d, %Y')} - {invoice.period_end.strftime('%B %d, %Y')}",
            styles['Normal']
        ))
        story.append(Spacer(1, 20))

        # Amount details
        amount_data = [
            ['Description', 'Amount'],
            [f'{subscription.plan.name} Plan', f'${invoice.subtotal}'],
            ['Tax', f'${invoice.tax_amount}'],
            ['Total', f'${invoice.total_amount}'],
        ]

        amount_table = Table(amount_data, colWidths=[4 * inch, 2 * inch])
        amount_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
            ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))

        story.append(amount_table)
        story.append(Spacer(1, 30))

        # Footer
        if invoice.notes:
            story.append(Paragraph("Notes:", styles['Heading4']))
            story.append(Paragraph(invoice.notes, styles['Normal']))

        return story

    def _create_generic_content(self, context: Dict[str, Any], styles) -> list:
        """Create generic ReportLab content"""
        from reportlab.platypus import Paragraph, Spacer

        story = []

        # Generic document
        story.append(Paragraph("Document", styles['Heading1']))
        story.append(Spacer(1, 20))

        for key, value in context.items():
            if isinstance(value, (str, int, float, Decimal)):
                story.append(Paragraph(f"{key}: {value}", styles['Normal']))
                story.append(Spacer(1, 10))

        return story


class InvoicePDFGenerator(PDFGenerator):
    """
    Specialized PDF generator for invoices
    """

    def generate_invoice_pdf(self, invoice) -> bytes:
        """
        Generate PDF for an invoice

        Args:
            invoice: SubscriptionInvoice instance

        Returns:
            bytes: PDF content
        """
        context = {
            'invoice': invoice,
            'subscription': invoice.subscription,
            'organization': invoice.subscription.organization,
            'plan': invoice.subscription.plan,
            'owner': invoice.subscription.organization.owner,
            'generated_at': timezone.now(),
            'site_name': getattr(settings, 'PROJECT_METADATA', {}).get('NAME', 'Billmunshi'),
        }

        template_name = 'subscriptions/pdf/invoice.html'
        return self.generate_pdf(template_name, context, f"invoice_{invoice.invoice_number}.pdf")

    def save_invoice_pdf(self, invoice, save_path: str = None) -> str:
        """
        Generate and save invoice PDF to file

        Args:
            invoice: SubscriptionInvoice instance
            save_path: Optional custom save path

        Returns:
            str: File path where PDF was saved
        """
        pdf_content = self.generate_invoice_pdf(invoice)

        if save_path is None:
            # Default save path
            invoice_dir = os.path.join(settings.MEDIA_ROOT, 'invoices', str(invoice.subscription.organization.id))
            os.makedirs(invoice_dir, exist_ok=True)
            save_path = os.path.join(invoice_dir, f"invoice_{invoice.invoice_number}.pdf")

        with open(save_path, 'wb') as f:
            f.write(pdf_content)

        logger.info(f"Invoice PDF saved: {save_path}")
        return save_path


class ReportPDFGenerator(PDFGenerator):
    """
    PDF generator for various reports
    """

    def generate_usage_report(self, subscription, start_date, end_date) -> bytes:
        """
        Generate usage report PDF

        Args:
            subscription: OrganizationSubscription instance
            start_date: Report start date
            end_date: Report end date

        Returns:
            bytes: PDF content
        """
        from .models import UsageRecord
        from django.db.models import Sum

        # Get usage data
        usage_records = UsageRecord.objects.filter(
            subscription=subscription,
            usage_date__range=[start_date, end_date]
        )

        usage_summary = {}
        for usage_type, _ in UsageRecord.USAGE_TYPES:
            total = usage_records.filter(usage_type=usage_type).aggregate(
                total=Sum('quantity')
            )['total'] or 0
            usage_summary[usage_type] = total

        context = {
            'subscription': subscription,
            'organization': subscription.organization,
            'plan': subscription.plan,
            'start_date': start_date,
            'end_date': end_date,
            'usage_summary': usage_summary,
            'usage_records': usage_records[:100],  # Limit for PDF
            'generated_at': timezone.now(),
        }

        template_name = 'subscriptions/pdf/usage_report.html'
        return self.generate_pdf(template_name, context)

    def generate_monthly_statement(self, subscription, month, year) -> bytes:
        """
        Generate monthly statement PDF

        Args:
            subscription: OrganizationSubscription instance
            month: Month number (1-12)
            year: Year

        Returns:
            bytes: PDF content
        """
        from .models import SubscriptionInvoice, UsageRecord
        from datetime import datetime
        from django.db.models import Sum

        # Get month's invoices
        month_invoices = SubscriptionInvoice.objects.filter(
            subscription=subscription,
            issue_date__month=month,
            issue_date__year=year
        )

        # Get month's usage
        month_usage = UsageRecord.objects.filter(
            subscription=subscription,
            usage_date__month=month,
            usage_date__year=year
        ).values('usage_type').annotate(
            total=Sum('quantity')
        )

        context = {
            'subscription': subscription,
            'organization': subscription.organization,
            'plan': subscription.plan,
            'month': datetime(year, month, 1).strftime('%B %Y'),
            'invoices': month_invoices,
            'usage_data': month_usage,
            'total_charged': sum(inv.total_amount for inv in month_invoices),
            'generated_at': timezone.now(),
        }

        template_name = 'subscriptions/pdf/monthly_statement.html'
        return self.generate_pdf(template_name, context)


# Singleton instances
invoice_pdf_generator = InvoicePDFGenerator()
report_pdf_generator = ReportPDFGenerator()


# Convenience functions
def generate_invoice_pdf(invoice) -> bytes:
    """Generate PDF for an invoice"""
    return invoice_pdf_generator.generate_invoice_pdf(invoice)


def generate_usage_report_pdf(subscription, start_date, end_date) -> bytes:
    """Generate usage report PDF"""
    return report_pdf_generator.generate_usage_report(subscription, start_date, end_date)


def generate_monthly_statement_pdf(subscription, month, year) -> bytes:
    """Generate monthly statement PDF"""
    return report_pdf_generator.generate_monthly_statement(subscription, month, year)