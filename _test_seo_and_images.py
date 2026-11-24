"""SEO, Cloudinary image storage and database-outage regression tests.

Runs against a THROWAWAY SQLite database in a temp directory (DATABASE_URL is
set before `app` is imported, and load_dotenv() never overrides it), sends no
email, and never talks to Cloudinary: requests.post is replaced by a stub.

    .venv\\Scripts\\python.exe _test_seo_and_images.py

Exits non-zero if any check fails.
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

TMPDIR = tempfile.mkdtemp(prefix='agrolink-seo-tests-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(TMPDIR, 'test.db').replace('\\', '/')
os.environ['DISABLE_SCHEDULER'] = '1'
os.environ['SITE_URL'] = 'https://biofarmfruits.example'
os.environ.setdefault('SECRET_KEY', 'test-secret-not-for-production')
for _var in ('MAIL_USERNAME', 'MAIL_PASSWORD', 'MAIL_DEFAULT_SENDER', 'CHIEF_ADMIN_EMAIL',
             'CLOUDINARY_URL', 'CLOUDINARY_CLOUD_NAME', 'CLOUDINARY_API_KEY',
             'CLOUDINARY_API_SECRET', 'GOOGLE_SITE_VERIFICATION', 'CF_BEACON_TOKEN'):
    os.environ[_var] = ''

import requests  # noqa: E402
from werkzeug.datastructures import FileStorage  # noqa: E402

import app as app_module  # noqa: E402
import image_storage  # noqa: E402
from app import BlogPost, Product, UploadedImage, create_app, db  # noqa: E402

application = create_app()
application.config['RATELIMIT_ENABLED'] = False
client = application.test_client()
FAILED = []


def check(label, ok, detail=''):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f'   [{detail}]' if detail and not ok else ''))
    if not ok:
        FAILED.append(label)


def head_of(html):
    title = re.search(r'<title>(.*?)</title>', html, re.S)
    desc = re.search(r'<meta name="description" content="([^"]*)"', html)
    canon = re.search(r'<link rel="canonical" href="([^"]*)"', html)
    robots = re.search(r'<meta name="robots" content="([^"]*)"', html)
    ld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return {'title': title.group(1).strip() if title else '',
            'desc': desc.group(1) if desc else '',
            'canonical': canon.group(1) if canon else '',
            'robots': robots.group(1) if robots else '', 'ld': ld}


def png_bytes():
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (900, 600), (200, 30, 80)).save(out, format='PNG')
    return out.getvalue()


# ---------------------------------------------------------------------------
print('\n[setup] fixtures')
with application.app_context():
    live = Product(name='Red Dragon Fruit', description='Sweet <b>red-fleshed</b> dragon fruit, '
                   'harvested ripe.', price=450, stock=20, status='approved', category='fruits',
                   image='https://res.cloudinary.com/othercloud/image/upload/v1/x.jpg')
    hidden = Product(name='Unreviewed Item', description='pending', price=10, stock=1,
                     status='pending')
    post = BlogPost(title='How to grow dragon fruit', slug='grow-dragon-fruit',
                    body='<p>Plant cuttings in well-drained soil.</p>', published=True)
    draft = BlogPost(title='Draft', slug='draft-post', body='x', published=False)
    db.session.add_all([live, hidden, post, draft])
    db.session.commit()
    live_id, hidden_id = live.id, hidden.id

# ---------------------------------------------------------------------------
print('\n[1] public pages: unique titles and descriptions, canonical, no doubled brand')
public = ['/', '/products', '/products?category=fruits', f'/product/{live_id}', '/blog',
          '/blog/grow-dragon-fruit', '/training', '/contact', '/climate', '/disease-detection']
heads = {}
for path in public:
    r = client.get(path)
    heads[path] = h = head_of(r.get_data(as_text=True))
    check(f'{path} returns 200', r.status_code == 200, r.status_code)
    check(f'{path} has a description', 40 <= len(h['desc']) <= 170, f"{len(h['desc'])}: {h['desc']}")
    check(f'{path} brand appears once in title', h['title'].count('BioFarm Fruits') == 1, h['title'])
    check(f'{path} canonical is absolute on SITE_URL',
          h['canonical'].startswith('https://biofarmfruits.example/'), h['canonical'])
    check(f'{path} is indexable', not h['robots'] and 'X-Robots-Tag' not in r.headers)
titles = [h['title'] for h in heads.values()]
descs = [h['desc'] for h in heads.values()]
check('titles are unique', len(set(titles)) == len(titles), titles)
check('descriptions are unique', len(set(descs)) == len(descs))
check('search/tracking params dropped from canonical',
      head_of(client.get('/products?category=fruits&utm_source=x&page=1').get_data(as_text=True))
      ['canonical'] == 'https://biofarmfruits.example/products?category=fruits')

# ---------------------------------------------------------------------------
print('\n[2] structured data is valid JSON with honest content')
for path, h in heads.items():
    for block in h['ld']:
        try:
            json.loads(block)
            ok = True
        except ValueError as exc:
            ok, block = False, str(exc)
        check(f'{path} JSON-LD parses', ok, block[:120])
home_ld = json.loads(heads['/']['ld'][0])
check('homepage has Organization + WebSite',
      {n['@type'] for n in home_ld['@graph']} >= {'Organization', 'WebSite'})
prod_ld = json.loads(heads[f'/product/{live_id}']['ld'][-1])
product_node = next(n for n in prod_ld['@graph'] if n['@type'] == 'Product')
check('Product offer priced in KES', product_node['offers']['priceCurrency'] == 'KES'
      and product_node['offers']['price'] == '450.00')
check('Product in stock', product_node['offers']['availability'].endswith('InStock'))
check('Product carries no rating/review markup',
      'aggregateRating' not in product_node and 'review' not in product_node)
check('Product description has HTML stripped', '<b>' not in product_node['description'])
check('BreadcrumbList present', any(n['@type'] == 'BreadcrumbList' for n in prod_ld['@graph']))
blog_ld = json.loads(heads['/blog/grow-dragon-fruit']['ld'][-1])
check('BlogPosting present', any(n['@type'] == 'BlogPosting' for n in blog_ld['@graph']))
check('product page has og:image', 'og:image' in client.get(f'/product/{live_id}').get_data(as_text=True))

# ---------------------------------------------------------------------------
print('\n[3] private pages are kept out of the index')
for path in ['/login', '/register', '/forgot-password']:
    r = client.get(path)
    h = head_of(r.get_data(as_text=True))
    check(f'{path} noindex (meta + header)', 'noindex' in h['robots']
          and 'noindex' in r.headers.get('X-Robots-Tag', ''))
    check(f'{path} has no canonical', not h['canonical'])
r = client.get('/products?search=dragon')
check('search results noindex', 'noindex' in r.headers.get('X-Robots-Tag', ''))
r = client.get('/admin/dashboard')
check('admin answers with noindex header even on redirect',
      'noindex' in r.headers.get('X-Robots-Tag', ''))

# ---------------------------------------------------------------------------
print('\n[4] robots.txt and sitemap.xml')
r = client.get('/robots.txt')
txt = r.get_data(as_text=True)
check('robots.txt is text/plain', r.mimetype == 'text/plain')
check('robots.txt blocks admin and pos', 'Disallow: /admin' in txt and 'Disallow: /pos' in txt)
check('robots.txt does not block products', 'Disallow: /products' not in txt
      and 'Disallow: /product\n' not in txt)
check('robots.txt points to sitemap', 'Sitemap: https://biofarmfruits.example/sitemap.xml' in txt)
r = client.get('/sitemap.xml')
check('sitemap is XML', r.mimetype == 'application/xml')
ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
locs = [e.text for e in ET.fromstring(r.data).findall('s:url/s:loc', ns)]
check('sitemap lists approved product', f'https://biofarmfruits.example/product/{live_id}' in locs)
check('sitemap omits unapproved product', f'https://biofarmfruits.example/product/{hidden_id}' not in locs)
check('sitemap lists published post only', any(l.endswith('/blog/grow-dragon-fruit') for l in locs)
      and not any(l.endswith('/blog/draft-post') for l in locs))
check('every sitemap URL returns 200',
      all(client.get(urlparse(l).path).status_code == 200 for l in locs),
      [l for l in locs if client.get(urlparse(l).path).status_code != 200])

# ---------------------------------------------------------------------------
print('\n[5] Cloudinary storage')
check('signature matches Cloudinary documentation example', image_storage.sign(
    {'eager': 'w_400,h_300,c_pad|w_260,h_200,c_crop', 'public_id': 'sample_image',
     'timestamp': '1315060510'}, 'abcd') == 'bfd09f95f331f558cbd1320e67aa8d488770583e')
check('disabled without credentials', not image_storage.enabled())

os.environ['CLOUDINARY_URL'] = 'cloudinary://123456:s3cret@democloud'
calls = []
real_post = requests.post


class FakeResp:
    status_code = 200

    def __init__(self, public_id):
        self._public_id = public_id

    def json(self):
        return {'secure_url': f'https://res.cloudinary.com/democloud/image/upload/v17/{self._public_id}.jpg',
                'width': 900, 'height': 600, 'public_id': self._public_id}


def fake_post(url, data=None, files=None, timeout=None):
    calls.append({'url': url, 'data': dict(data), 'file': files['file'][1]})
    return FakeResp(data['public_id'])


requests.post = fake_post
with application.test_request_context():
    before = UploadedImage.query.count()
    saved, err = app_module.save_uploaded_image(
        FileStorage(io.BytesIO(png_bytes()), filename='photo.png', content_type='image/png'))
    check('upload succeeds', err is None and saved is not None, err)
    check('stored reference is the Cloudinary URL',
          saved.ref.startswith('https://res.cloudinary.com/democloud/image/upload/'), saved.ref)
    check('no image bytes written to the database', UploadedImage.query.count() == before)
    check('local copy kept for the disease detector', os.path.isfile(saved.local_path))
    sent = calls[-1]['data']
    expected_sig = image_storage.sign({'public_id': sent['public_id'], 'timestamp': sent['timestamp']},
                                      's3cret')
    check('request is signed correctly', sent['signature'] == expected_sig)
    check('API secret is never sent', 's3cret' not in json.dumps(sent))
    check('public id is in the biofarm folder', sent['public_id'].startswith('biofarm/'))
    thumb = app_module.image_src(saved.ref, size='thumb')
    check('thumbnail is a resized AVIF/WebP variant', '/upload/c_limit,w_480,f_auto,q_auto/' in thumb, thumb)
    srcset = app_module.image_srcset(saved.ref)
    check('srcset offers 480/960/1600', all(f' {w}w' in srcset for w in (480, 960, 1600)), srcset)
    other = 'https://res.cloudinary.com/othercloud/image/upload/v1/x.jpg'
    check("another account's Cloudinary URL is left untouched",
          app_module.image_src(other, size='thumb') == other)

    def failing_post(*_a, **_k):
        raise requests.ConnectionError('offline')
    requests.post = failing_post
    saved2, err2 = app_module.save_uploaded_image(
        FileStorage(io.BytesIO(png_bytes()), filename='photo2.png', content_type='image/png'))
    check('Cloudinary outage falls back to database storage',
          err2 is None and saved2.ref.startswith('/uploads/')
          and UploadedImage.query.filter_by(filename=saved2.filename).count() == 1, err2)

# ---------------------------------------------------------------------------
print('\n[6] migration script moves DB images and rewrites references (repeatable)')
requests.post = fake_post
with application.app_context():
    target = db.session.get(Product, live_id)
    target.image = saved2.ref
    db.session.commit()
import migrate_images_to_cloudinary as mig  # noqa: E402
sys.argv = ['migrate', '--apply']
mig.main()
with application.app_context():
    migrated = db.session.get(Product, live_id).image
check('product now points at Cloudinary', migrated.startswith('https://res.cloudinary.com/democloud/'),
      migrated)
uploads_before = len(calls)
mig.main()
check('second run uploads nothing new', len(calls) == uploads_before)
with application.app_context():
    check('DB bytes kept until --purge-db-bytes',
          UploadedImage.query.filter_by(filename=saved2.filename).count() == 1)
sys.argv = ['migrate', '--apply', '--purge-db-bytes']
mig.main()
with application.app_context():
    check('--purge-db-bytes removes migrated bytes',
          UploadedImage.query.filter_by(filename=saved2.filename).count() == 0)
r = client.get(f'/product/{live_id}')
check('product page renders the migrated image', 'res.cloudinary.com/democloud' in r.get_data(as_text=True))
requests.post = real_post
os.environ['CLOUDINARY_URL'] = ''

# ---------------------------------------------------------------------------
print('\n[7] database unreachable: the site boots and degrades instead of crashing')
probe = r'''
import os, sys
sys.path.insert(0, %r)
os.environ["DATABASE_URL"] = "postgresql+psycopg://u:p@127.0.0.1:1/x?connect_timeout=1"
os.environ["DISABLE_SCHEDULER"] = "1"
for v in ("MAIL_USERNAME", "MAIL_PASSWORD"): os.environ[v] = ""
import app as m
c = m.create_app().test_client()
for p in ("/", "/products", "/login", "/training"):
    r = c.get(p)
    print(p, r.status_code, r.headers.get("Retry-After"), "back shortly" in r.get_data(as_text=True))
''' % os.path.dirname(os.path.abspath(__file__))
out = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, timeout=120)
rows = {line.split()[0]: line.split()[1:] for line in out.stdout.splitlines() if line.startswith('/')}
check('app boots with the database down', len(rows) == 4, out.stderr[-400:])
check('homepage answers 503 with Retry-After and the friendly page',
      rows.get('/') == ['503', '120', 'True'], rows.get('/'))
check('products answers 503', rows.get('/products', [''])[0] == '503')
check('login page still works', rows.get('/login', [''])[0] == '200')
check('training page still works', rows.get('/training', [''])[0] == '200')

# ---------------------------------------------------------------------------
print('\n' + '=' * 66)
if FAILED:
    print(f'FAILED: {len(FAILED)} check(s)')
    for label in FAILED:
        print('  -', label)
    sys.exit(1)
print('ALL CHECKS PASSED')
