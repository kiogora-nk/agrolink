"""PDF and spreadsheet report builders for BioFarm Fruits.

Everything reportlab-related lives here so receipts, customer monthly
statements and the chief-admin system report share one place (and one
look). All builders return a BytesIO ready to be emailed or downloaded.
"""

import csv
import os
from datetime import datetime, timezone
from io import BytesIO, StringIO

import qrcode
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

# Brand palette (matches the site: slate + amber, small emerald accents).
SLATE_DARK = colors.HexColor('#1e293b')   # slate-800
AMBER = colors.HexColor('#d97706')        # amber-600
AMBER_LIGHT = colors.HexColor('#fef3c7')  # amber-100
SLATE_LIGHT = colors.HexColor('#f1f5f9')  # slate-100


def _styles():
    base = getSampleStyleSheet()
    return {
        'title': ParagraphStyle('BfTitle', parent=base['Heading1'], fontSize=22,
                                textColor=SLATE_DARK, spaceAfter=4),
        'heading': ParagraphStyle('BfHeading', parent=base['Heading1'], fontSize=14,
                                  textColor=SLATE_DARK, spaceBefore=10, spaceAfter=6),
        'normal': base['Normal'],
        'small': ParagraphStyle('BfSmall', parent=base['Normal'], fontSize=9,
                                textColor=colors.HexColor('#64748b')),
        'caption': ParagraphStyle('BfCaption', parent=base['Normal'], fontSize=9,
                                  textColor=colors.HexColor('#64748b'),
                                  alignment=TA_CENTER),
    }


def _logo_flowable(logo_path, height=0.75 * inch):
    """The brand logo as a reportlab Image, aspect ratio preserved.

    ImageReader sniffs the real file format, so a mismatched extension
    (e.g. a JPEG saved as logo.png.jpg) still works. Returns None when
    the file is missing so callers can fall back to text-only headers.
    """
    if not logo_path or not os.path.exists(logo_path):
        return None
    try:
        img_w, img_h = ImageReader(logo_path).getSize()
        if not img_w or not img_h:
            return None
        return Image(logo_path, width=height * img_w / img_h, height=height)
    except Exception:  # noqa: BLE001 - a broken logo must not kill the report
        return None


def _qr_flowable(data, size=1.1 * inch):
    """A QR code encoding `data`, rendered as an in-memory PNG flowable."""
    qr_img = qrcode.QRCode(border=2, box_size=10)
    qr_img.add_data(data)
    qr_img.make(fit=True)
    buf = BytesIO()
    qr_img.make_image(fill_color='black', back_color='white').save(buf, format='PNG')
    buf.seek(0)
    return Image(buf, width=size, height=size)


def receipt_qr_data(order, company):
    """Text encoded in the receipt QR: everything needed to check the
    receipt without a network connection. Shared by the PDF and the
    HTML receipt page."""
    lines = [
        company['name'],
        f"Receipt: {order.receipt_number or order.id}",
        f"Order: {order.order_number or order.id}",
        f"Date: {order.created_at.strftime('%Y-%m-%d %H:%M') if order.created_at else '-'}",
        f"Customer: {order.user.username if order.user else '-'}",
        f"Item: {(order.product.name if order.product else 'N/A')} x {order.quantity or 1}",
        f"Total: KES {order.total_price or 0:,.2f}",
        f"Status: {order.status.title()}",
    ]
    return '\n'.join(lines)


def _header(elements, styles, company, logo_height=0.75 * inch):
    """Brand header: logo beside the company block when one is configured."""
    name, address, phone = company['name'], company['address'], company['phone']
    logo = _logo_flowable(company.get('logo_path'), height=logo_height)
    if logo is not None:
        info = [
            Paragraph(name, styles['title']),
            Paragraph(address or '', styles['small']),
            Paragraph(f'Phone: {phone}', styles['small']),
        ]
        header = Table([[logo, info]], colWidths=[1.5 * inch, 4.9 * inch])
        header.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(header)
    else:
        elements.append(Paragraph(name, styles['title']))
        elements.append(Paragraph(address or '', styles['small']))
        elements.append(Paragraph(f'Phone: {phone}', styles['small']))
    elements.append(Spacer(1, 14))


def _data_table(rows, col_widths, header_bg=SLATE_DARK):
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), header_bg),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, SLATE_LIGHT]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def build_receipt_pdf(order, company):
    """A single-order receipt. `company` is a dict with name/address/phone/email."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title=f'Receipt {order.receipt_number or order.id}')
    elements = []
    styles = _styles()

    _header(elements, styles, company)
    elements.append(Paragraph(f'RECEIPT #{order.receipt_number or order.id}', styles['heading']))
    elements.append(Paragraph(
        f'Date: {order.created_at.strftime("%Y-%m-%d %H:%M") if order.created_at else "-"}'
        f' &nbsp;&nbsp; Order: {order.order_number or order.id}'
        f' &nbsp;&nbsp; Status: {order.status.title()}', styles['small']))
    elements.append(Paragraph(f'Customer: {order.user.username} ({order.user.email})', styles['normal']))
    if order.delivery_address:
        elements.append(Paragraph(f'Deliver to: {order.delivery_address}'
                                  f'{f" &middot; {order.delivery_phone}" if order.delivery_phone else ""}',
                                  styles['normal']))
    elements.append(Spacer(1, 12))

    rows = [['Item', 'Quantity', 'Unit Price', 'Total']]
    product = order.product
    unit = (order.total_price / order.quantity) if order.quantity else (order.total_price or 0)
    rows.append([product.name if product else f'Product #{order.product_id}',
                 str(order.quantity or 1),
                 f'KES {unit:,.2f}',
                 f'KES {order.total_price or 0:,.2f}'])
    elements.append(_data_table(rows, [3.4 * inch, 1 * inch, 1.4 * inch, 1.4 * inch]))
    elements.append(Spacer(1, 14))
    elements.append(Paragraph(f'Total: KES {order.total_price or 0:,.2f}', styles['heading']))
    elements.append(Spacer(1, 24))
    elements.append(Paragraph('Thank you for shopping with us!', styles['normal']))
    elements.append(Paragraph(f'{company["name"]} &middot; {company["email"]}', styles['small']))

    # QR code so the receipt can be checked with any phone camera.
    try:
        qr = _qr_flowable(receipt_qr_data(order, company))
        qr_table = Table(
            [[qr], [Paragraph('Scan to check receipt details', styles['caption'])]],
            colWidths=[2.4 * inch])
        qr_table.hAlign = 'CENTER'
        qr_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        elements.append(Spacer(1, 10))
        elements.append(qr_table)
    except Exception:  # noqa: BLE001 - a QR failure must not kill the receipt
        pass

    doc.build(elements)
    buffer.seek(0)
    return buffer


def build_customer_monthly_pdf(user, orders, month_label, company):
    """A customer's personal order statement for one calendar month."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title=f'Monthly statement {month_label}')
    elements = []
    styles = _styles()

    _header(elements, styles, company)
    elements.append(Paragraph(f'Monthly Order Statement — {month_label}', styles['heading']))
    elements.append(Paragraph(f'Customer: {user.username} ({user.email})', styles['normal']))
    elements.append(Spacer(1, 10))

    rows = [['Date', 'Receipt', 'Item', 'Qty', 'Total', 'Status']]
    total = 0.0
    for o in orders:
        total += o.total_price or 0
        rows.append([
            o.created_at.strftime('%d %b') if o.created_at else '-',
            o.receipt_number or str(o.id),
            (o.product.name if o.product else f'Product #{o.product_id}')[:30],
            str(o.quantity or 1),
            f'KES {o.total_price or 0:,.2f}',
            o.status.title(),
        ])
    rows.append(['', '', '', '', f'KES {total:,.2f}', 'TOTAL'])
    elements.append(_data_table(rows, [0.8 * inch, 1.1 * inch, 2.2 * inch, 0.5 * inch, 1.1 * inch, 0.9 * inch]))
    elements.append(Spacer(1, 16))
    elements.append(Paragraph(
        f'You placed {len(orders)} order(s) totalling KES {total:,.2f} in {month_label}. '
        f'Thank you for shopping with {company["name"]}!', styles['normal']))

    doc.build(elements)
    buffer.seek(0)
    return buffer


def build_system_monthly_pdf(stats, orders, month_label, company):
    """The full system report for the chief admin: summary + every order."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title=f'System report {month_label}')
    elements = []
    styles = _styles()

    _header(elements, styles, company)
    elements.append(Paragraph(f'Monthly System Report — {month_label}', styles['heading']))
    elements.append(Paragraph(f'Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}',
                              styles['small']))
    elements.append(Spacer(1, 10))

    summary = [
        ['Metric', 'Value'],
        ['Orders placed', str(stats['orders'])],
        ['Revenue (KES)', f"{stats['revenue']:,.2f}"],
        ['Orders delivered', str(stats['delivered'])],
        ['Orders cancelled', str(stats['cancelled'])],
        ['New users', str(stats['new_users'])],
        ['Active customers with orders', str(stats['customers_with_orders'])],
        ['Top product', stats.get('top_product') or '-'],
    ]
    elements.append(_data_table(summary, [3.2 * inch, 2.4 * inch], header_bg=AMBER))
    elements.append(Spacer(1, 14))

    elements.append(Paragraph('All Orders', styles['heading']))
    rows = [['Date', 'Receipt', 'Customer', 'Item', 'Qty', 'Total', 'Status']]
    for o in orders:
        rows.append([
            o.created_at.strftime('%d %b') if o.created_at else '-',
            o.receipt_number or str(o.id),
            o.user.username if o.user else '-',
            (o.product.name if o.product else f'#{o.product_id}')[:24],
            str(o.quantity or 1),
            f'KES {o.total_price or 0:,.2f}',
            o.status.title(),
        ])
    elements.append(_data_table(rows, [0.7 * inch, 1 * inch, 1.1 * inch, 1.8 * inch, 0.4 * inch, 1 * inch, 0.7 * inch]))
    if not orders:
        elements.append(Paragraph('No orders this month.', styles['normal']))

    doc.build(elements)
    buffer.seek(0)
    return buffer


def build_system_monthly_csv(orders, stats, month_label):
    """Spreadsheet of every order in the month (opens directly in Excel)."""
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow([f'BioFarm Fruits — Monthly Orders {month_label}'])
    writer.writerow([])
    writer.writerow(['Date', 'Receipt #', 'Order #', 'Customer', 'Email', 'Item',
                     'Quantity', 'Unit Price', 'Total', 'Status', 'Delivery Phone', 'Delivery Address'])
    total = 0.0
    for o in orders:
        total += o.total_price or 0
        unit = (o.total_price / o.quantity) if o.quantity else (o.total_price or 0)
        writer.writerow([
            o.created_at.strftime('%Y-%m-%d %H:%M') if o.created_at else '',
            o.receipt_number or '',
            o.order_number or o.id,
            o.user.username if o.user else '',
            o.user.email if o.user else '',
            o.product.name if o.product else f'Product #{o.product_id}',
            o.quantity or 1,
            f'{unit:.2f}',
            f'{o.total_price or 0:.2f}',
            o.status,
            o.delivery_phone or '',
            o.delivery_address or '',
        ])
    writer.writerow([])
    writer.writerow(['', '', '', '', '', '', 'TOTAL', '', f'{total:.2f}', '', '', ''])
    writer.writerow([])
    writer.writerow(['Summary'])
    for key, label in [('orders', 'Orders'), ('revenue', 'Revenue (KES)'),
                       ('delivered', 'Delivered'), ('cancelled', 'Cancelled'),
                       ('new_users', 'New users'), ('customers_with_orders', 'Customers with orders')]:
        writer.writerow([label, stats.get(key, '')])
    data = out.getvalue().encode('utf-8')
    return BytesIO(data)
