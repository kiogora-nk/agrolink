"""POS smoke test: cash sale, stock deduction, duplicate checkout, M-Pesa states."""
import json
import os
import shutil

snap = os.path.join(os.environ.get('TEMP', '.'), 'agrolink_head', 'instance', 'biofarm.db')
db = os.path.join(os.environ.get('TEMP', '.'), 'postest.db')
shutil.copyfile(snap, db)
os.environ['DATABASE_URL'] = 'sqlite:///' + db.replace(os.sep, '/')

from app import create_app, db as _db, Product, Sale, SaleItem, StockMovement, User  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

app = create_app()
app.config['TESTING'] = True
app.config['WTF_CSRF_ENABLED'] = False

ok = fail = 0


def check(label, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  PASS  {label}')
    else:
        fail += 1
        print(f'  FAIL  {label} {extra}')


with app.app_context():
    for username, role in (('cash1', 'cashier'), ('mgr1', 'admin')):
        u = User.query.filter_by(username=username).first()
        if not u:
            u = User(username=username, email=f'{username}@t.test',
                     password_hash=generate_password_hash('pw'),
                     role=role, is_active=True, verified=True)
            _db.session.add(u)
    _db.session.commit()

client = app.test_client()


def login(username):
    # /login redirects away when a session is already open, so sign out first.
    client.get('/logout')
    client.post('/login', data={'username': username, 'password': 'pw'},
                follow_redirects=True)


print('\n--- authorization ---')
client.get('/logout')
r = client.get('/pos', follow_redirects=False)
check('anonymous is redirected away from /pos', r.status_code in (301, 302), r.status_code)

login('cash1')
r = client.get('/pos')
check('cashier can open the till', r.status_code == 200, r.status_code)
r = client.get('/pos/summary', follow_redirects=False)
check('cashier is blocked from the daily summary', r.status_code in (301, 302), r.status_code)
r = client.get('/admin/dashboard', follow_redirects=False)
check('cashier is blocked from the admin dashboard', r.status_code in (301, 302), r.status_code)

print('\n--- cash sale ---')
with app.app_context():
    p = Product.query.filter(Product.status == 'approved', Product.stock > 5).first()
    pid, before, price = p.id, p.stock, p.price
    print(f'  product #{pid} {p.name!r} stock={before} price={price}')

r = client.post('/pos/checkout', data={
    'idempotency_key': 'key-cash-1',
    'cart': json.dumps([{'id': pid, 'qty': 2}]),
    'payment_method': 'cash',
}, follow_redirects=False)
check('cash checkout redirects to a receipt', r.status_code == 302 and '/pos/sale/' in r.headers.get('Location', ''), r.status_code)

with app.app_context():
    sale = Sale.query.filter_by(idempotency_key='key-cash-1').first()
    check('sale was recorded', sale is not None)
    check('sale total is price x qty (server-side)',
          sale and abs(sale.total - price * 2) < 0.01, sale.total if sale else None)
    check('cash sale is confirmed', sale and sale.payment_status == 'confirmed')
    check('sale number generated', sale and sale.sale_number)
    check('stock deducted by 2', Product.query.get(pid).stock == before - 2,
          Product.query.get(pid).stock)
    check('one sale item written', sale and len(sale.items) == 1)
    check('unit price snapshotted', sale and abs(sale.items[0].unit_price - price) < 0.01)
    check('stock movement ledger written',
          StockMovement.query.filter_by(sale_id=sale.id, reason='sale').count() == 1)
    sale_id = sale.id

print('\n--- duplicate submission (same idempotency key) ---')
with app.app_context():
    mid = Product.query.get(pid).stock
r = client.post('/pos/checkout', data={
    'idempotency_key': 'key-cash-1',
    'cart': json.dumps([{'id': pid, 'qty': 2}]),
    'payment_method': 'cash',
}, follow_redirects=False)
with app.app_context():
    check('no second sale created', Sale.query.filter_by(idempotency_key='key-cash-1').count() == 1)
    check('stock deducted only once', Product.query.get(pid).stock == mid, Product.query.get(pid).stock)

print('\n--- client-supplied price is ignored ---')
r = client.post('/pos/checkout', data={
    'idempotency_key': 'key-tamper-1',
    'cart': json.dumps([{'id': pid, 'qty': 1, 'price': 0.01, 'total': 0.01}]),
    'payment_method': 'cash',
}, follow_redirects=False)
with app.app_context():
    tampered = Sale.query.filter_by(idempotency_key='key-tamper-1').first()
    check('tampered price has no effect on the total',
          tampered and abs(tampered.total - price) < 0.01, tampered.total if tampered else None)

print('\n--- insufficient stock / overselling ---')
with app.app_context():
    small = Product.query.filter(Product.status == 'approved').order_by(Product.stock.asc()).first()
    sid, sstock = small.id, small.stock
    print(f'  product #{sid} {small.name!r} stock={sstock}')
if sstock >= 1:
    r = client.post('/pos/checkout', data={
        'idempotency_key': 'key-over-1',
        'cart': json.dumps([{'id': sid, 'qty': sstock + 1}]),
        'payment_method': 'cash',
    }, follow_redirects=False)
    with app.app_context():
        check('oversell is refused', Sale.query.filter_by(idempotency_key='key-over-1').first() is None)
        check('stock untouched after refusal', Product.query.get(sid).stock == sstock,
              Product.query.get(sid).stock)

with app.app_context():
    zero = Product.query.filter(Product.status == 'approved', Product.stock == 0).first()
if zero:
    r = client.post('/pos/checkout', data={
        'idempotency_key': 'key-zero-1',
        'cart': json.dumps([{'id': zero.id, 'qty': 1}]),
        'payment_method': 'cash',
    }, follow_redirects=False)
    with app.app_context():
        check('selling an out-of-stock product is refused',
              Sale.query.filter_by(idempotency_key='key-zero-1').first() is None)

print('\n--- invalid quantities ---')
for label, payload in (('zero quantity', [{'id': 1, 'qty': 0}]),
                       ('negative quantity', [{'id': 1, 'qty': -3}]),
                       ('non-numeric quantity', [{'id': 1, 'qty': 'lots'}]),
                       ('empty cart', []),
                       ('malformed json', None)):
    key = 'key-bad-' + label.replace(' ', '-')
    r = client.post('/pos/checkout', data={
        'idempotency_key': key,
        'cart': 'not json' if payload is None else json.dumps(payload),
        'payment_method': 'cash',
    }, follow_redirects=False)
    with app.app_context():
        check(f'{label} rejected', Sale.query.filter_by(idempotency_key=key).first() is None)

print('\n--- M-Pesa stays pending ---')
r = client.post('/pos/checkout', data={
    'idempotency_key': 'key-mpesa-1',
    'cart': json.dumps([{'id': pid, 'qty': 1}]),
    'payment_method': 'mpesa',
}, follow_redirects=False)
with app.app_context():
    m = Sale.query.filter_by(idempotency_key='key-mpesa-1').first()
    check('M-Pesa sale recorded', m is not None)
    check('M-Pesa is NOT marked paid without confirmation',
          m and m.payment_status == 'pending', m.payment_status if m else None)
    mpesa_id = m.id

r = client.post(f'/pos/sale/{mpesa_id}/payment',
                data={'action': 'confirm', 'payment_reference': 'QK73HS92LM'},
                follow_redirects=False)
with app.app_context():
    m = Sale.query.get(mpesa_id)
    check('cashier cannot confirm their own M-Pesa payment', m.payment_status == 'pending',
          m.payment_status)

login('mgr1')
r = client.post(f'/pos/sale/{mpesa_id}/payment',
                data={'action': 'confirm', 'payment_reference': 'bad!!'},
                follow_redirects=False)
with app.app_context():
    check('a malformed transaction code is rejected',
          Sale.query.get(mpesa_id).payment_status == 'pending')

r = client.post(f'/pos/sale/{mpesa_id}/payment',
                data={'action': 'confirm', 'payment_reference': 'QK73HS92LM'},
                follow_redirects=False)
with app.app_context():
    m = Sale.query.get(mpesa_id)
    check('admin can confirm M-Pesa', m.payment_status == 'confirmed')
    check('who confirmed and when is recorded',
          m.confirmed_by_id is not None and m.confirmed_at is not None)
    check('transaction code stored', m.payment_reference == 'QK73HS92LM')

print('\n--- void is a reversal, not a delete ---')
with app.app_context():
    vbefore = Product.query.get(pid).stock
r = client.post(f'/pos/sale/{sale_id}/void', data={'reason': 'customer changed mind'},
                follow_redirects=False)
with app.app_context():
    s = Sale.query.get(sale_id)
    check('sale row still exists', s is not None)
    check('sale marked voided', s.status == 'voided')
    check('void records who and why', s.voided_by_id is not None and s.void_reason)
    check('stock returned', Product.query.get(pid).stock == vbefore + 2,
          Product.query.get(pid).stock)
    check('items preserved', len(s.items) == 1)
    check('void written to the stock ledger',
          StockMovement.query.filter_by(sale_id=sale_id, reason='void').count() == 1)

print('\n--- receipts and pages ---')
for path in ('/pos', '/pos/sales', f'/pos/sale/{sale_id}/receipt', '/pos/summary'):
    r = client.get(path)
    check(f'GET {path} renders', r.status_code == 200, r.status_code)

with app.app_context():
    sale = Sale.query.get(sale_id)
    lines = sale.receipt_lines()
    check('receipt lines carry name, qty and price',
          lines and all({'name', 'quantity', 'unit_price', 'line_total'} <= set(l) for l in lines))

print(f'\n{ok} passed, {fail} failed')
