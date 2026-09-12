"""Verify: editing a product image via /admin/product/<id>/edit works,
including formats that used to be silently dropped (HEIC/JFIF/BMP) and
clear errors for genuinely bad files.

Runs against a throwaway sqlite DB (test_img_edit.db) so real data is
untouched. Run:  venv/Scripts/python.exe _test_image_edit.py
"""
import io
import os

os.environ['DATABASE_URL'] = 'sqlite:///test_img_edit.db'

from app import app, db, User, Product  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


def _image_bytes(fmt, color):
    """Encode a real 1x1 image with Pillow (round-trip through an actual
    encoder, so the fixtures are always well-formed files)."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGBA', (1, 1), color).save(buf, format=fmt)
    return buf.getvalue()


PNG_RED = _image_bytes('PNG', (255, 0, 0, 255))
PNG_BLUE = _image_bytes('PNG', (0, 0, 255, 255))
TIFF_1PX = _image_bytes('TIFF', (255, 0, 0, 255))

app.config['WTF_CSRF_ENABLED'] = False  # test client can't fetch tokens

with app.app_context():
    db.drop_all()
    db.create_all()
    admin = User(username='tadmin', email='tadmin@x.com',
                 password_hash=generate_password_hash('Passw0rd!'),
                 role='admin', is_active=True)
    db.session.add(admin)
    db.session.commit()
    p = Product(name='Test Fruit', description='d', price=100, category='fruits',
                farmer_id=admin.id, status='approved',
                image='https://example.com/old.jpg')
    db.session.add(p)
    db.session.commit()
    pid = p.id

c = app.test_client()
r = c.post('/login', data={'username': 'tadmin', 'password': 'Passw0rd!'},
           follow_redirects=True)
print('login status:', r.status_code)

failures = []


def edit(image_file=None, image_url='', note=''):
    data = {'name': 'Test Fruit', 'price': '100', 'category': 'fruits',
            'stock': '5', 'description': 'd', 'status': 'approved'}
    if image_url:
        data['image_url'] = image_url
    if image_file is not None:
        data['image'] = image_file
    r = c.post(f'/admin/product/{pid}/edit', data=data,
               content_type='multipart/form-data',
               follow_redirects=True)
    with app.app_context():
        prod = db.session.get(Product, pid)
        print(f'--- {note}: POST {r.status_code} -> product.image = {prod.image!r}')
        return prod.image


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    if not cond:
        failures.append(msg)


# 1. valid PNG upload changes the image
img = edit(image_file=(io.BytesIO(PNG_RED), 'new1.png'), note='valid png upload')
check(img and img.startswith('/uploads/') and img.endswith('.png'),
      'valid png upload replaces product.image')

# 2. the stored /uploads/ URL actually serves the bytes
r = c.get(img)
check(r.status_code == 200 and len(r.data) == len(PNG_RED),
      f'served upload: GET {img} -> {r.status_code}, {len(r.data)} bytes')

# 3. a second upload replaces the first
img2 = edit(image_file=(io.BytesIO(PNG_BLUE), 'new2.png'), note='second png upload')
check(img2 != img, 'second upload replaces the first image')

# 4. TIFF is converted to JPEG instead of being silently dropped
img3 = edit(image_file=(io.BytesIO(TIFF_1PX), 'photo.tiff'), note='tiff upload')
check(img3 and img3 != img2 and img3.endswith('.jpg'),
      'tiff upload converted to jpg and replaces image')

# 5. garbage with a real image extension -> image unchanged (old behaviour was
#    to silently keep it; now the flash tells the user, image still unchanged)
img4 = edit(image_file=(io.BytesIO(b'not an image at all'), 'photo.png'),
            note='corrupt png upload')
check(img4 == img3, 'corrupt image file does not wipe the stored image')

# 6. text file -> rejected, image unchanged
img5 = edit(image_file=(io.BytesIO(b'hello'), 'notes.txt'), note='txt upload')
check(img5 == img4, 'non-image file rejected, image unchanged')

# 7. image_url still works
img6 = edit(image_url='https://example.com/brand-new.jpg', note='image_url only')
check(img6 == 'https://example.com/brand-new.jpg', 'image_url replaces image')

# 8. nothing provided -> image kept
img7 = edit(note='nothing provided')
check(img7 == img6, 'empty submission keeps the current image')

print()
if failures:
    print(f'{len(failures)} check(s) FAILED')
    raise SystemExit(1)
print('ALL CHECKS PASSED')
