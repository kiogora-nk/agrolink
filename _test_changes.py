"""End-to-end check of the four requested changes:
1. register: captcha gone, phone mandatory
2. product page: contact-admin routing
3. admin orders: search + one-click status buttons
4. full order lifecycle: place -> confirm -> deliver
Cleans up all test data afterwards.
"""
import io
import re

from app import create_app, db
from app import User, Product, Order

app = create_app()
client = app.test_client()

def token(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else ''

print('== [1] register page: no captcha, phone required ==')
r = client.get('/register')
html = r.get_data(as_text=True)
print('status:', r.status_code,
      '| captcha gone:', 'captcha' not in html.lower(),
      '| phone required:', 'name="phone"' in html and 'required' in
                           html[html.find('name="phone"') - 200:html.find('name="phone"') + 300])

print('== [2] register without phone -> rejected ==')
t = token(html)
r = client.post('/register', data={
    'username': 'diag_buyer', 'email': 'diag_buyer@example.com',
    'phone': '', 'password': 'Password@123', 'confirm_password': 'Password@123',
    'role': 'buyer', 'csrf_token': t}, follow_redirects=False)
print('status:', r.status_code, '| rejected:', r.status_code == 200 and
      'phone' in r.get_data(as_text=True).lower())

print('== [3] register with short phone -> rejected ==')
r = client.post('/register', data={
    'username': 'diag_buyer', 'email': 'diag_buyer@example.com',
    'phone': '123', 'password': 'Password@123', 'confirm_password': 'Password@123',
    'role': 'buyer', 'csrf_token': t}, follow_redirects=False)
print('rejected:', 'too short' in r.get_data(as_text=True))

print('== [4] register with valid phone -> success ==')
r = client.post('/register', data={
    'username': 'diag_buyer', 'email': 'diag_buyer@example.com',
    'phone': '0712 345 678', 'password': 'Password@123',
    'confirm_password': 'Password@123', 'role': 'buyer', 'csrf_token': t},
    follow_redirects=False)
print('status:', r.status_code, r.location or '')

with app.app_context():
    buyer = User.query.filter_by(username='diag_buyer').first()
    print('user created:', bool(buyer), '| phone:', buyer.phone if buyer else None)

with app.app_context():
    product = (Product.query.filter_by(status='approved')
               .filter(Product.stock > 0).first())
    pid = product.id
    stock_before = product.stock
print(f'== [5] product {pid} page: contact-admin routing ==')
r = client.get(f'/product/{pid}')
html = r.get_data(as_text=True)
print('status:', r.status_code, '| ask-about link:', 'Ask about this product' in html,
      '| through-us note:', 'handled by our team' in html)

print('== [6] place order as buyer ==')
t = token(client.get(f'/product/{pid}').get_data(as_text=True))
r = client.post(f'/place-order/{pid}', data={
    'quantity': 1, 'delivery_address': 'Diag Test Street 1, Nairobi',
    'delivery_phone': '0712 345 678', 'notes': 'diag order', 'csrf_token': t},
    follow_redirects=False)
print('status:', r.status_code, r.location or '')

with app.app_context():
    order = (Order.query.filter(Order.user_id == buyer.id)
             .order_by(Order.id.desc()).first())
    print('order created:', bool(order), '| id:', order.id if order else None,
          '| status:', order.status if order else None)

print('== [7] admin orders page ==')
client.get('/logout')
r = client.get('/login')
t = token(r.get_data(as_text=True))
client.post('/login', data={'username': 'admin', 'password': 'Admin@2025',
                            'csrf_token': t})
r = client.get('/admin/orders')
html = r.get_data(as_text=True)
print('status:', r.status_code,
      '| search box:', 'name="q"' in html,
      '| one-click buttons:', 'Confirm order' in html,
      '| correction select:', html.count('Update</button>') >= 1)

print('== [8] search finds the diag order ==')
r = client.get('/admin/orders?q=diag_buyer')
html = r.get_data(as_text=True)
print('found by customer:', 'diag_buyer' in html)
r = client.get('/admin/orders?q=zzz-no-such-thing')
print('no-match message:', 'No orders match your search.' in r.get_data(as_text=True))

print('== [9] one-click status flow ==')
r = client.post(f'/admin/order/{order.id}/status', data={
    'status': 'confirmed', 'csrf_token': token(client.get('/admin/orders').get_data(as_text=True))},
    follow_redirects=False)
print('confirm:', r.status_code)
r = client.post(f'/admin/order/{order.id}/status', data={
    'status': 'delivered', 'csrf_token': token(client.get('/admin/orders').get_data(as_text=True))},
    follow_redirects=False)
print('deliver:', r.status_code)
with app.app_context():
    o = db.session.get(Order, order.id)
    print('final status:', o.status, '| delivered_at set:', bool(o.delivered_at))

print('== [10] cleanup ==')
with app.app_context():
    db.session.delete(db.session.get(Order, order.id))
    p = db.session.get(Product, pid)
    p.stock = stock_before
    db.session.delete(User.query.filter_by(username='diag_buyer').first())
    db.session.commit()
    print('test order + user removed, stock restored to', p.stock)
