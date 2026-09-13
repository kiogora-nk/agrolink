"""Diagnose why POST /admin/products/new re-renders the form instead of redirecting."""
import io
import re

from app import create_app

app = create_app()
client = app.test_client()


def token(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else ''


r = client.get('/login')
t = token(r.get_data(as_text=True))
client.post('/login', data={'username': 'admin', 'password': 'Admin@2025',
                            'csrf_token': t})

r = client.get('/admin/products/new')
t = token(r.get_data(as_text=True))
png = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
       b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00'
       b'\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82')
data = {
    'name': 'Test Mango KG', 'description': 'diag product', 'price': '120',
    'category': 'fruits', 'stock': '10', 'status': 'approved', 'csrf_token': t,
}
data['image'] = (io.BytesIO(png), 'mango.png', 'image/png')
r = client.post('/admin/products/new', data=data,
                content_type='multipart/form-data', follow_redirects=False)
print('status:', r.status_code, r.location or '')
html = r.get_data(as_text=True)
i = html.find('bg-red-100')
print('error block:', re.sub(r'\s+', ' ', html[i - 30:i + 220]) if i != -1 else '(none)')
