"""Render the homepage and show the featured section's markup + image URLs."""
import re

import app as appmod
from app import Product, db

a = appmod.app
with a.app_context():
    feats = (Product.query.filter_by(status='approved', featured=True)
             .order_by(Product.created_at.desc()).limit(8).all())
    print('featured products:', [(p.id, p.name, p.image) for p in feats])

c = a.test_client()
r = c.get('/')
html = r.get_data(as_text=True)
print('status', r.status_code, 'len', len(html))

i = html.find('Handpicked for You')
print('\n=== FEATURED SECTION HTML ===')
print(html[i - 400:i + 2600])
