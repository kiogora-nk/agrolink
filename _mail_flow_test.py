"""Exercise the order + contact email paths, including the PDF receipt attachment."""
import logging

import app as appmod
from app import User, Order, db

a = appmod.app
a.config['WTF_CSRF_ENABLED'] = False
logging.basicConfig(level=logging.DEBUG)

calls = []
orig = appmod.notifications.send_email


def spy(subject, recipients, body, html=None, attachments=None):
    ok = orig(subject, recipients, body, html=html, attachments=attachments)
    calls.append((subject, recipients, ok, len(attachments or [])))
    print(f'  -> send_email({subject!r}, to={recipients!r}, att={len(attachments or [])}) -> {ok}')
    return ok


appmod.notifications.send_email = spy

with a.app_context():
    o = Order.query.order_by(Order.id.desc()).first()
    print('latest order:', o.id if o else None)
    if o:
        print('customer:', o.user.username, o.user.email)
        print('receipt_number:', o.receipt_number, 'order_number:', o.order_number)
        att = appmod._receipt_attachment(o)
        print('receipt attachment ->', 'None (FAILED)' if att is None else f'{att[0]} {len(att[1])} bytes')
        print('admin emails:', appmod._admin_emails())

print('\n--- POST /contact ---')
c = a.test_client()
r = c.post('/contact', data={'name': 'Test Person', 'email': 'shop@x.com',
                             'phone': '0746767123', 'message': 'Route email test.'},
           follow_redirects=True)
print('status', r.status_code)
print('calls:', calls)
