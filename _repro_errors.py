"""Reproduce reported production errors: add product, homepage freshness, create admin."""
import io
import re

from app import create_app, db
from app import User

app = create_app()
client = app.test_client()

def token(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else ''

def flash_text(html):
    """Return the flashed error message, if any."""
    m = re.search(r'bg-red-100[^>]*>\s*(.*?)\s*<', html, re.S)
    return re.sub(r'\s+', ' ', m.group(1)) if m else ''

def tiny_png():
    """A well-formed 1x1 PNG encoded by Pillow (hand-crafted byte strings
    tend to have broken chunk lengths and fail PIL's verify())."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (1, 1), (200, 100, 50)).save(buf, format='PNG')
    return buf.getvalue()

print('== [1] chief admin login ==')
r = client.get('/login')
t = token(r.get_data(as_text=True))
r = client.post('/login', data={
    'username': 'admin', 'password': 'Admin@2025', 'csrf_token': t,
}, follow_redirects=False)
print('   login:', r.status_code, r.location or '')

print('== [2] add product (admin, with image) ==')
r = client.get('/admin/products/new')
html = r.get_data(as_text=True)
t = token(html)
print('   GET form:', r.status_code, '| csrf token:', bool(t))
data = {
    'name': 'Test Mango KG',
    'description': 'diag product',
    'price': '120',
    'category': 'fruits',
    'stock': '10',
    'status': 'approved',
    'csrf_token': t,
}
data['image'] = (io.BytesIO(tiny_png()), 'mango.png', 'image/png')
r = client.post('/admin/products/new', data=data,
   content_type='multipart/form-data', follow_redirects=False)
print('   POST /admin/products/new:', r.status_code, r.location or '')
if r.status_code not in (301, 302):
    print('   flash:', flash_text(r.get_data(as_text=True)) or '(none)')

print('== [3] homepage freshness ==')
r = client.get('/')
html = r.get_data(as_text=True)
print('   status:', r.status_code, '| test product on homepage:', 'Test Mango KG' in html)

print('== [4] create admin ==')
r = client.get('/chief-admin/create-admin')
t = token(r.get_data(as_text=True))
r = client.post('/chief-admin/create-admin', data={
    'username': 'diag_admin2',
    'email': 'diag_admin2@example.com',
    'phone': '',
    'password': 'Password@123',
    'role': 'admin',
    'csrf_token': t,
}, follow_redirects=False)
print('   POST create-admin:', r.status_code, r.location or '')
if r.status_code not in (301, 302):
    print('   flash:', flash_text(r.get_data(as_text=True)) or '(none)')
with app.app_context():
    u = User.query.filter_by(username='diag_admin2').first()
    print('   admin created:', bool(u), '| role:', getattr(u, 'role', None))
