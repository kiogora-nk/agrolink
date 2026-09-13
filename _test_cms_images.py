"""Verify hero image uploads via /admin/cms render on the homepage."""
import io
import re

from app import create_app
from app import SiteContent

app = create_app()
client = app.test_client()

def token(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else ''

def tiny_png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (1, 1), (200, 100, 50)).save(buf, format='PNG')
    return buf.getvalue()

r = client.get('/login')
t = token(r.get_data(as_text=True))
client.post('/login', data={'username': 'admin', 'password': 'Admin@2025',
                            'csrf_token': t})

print('== GET /admin/cms ==')
r = client.get('/admin/cms')
html = r.get_data(as_text=True)
print('status:', r.status_code,
      '| image fields:', html.count('type="file"'),
      '| previews:', html.count('h-16 w-16'),
      '| enctype:', 'multipart/form-data' in html)

print('== upload hero image 1 ==')
t = token(html)
data = {
    'hero_tagline': 'Welcome to BioFarm Fruits',
    'hero_title': 'Quality Dragon Fruits & Seedlings',
    'hero_subtitle': 'We offer premium dragon fruits, avocado seedlings, and expert farming training.',
    'about_title': 'About BioFarm Fruits',
    'about_body': 'BioFarm Fruits supplies quality dragon fruits, avocado seedlings, and hands-on farming training across Kenya.',
    'contact_note': 'Reach out to us directly through phone, WhatsApp, or social media.',
    'csrf_token': t,
    'hero_image_1': (io.BytesIO(tiny_png()), 'hero.png', 'image/png'),
}
r = client.post('/admin/cms', data=data,
                content_type='multipart/form-data', follow_redirects=False)
print('POST:', r.status_code, r.location or '')

with app.app_context():
    row = SiteContent.query.filter_by(key='hero_image_1').first()
    print('stored value:', row.value if row else None)
    stored = row.value if row else ''

print('== homepage uses new image ==')
r = client.get('/')
html = r.get_data(as_text=True)
ok = f'/uploads/{stored}' in html
print('status:', r.status_code, '| new image on homepage:', ok)
print('unsplash fallback count (should be 3):',
      html.count('images.unsplash.com'))

if not ok:
    i = html.find('min-h-48')
    print('hero block:', re.sub(r'\s+', ' ', html[i - 60:i + 400]))
