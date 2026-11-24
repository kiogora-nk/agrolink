"""Order-reliability regression tests.

Runs against a THROWAWAY SQLite database in a temp directory. DATABASE_URL is
set before `app` is imported, and load_dotenv() never overrides an existing
environment variable, so this cannot reach the real Neon database whatever is
in .env. Same isolated-DB trick _bench_routes.py uses. Nothing here reads or
writes production data, and nothing here sends real email (no MAIL_* creds).

    venv/Scripts/python.exe _test_order_reliability.py

Exits non-zero if any check fails, so it is usable as a gate.
"""
import os
import re
import shutil
import sys
import tempfile

TMPDIR = tempfile.mkdtemp(prefix='agrolink-order-tests-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(TMPDIR, 'test.db').replace('\\', '/')
os.environ['DISABLE_SCHEDULER'] = '1'   # never start the report scheduler in tests
os.environ.setdefault('SECRET_KEY', 'test-secret-not-for-production')

# Never send real mail. app.py copies MAIL_* out of the environment at import
# time and .env holds live Gmail credentials, so without this every fabricated
# order below would email the real chief-admin inbox. Setting the keys (even to
# '') before the import also stops load_dotenv() from filling them back in.
for _var in ('MAIL_USERNAME', 'MAIL_PASSWORD', 'MAIL_DEFAULT_SENDER',
             'CHIEF_ADMIN_EMAIL'):
    os.environ[_var] = ''

from sqlalchemy.exc import OperationalError  # noqa: E402

import app as app_module  # noqa: E402
from app import create_app, db, Notification, Order, Product, User  # noqa: E402

application = create_app()          # runs init_db(): schema + seeded admin
application.config['RATELIMIT_ENABLED'] = False
client = application.test_client()

# Hard stop if the app is not on the throwaway database. This file creates,
# updates and deletes rows, so pointing it at a real database by accident would
# be destructive. Fail loudly instead of guessing.
_uri = str(application.config.get('SQLALCHEMY_DATABASE_URI', ''))
if not (_uri.startswith('sqlite:') and TMPDIR.replace('\\', '/') in _uri):
    sys.exit(f'REFUSING TO RUN: app is not on the temporary test database.\n'
             f'  expected a sqlite file under {TMPDIR}\n'
             f'  got {_uri!r}')
print('  isolation check: app is on the throwaway sqlite database')

FAILURES = []


def check(label, condition, detail=''):
    ok = bool(condition)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ''))
    if not ok:
        FAILURES.append(label)
    return ok


def csrf_of(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else ''


def login(username, password):
    client.get('/logout')
    html = client.get('/login').get_data(as_text=True)
    client.post('/login', data={'username': username, 'password': password,
                               'csrf_token': csrf_of(html)}, follow_redirects=True)


def register(username, email):
    html = client.get('/register').get_data(as_text=True)
    client.post('/register', data={
        'username': username, 'email': email, 'phone': '0712 345 678',
        'password': 'Password@123', 'confirm_password': 'Password@123',
        'role': 'buyer', 'csrf_token': csrf_of(html)}, follow_redirects=True)


def make_product(name, price, stock):
    with application.app_context():
        p = Product(name=name, description='test row', price=price,
                    stock=stock, status='approved')
        db.session.add(p)
        db.session.commit()
        return p.id


def stock_of(pid):
    with application.app_context():
        return db.session.get(Product, pid).stock


def orders_of(username):
    with application.app_context():
        u = User.query.filter_by(username=username).first()
        return Order.query.filter_by(user_id=u.id).order_by(Order.id).all() if u else []


def order_form(pid):
    """(csrf, order_token) from a freshly rendered product page."""
    html = client.get(f'/product/{pid}').get_data(as_text=True)
    m = re.search(r'name="order_token"[^>]*value="([^"]+)"', html)
    return csrf_of(html), (m.group(1) if m else None)


def post_order(pid, qty=1, address='Test St 1, Nairobi', phone='0712 345 678',
               token=None, extra=None):
    tok_csrf, form_token = order_form(pid)
    data = {'quantity': qty, 'delivery_address': address, 'delivery_phone': phone,
            'csrf_token': tok_csrf,
            'order_token': form_token if token is None else token}
    if extra:
        data.update(extra)
    return client.post(f'/place-order/{pid}', data=data, follow_redirects=True)


body = lambda r: r.get_data(as_text=True)

# ---------------------------------------------------------------------------
print('\n[setup] isolated database + fixtures')
print('  db =', os.environ['DATABASE_URL'])
print('  sqlite file inside temp dir:', TMPDIR in os.environ['DATABASE_URL'])
register('buyer_one', 'buyer_one@example.com')
register('buyer_two', 'buyer_two@example.com')
pid = make_product('Test Apples', 250.0, 10)
check('buyer registered and product created', len(orders_of('buyer_one')) == 0 and stock_of(pid) == 10)

# ---------------------------------------------------------------------------
print('\n[1] a valid order is saved with a server-computed total')
login('buyer_one', 'Password@123')
r = post_order(pid, qty=3)
orders = orders_of('buyer_one')
check('exactly one order row exists', len(orders) == 1, f'n={len(orders)}')
if orders:
    o = orders[-1]
    check('quantity saved', o.quantity == 3, f'qty={o.quantity}')
    check('total computed server-side (250 x 3)', o.total_price == 750.0, f'total={o.total_price}')
    check('order number assigned', bool(o.order_number), o.order_number or '')
    check('receipt number assigned', bool(o.receipt_number), o.receipt_number or '')
    check('status starts pending', o.status == 'pending', o.status)
    check('delivery details persisted',
          o.delivery_address == 'Test St 1, Nairobi' and o.delivery_phone == '0712 345 678')
check('stock decremented by 3', stock_of(pid) == 7, f'stock={stock_of(pid)}')
check('success message names the order reference',
      'Order placed successfully' in body(r) and (orders[-1].order_number in body(r) if orders else False))
check('order visible on /my-orders', 'Test Apples' in body(client.get('/my-orders')))

# ---------------------------------------------------------------------------
print('\n[2] the order is visible to an admin')
login('admin', 'Admin@2025')
admin_page = body(client.get('/admin/orders'))
check('admin order queue renders', 'Test Apples' in admin_page or 'buyer_one' in admin_page)

# ---------------------------------------------------------------------------
print('\n[3] client-submitted prices are ignored')
login('buyer_one', 'Password@123')
before = len(orders_of('buyer_one'))
post_order(pid, qty=2, extra={'price': '1', 'total_price': '1', 'unit_price': '1'})
after = orders_of('buyer_one')
check('one new order added', len(after) == before + 1)
if len(after) > before:
    check('total still 250 x 2, not the tampered 1', after[-1].total_price == 500.0,
          f'total={after[-1].total_price}')

# ---------------------------------------------------------------------------
print('\n[4] invalid submissions are rejected and write nothing')
base = len(orders_of('buyer_one'))
base_stock = stock_of(pid)

r = post_order(pid, qty=0)
check('quantity 0 rejected', 'at least 1' in body(r) and len(orders_of('buyer_one')) == base)

r = post_order(pid, qty=-5)
check('negative quantity rejected', len(orders_of('buyer_one')) == base)

r = post_order(pid, qty=base_stock + 999)
check('over-stock rejected', 'units available' in body(r) and len(orders_of('buyer_one')) == base)

r = post_order(pid, address='')
check('missing address rejected', 'delivery address' in body(r) and len(orders_of('buyer_one')) == base)

r = post_order(pid, phone='')
check('missing phone rejected', 'delivery phone' in body(r) and len(orders_of('buyer_one')) == base)

r = client.post('/place-order/999999', data={'quantity': 1, 'delivery_address': 'x',
                'delivery_phone': 'y', 'csrf_token': csrf_of(body(client.get(f'/product/{pid}')))},
                follow_redirects=True)
check('unknown product id gives 404', r.status_code == 404, f'status={r.status_code}')
check('nothing was written by any rejected attempt',
      len(orders_of('buyer_one')) == base and stock_of(pid) == base_stock)

# ---------------------------------------------------------------------------
print('\n[5] a replayed submission (double-click) cannot create a second order')
base = len(orders_of('buyer_one'))
_, tok = order_form(pid)
stock_before = stock_of(pid)
post_order(pid, qty=1, token=tok)
first = len(orders_of('buyer_one'))
post_order(pid, qty=1, token=tok)          # identical replay, same token
second = len(orders_of('buyer_one'))
check('the replay added no second order', second == first == base + 1,
      f'{base} -> {first} -> {second}')
check('stock was decremented exactly once', stock_of(pid) == stock_before - 1,
      f'{stock_before} -> {stock_of(pid)}')
with application.app_context():
    dupes = Order.query.filter_by(idempotency_key=tok).count()
check('the database holds exactly one row for that token', dupes == 1, f'n={dupes}')

# ---------------------------------------------------------------------------
print('\n[6] a database failure during commit is reported honestly')
base = len(orders_of('buyer_one'))
stock_before = stock_of(pid)
# Fetch the form and its token BEFORE breaking commit: rendering the product
# page itself writes (it increments a view counter), so it needs a working
# connection. Getting this wrong would make the request fail CSRF instead of
# reaching the path under test.
form_csrf, form_token = order_form(pid)
original_commit = db.session.commit
attempts = {'n': 0}


def failing_commit():
    attempts['n'] += 1
    raise OperationalError('SELECT 1', {}, Exception('simulated connection loss'))


db.session.commit = failing_commit
try:
    # follow_redirects is deliberately False here. The redirect target is the
    # product page, and that page commits too (it bumps a view counter), so
    # following it while commit is still broken would land on the 503 page and
    # the flash under test would never render. The flash is collected on the
    # next request instead, once commit is healthy again.
    r = client.post(f'/place-order/{pid}', data={
        'quantity': 1, 'delivery_address': 'Test St 1, Nairobi',
        'delivery_phone': '0712 345 678', 'csrf_token': form_csrf,
        'order_token': form_token}, follow_redirects=False)
finally:
    db.session.commit = original_commit

check('the simulated failure actually fired', attempts['n'] >= 1, f'commit attempts={attempts["n"]}')
check('the customer is redirected rather than shown a result',
      r.status_code == 302, f'status={r.status_code}')
shown = body(client.get(r.headers.get('Location', f'/product/{pid}')))
check('no false success message', 'Order placed successfully' not in shown)
check('a useful error is shown', 'could not save your order' in shown)
check('no order row was left behind', len(orders_of('buyer_one')) == base)
check('stock reservation was rolled back', stock_of(pid) == stock_before,
      f'{stock_before} -> {stock_of(pid)}')

# ---------------------------------------------------------------------------
print('\n[7] a notification failure does not lose the saved order')
base = len(orders_of('buyer_one'))
original_notify = app_module._notify_new_order
app_module._notify_new_order = lambda order: (_ for _ in ()).throw(RuntimeError('simulated SMTP/DB blowup'))
try:
    r = post_order(pid, qty=1, token='tok-notify-failure')
finally:
    app_module._notify_new_order = original_notify
after = orders_of('buyer_one')
check('the order is still persisted', len(after) == base + 1, f'n={len(after)}')
check('the customer still sees success, not an error page',
      'Order placed successfully' in body(r))
check('status code is not a 500', r.status_code == 200, f'status={r.status_code}')

# ---------------------------------------------------------------------------
print('\n[8] the last unit cannot be sold twice')
one = make_product('Last Unit Only', 100.0, 1)
_, t1 = order_form(one)
post_order(one, qty=1, token=t1)
r2 = post_order(one, qty=1, token=t1 + '-different')
check('the second order for the last unit is refused', 'units available' in body(r2) or 'out of stock' in body(r2))
check('stock never went negative', stock_of(one) == 0, f'stock={stock_of(one)}')
with application.app_context():
    n = Order.query.filter_by(product_id=one).count()
check('exactly one order exists for that product', n == 1, f'n={n}')

# ---------------------------------------------------------------------------
print('\n[9] customer orders carry no payment state to manipulate')
check('Order has no payment column',
      not any('payment' in c.name for c in Order.__table__.columns),
      ', '.join(c.name for c in Order.__table__.columns))
check('payment lives only on the POS Sale model',
      'payment_status' in app_module.Sale.__table__.columns)

# ---------------------------------------------------------------------------
print('\n[10] guest ordering is refused')
client.get('/logout')
with application.app_context():
    orders_before = Order.query.count()
# A guest's product page has no order form, so there is no CSRF token to
# send: the request is refused with a 400 before login_required even runs.
# Either refusal is correct; what matters is that nothing was saved.
anon = post_order(pid, qty=1, token='tok-anon')
with application.app_context():
    anon_saved = Order.query.filter_by(idempotency_key='tok-anon').count()
    orders_after = Order.query.count()
check('anonymous POST does not create an order',
      anon_saved == 0 and orders_after == orders_before
      and ('login' in body(anon).lower() or anon.status_code in (302, 400, 401)),
      f'status={anon.status_code} saved={anon_saved}')

# ---------------------------------------------------------------------------
print('\n[11] one customer cannot see another customer\'s orders')
login('buyer_two', 'Password@123')
page = body(client.get('/my-orders'))
check('buyer_two does not see buyer_one\'s orders', 'Test Apples' not in page)

# ---------------------------------------------------------------------------
print('\n[12] order status can only be changed by an admin')
orders = orders_of('buyer_one')
target = orders[-1]
html = body(client.get('/my-orders'))
r = client.post(f'/admin/order/{target.id}/status',
                data={'status': 'confirmed', 'csrf_token': csrf_of(html)},
                follow_redirects=False)
with application.app_context():
    still = db.session.get(Order, target.id).status
check('a buyer cannot change an order status', still == 'pending', f'status now={still}')

login('admin', 'Admin@2025')
html = body(client.get('/admin/orders'))
r = client.post(f'/admin/order/{target.id}/status',
                data={'status': 'confirmed', 'csrf_token': csrf_of(html)},
                follow_redirects=True)
with application.app_context():
    changed = db.session.get(Order, target.id).status
check('an admin can change the status', changed == 'confirmed', f'status now={changed}')

html = body(client.get('/admin/orders'))
r = client.post(f'/admin/order/{target.id}/status',
                data={'status': 'nonsense-status', 'csrf_token': csrf_of(html)},
                follow_redirects=True)
with application.app_context():
    unchanged = db.session.get(Order, target.id).status
check('an invalid status value is rejected', unchanged == 'confirmed', f'status now={unchanged}')

# ---------------------------------------------------------------------------
print('\n[13] the homepage survives optional-content failure')
anon_client = application.test_client()
check('homepage renders normally', anon_client.get('/').status_code == 200)


class _ExplodingQuery:
    """Stands in for Review.query and fails the way a dead connection would."""
    def options(self, *args, **kwargs):
        raise OperationalError('SELECT reviews', {}, Exception('simulated'))


class _ExplodingReview:
    query = _ExplodingQuery()


original_review = app_module.Review
app_module.Review = _ExplodingReview          # the module global home() reads
try:
    homepage_status = anon_client.get('/').status_code
finally:
    app_module.Review = original_review
check('homepage still returns 200 when the reviews table is unreadable',
      homepage_status == 200, f'status={homepage_status}')
check('homepage renders again normally afterwards',
      anon_client.get('/').status_code == 200)

# ---------------------------------------------------------------------------
print('\n[14] a dropped connection answers 503 rather than a generic crash')
anon_client2 = application.test_client()
original_commit = db.session.commit
db.session.commit = failing_commit   # raises OperationalError on the view counter
try:
    r = anon_client2.get(f'/product/{pid}')
    status = r.status_code
finally:
    db.session.commit = original_commit
check('database disconnect yields 503', status == 503, f'status={status}')

# ---------------------------------------------------------------------------
print('\n[15] the app is still usable after that simulated disconnect')
check('a normal request succeeds afterwards',
      application.test_client().get('/').status_code == 200)
check('admin pages still load', client.get('/admin/orders').status_code == 200)

# ---------------------------------------------------------------------------
print('\n' + '=' * 66)
if FAILURES:
    print(f'FAILED: {len(FAILURES)} check(s)')
    for f in FAILURES:
        print('  -', f)
else:
    print('ALL CHECKS PASSED')

shutil.rmtree(TMPDIR, ignore_errors=True)
sys.exit(1 if FAILURES else 0)
