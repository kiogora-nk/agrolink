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
png = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
       b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00'
       b'\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82')
data = {
    'name': 'Test Mango KG',
    'description': 'diag product',
    'price': '120',
    'category': 'fruits',
    'stock': '10',
    'status': 'approved',
    'csrf_token': t,
}
data['image'] = (io.BytesIO(png), 'mango.png', 'image/png')
r = client.post('/admin/products/new', data=data,
   content_type='multipart/form-data', follow_redirects=False)
print('   POST /admin/products/new:', r.status_code, r.location or '')
if r.status_code not in (301, 302):
    print(r.get_data(as_text=True)[:1200])

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
    print(r.get_data(as_text=True)[:1200])
u = User.query.filter_by(username='diag_admin2').first()
print('   admin created:', bool(u), '| role:', getattr(u, 'role', None))

db.session.close()
