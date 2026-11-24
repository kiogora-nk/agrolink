"""Cloudinary image storage.

Why this exists: uploaded pictures used to be stored as bytes inside Postgres
(the ``uploaded_images`` table) and served by the app on every view. Every
product card therefore woke the database and pulled image bytes through it,
which is what pushed the free Neon plan over its quota and took the site down
on 2026-11-24. Cloudinary's free plan stores the files and serves them from
its own CDN, so an image view never touches the app or the database.

Configuration (either form; nothing here is ever sent to the browser except
the public delivery URL):

    CLOUDINARY_URL=cloudinary://<api_key>:<api_secret>@<cloud_name>

or the three separate variables CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY,
CLOUDINARY_API_SECRET. ``CLOUDINARY_FOLDER`` (default ``biofarm``) groups the
uploads in the Cloudinary media library.

When nothing is configured, ``enabled()`` is False and the app keeps its
previous behaviour (database-backed uploads), so local development and tests
need no account.

Uses the plain REST upload API through ``requests`` (already a dependency)
rather than the Cloudinary SDK: a signed upload is a single POST.
"""
import hashlib
import os
import re
import time
from urllib.parse import unquote, urlparse

import requests

UPLOAD_TIMEOUT_SECONDS = 30

# Card grids render around 300px wide; 480 is sharp on a normal phone.
THUMB_WIDTH = 480
MEDIUM_WIDTH = 960
# Matches IMAGE_MAX_DIMENSION in app.py: nothing larger is ever uploaded.
FULL_WIDTH = 1600


class UploadError(Exception):
    """Cloudinary refused or could not be reached."""


def _config():
    """The configured account as a dict, or None when not configured."""
    url = (os.environ.get('CLOUDINARY_URL') or '').strip()
    if url:
        parsed = urlparse(url)
        if (parsed.scheme == 'cloudinary' and parsed.hostname
                and parsed.username and parsed.password):
            return {'cloud_name': parsed.hostname,
                    'api_key': unquote(parsed.username),
                    'api_secret': unquote(parsed.password)}
        return None
    cloud = (os.environ.get('CLOUDINARY_CLOUD_NAME') or '').strip()
    key = (os.environ.get('CLOUDINARY_API_KEY') or '').strip()
    secret = (os.environ.get('CLOUDINARY_API_SECRET') or '').strip()
    if cloud and key and secret:
        return {'cloud_name': cloud, 'api_key': key, 'api_secret': secret}
    return None


def enabled():
    return _config() is not None


def folder():
    return (os.environ.get('CLOUDINARY_FOLDER') or 'biofarm').strip('/ ') or 'biofarm'


def sign(params, api_secret):
    """Cloudinary request signature: SHA-1 of the sorted ``k=v`` pairs joined
    with ``&``, followed by the API secret. ``file``, ``api_key``,
    ``resource_type`` and ``cloud_name`` are never part of it."""
    payload = '&'.join(f'{k}={params[k]}' for k in sorted(params)
                       if params[k] not in (None, ''))
    return hashlib.sha1((payload + api_secret).encode('utf-8')).hexdigest()


def upload(data, filename):
    """Upload image bytes; returns ``{'url', 'width', 'height', 'public_id'}``.

    ``filename`` is the app's random-hex name (``<32 hex>.<ext>``). Its stem
    becomes the public id, so re-uploading the same file (the migration
    script, or a retry) overwrites rather than duplicates, and the delivery
    URL still ends in the original filename.
    """
    cfg = _config()
    if not cfg:
        raise UploadError('Cloudinary is not configured')
    stem = filename.rsplit('.', 1)[0]
    params = {'public_id': f'{folder()}/{stem}',
              'timestamp': str(int(time.time()))}
    params['signature'] = sign(params, cfg['api_secret'])
    params['api_key'] = cfg['api_key']
    endpoint = f"https://api.cloudinary.com/v1_1/{cfg['cloud_name']}/image/upload"
    try:
        resp = requests.post(endpoint, data=params,
                             files={'file': (filename, data)},
                             timeout=UPLOAD_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise UploadError(f'Cloudinary unreachable: {exc}') from exc
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200 or 'secure_url' not in body:
        message = (body.get('error') or {}).get('message') or resp.text[:200]
        raise UploadError(f'Cloudinary upload failed ({resp.status_code}): {message}')
    return {'url': body['secure_url'], 'width': body.get('width'),
            'height': body.get('height'), 'public_id': body.get('public_id')}


_DELIVERY = re.compile(r'^https://res\.cloudinary\.com/([^/]+)/image/upload/(.+)$')


def is_cloudinary_url(value):
    return bool(value) and isinstance(value, str) and bool(_DELIVERY.match(value.strip()))


def variant(url, width):
    """Delivery URL resized to at most ``width`` px, in the best format the
    browser accepts (``f_auto`` = AVIF/WebP where supported) at automatic
    quality.

    ``c_limit`` never enlarges: asking for 1600 of an 800px original returns
    the 800px original. That is what makes the srcset below safe without
    knowing each image's true width.

    Only URLs on *our* cloud are rewritten. A pasted link to somebody else's
    Cloudinary account is returned untouched, since transforming it would
    spend their quota and may be blocked by their settings.
    """
    match = _DELIVERY.match(url.strip()) if isinstance(url, str) else None
    cfg = _config()
    if not match or not cfg or match.group(1) != cfg['cloud_name']:
        return url
    return (f'https://res.cloudinary.com/{match.group(1)}/image/upload/'
            f'c_limit,w_{int(width)},f_auto,q_auto/{match.group(2)}')


def srcset(url):
    """Three widths for responsive <img>; empty when ``url`` is not ours."""
    if variant(url, THUMB_WIDTH) == url:
        return ''
    return ', '.join(f'{variant(url, w)} {w}w'
                     for w in (THUMB_WIDTH, MEDIUM_WIDTH, FULL_WIDTH))
