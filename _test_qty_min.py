"""Check the order-quantity floor on /place-order/<id>.

Quantity must be allowed as low as 1, and rejected for 0, negatives and
garbage without touching stock. Cleans up all test data afterwards.
"""
import re

from app import app, db
from app import User, Product, Order

client = app.test_client()

BUYER = 'diag_qty_buyer'


def token(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else ''


def cleanup():
    with app.app_context():
        user = User.query.filter_by(username=BUYER).first()
        if user:
            Order.query.filter_by(user_id=user.id).delete()
            db.session.delete(user)
            db.session.commit()


cleanup()

print('== register buyer ==')
html = client.get('/register').get_data(as_text=True)
r = client.post('/register', data={
    'username': BUYER, 'email': f'{BUYER}@example.com',
    'phone': '0712 345 678', 'password': 'Password@123',
    'confirm_password': 'Password@123', 'role': 'buyer',
    'csrf_token': token(html)}, follow_redirects=False)
print('status:', r.status_code, r.location or '')

with app.app_context():
    buyer = User.query.filter_by(username=BUYER).first()
    product = (Product.query.filter_by(status='approved')
               .filter(Product.stock > 5).first())
    pid, stock_before = product.id, product.stock
print('buyer:', bool(buyer), '| product:', pid, '| stock before:', stock_before)


def place(quantity):
    page = client.get(f'/product/{pid}').get_data(as_text=True)
    return client.post(f'/place-order/{pid}', data={
        'quantity': quantity,
        'delivery_address': 'Diag Test Street 1, Nairobi',
        'delivery_phone': '0712 345 678',
        'csrf_token': token(page)}, follow_redirects=True)


def orders_count():
    with app.app_context():
        return Order.query.filter_by(user_id=buyer.id).count()


def stock_now():
    with app.app_context():
        return db.session.get(Product, pid).stock


print('== [1] quantity 0 -> rejected, no order, stock untouched ==')
r = place(0)
print('rejected:', 'at least 1' in r.get_data(as_text=True),
      '| orders:', orders_count(), '| stock:', stock_now())

print('== [2] quantity -5 -> rejected, no order, stock untouched ==')
r = place(-5)
print('rejected:', 'at least 1' in r.get_data(as_text=True),
      '| orders:', orders_count(), '| stock:', stock_now())

print('== [3] quantity 1 -> accepted ==')
r = place(1)
print('orders:', orders_count(), '| stock:', stock_now(),
      '(expected', stock_before - 1, ')')

print('== [4] over-stock still blocked ==')
r = place(stock_before + 999)
print('rejected:', 'only' in r.get_data(as_text=True).lower(),
      '| orders:', orders_count())

cleanup()
print('== cleanup done ==')
