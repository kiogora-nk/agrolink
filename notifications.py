"""
Notification helpers for BioFarm Fruits.

Handles REAL email delivery (Gmail SMTP via Flask-Mail) plus click-to-contact
link builders for WhatsApp / SMS / phone / mailto. No paid SMS gateway is
used: staff reach customers through WhatsApp / SMS links opened from the admin
panel, and transactional messages go out by email.

Email fails soft: if SMTP credentials are not configured (local dev), the
message is logged to the console instead of raising, so the app keeps working.
"""

from flask import current_app
from flask_mail import Mail, Message

mail = Mail()


def init_mail(app):
    """Attach Flask-Mail to the app. Call once during app setup."""
    mail.init_app(app)


def _mail_configured():
    return bool(current_app.config.get('MAIL_USERNAME') and
                current_app.config.get('MAIL_PASSWORD'))


def send_email(subject, recipients, body, html=None, attachments=None):
    """Send an email. Returns True if handed to SMTP, False if only logged.

    `attachments` is a list of (filename, bytes, mimetype) tuples.

    Never raises on delivery failure - notifications must not break a checkout
    or an admin action.
    """
    if isinstance(recipients, str):
        recipients = [recipients]
    recipients = [r for r in recipients if r]
    if not recipients:
        return False

    if not _mail_configured():
        current_app.logger.info(
            '[EMAIL not sent - SMTP not configured]\nTo: %s\nSubject: %s\n%s%s',
            ', '.join(recipients), subject, body,
            f'\n[+{len(attachments)} attachment(s)]' if attachments else '',
        )
        return False

    try:
        msg = Message(subject=subject, recipients=recipients, body=body)
        if html:
            msg.html = html
        for filename, data, mimetype in (attachments or []):
            msg.attach(filename=filename, content_type=mimetype, data=data)
        mail.send(msg)
        return True
    except Exception as exc:  # noqa: BLE001 - log and continue
        current_app.logger.error('Email send failed: %s', exc)
        return False


def normalize_msisdn(phone, country_code='254'):
    """Turn a Kenyan number into a bare international MSISDN for wa.me / sms.

    '0746767123'   -> '254746767123'
    '+254746767123'-> '254746767123'
    '254746767123' -> '254746767123'
    Returns '' if there are no usable digits.
    """
    if not phone:
        return ''
    digits = ''.join(c for c in str(phone) if c.isdigit())
    if not digits:
        return ''
    if digits.startswith('0'):
        digits = country_code + digits[1:]
    elif digits.startswith(country_code):
        pass
    elif len(digits) <= 9:
        digits = country_code + digits
    return digits


def whatsapp_link(phone, text=''):
    """Build a wa.me click-to-chat URL, or '' if the number is unusable."""
    from urllib.parse import quote
    msisdn = normalize_msisdn(phone)
    if not msisdn:
        return ''
    url = f'https://wa.me/{msisdn}'
    if text:
        url += f'?text={quote(text)}'
    return url


def sms_link(phone, text=''):
    """Build an sms: link that opens the phone's messaging app prefilled."""
    from urllib.parse import quote
    msisdn = normalize_msisdn(phone)
    if not msisdn:
        return ''
    url = f'sms:+{msisdn}'
    if text:
        url += f'?body={quote(text)}'
    return url


def tel_link(phone):
    msisdn = normalize_msisdn(phone)
    return f'tel:+{msisdn}' if msisdn else ''


def notify_admins_new_order(order, admin_emails, attachments=None):
    """Email all admins that a new order has been placed."""
    subject = f'New order {order.order_number or order.receipt_number or order.id}'
    body = (
        f'A new order has been placed on BioFarm Fruits.\n\n'
        f'Order: {order.receipt_number or order.id}\n'
        f'Customer: {order.user.username} ({order.user.email})\n'
        f'Product: {order.product.name}\n'
        f'Quantity: {order.quantity}\n'
        f'Total: KES {order.total_price:,.2f}\n'
        f'Delivery phone: {order.delivery_phone or "-"}\n'
        f'Delivery address: {order.delivery_address or "-"}\n'
        f'Notes: {order.notes or "-"}\n\n'
        f'The receipt is attached.\n\n'
        f'Log in to the admin panel to process it.'
    )
    return send_email(subject, admin_emails, body, attachments=attachments)


def notify_customer_order_confirmation(order, attachments=None):
    """Email the customer confirming their order was received (receipt attached)."""
    if not order.user or not order.user.email:
        return False
    subject = f'Your BioFarm Fruits receipt - {order.receipt_number or order.id}'
    body = (
        f'Hi {order.user.username},\n\n'
        f'Thank you for your order with BioFarm Fruits!\n\n'
        f'Order: {order.receipt_number or order.id}\n'
        f'Product: {order.product.name}\n'
        f'Quantity: {order.quantity}\n'
        f'Total: KES {order.total_price:,.2f}\n\n'
        f'Your official receipt is attached to this email (PDF).\n'
        f'We will contact you shortly to arrange delivery and payment.\n\n'
        f'BioFarm Fruits'
    )
    return send_email(subject, order.user.email, body, attachments=attachments)
