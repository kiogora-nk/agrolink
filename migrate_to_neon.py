#!/usr/bin/env python3
"""Copy all data from the current database into a Neon Postgres database.

Usage:
  1. Create a project at https://neon.tech and copy the *pooled* connection
     string (the host contains "-pooler"). It looks like:
       postgresql://user:password@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require
  2. Run:
       set NEON_DATABASE_URL=postgresql://...   (target - required)
       set SOURCE_DATABASE_URL=postgresql://... (source - optional)
       python migrate_to_neon.py

  SOURCE_DATABASE_URL defaults to DATABASE_URL from the environment/.env
  (i.e. whatever the app currently uses, e.g. the Render Postgres URL or
  the local sqlite file).

The script creates the schema on Neon (via the app's models) and copies
every table's rows in foreign-key-safe order, preserving IDs. It never
writes to the source database. Re-running it is safe only on an empty
target; use --truncate to clear Neon tables first.
"""
import os
import sys

import sqlalchemy as sa

try:  # pick up NEON_DATABASE_URL / DATABASE_URL from .env if set there
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- resolve source & target before importing app (app reads DATABASE_URL) ---
NEON_URL = os.environ.get('NEON_DATABASE_URL', '').strip()
if not NEON_URL:
    sys.exit('Set NEON_DATABASE_URL to your Neon connection string first '
             '(see the usage note at the top of this file).')

SOURCE_URL = os.environ.get('SOURCE_DATABASE_URL', '').strip()
if not SOURCE_URL:
    SOURCE_URL = os.environ.get('DATABASE_URL', 'sqlite:///instance/biofarm.db')
if SOURCE_URL.startswith('postgres://'):
    SOURCE_URL = SOURCE_URL.replace('postgres://', 'postgresql://', 1)
if SOURCE_URL.startswith('sqlite:///') and not SOURCE_URL.startswith('sqlite:////'):
    # Flask-SQLAlchemy resolves relative sqlite paths against instance/;
    # plain SQLAlchemy does not, so point at the same file explicitly.
    rel = SOURCE_URL[len('sqlite:///'):]
    if not os.path.exists(rel) and os.path.exists(os.path.join('instance', rel)):
        SOURCE_URL = 'sqlite:///' + os.path.join('instance', rel)

os.environ['DATABASE_URL'] = SOURCE_URL  # keep the app on the source DB

from app import app, db  # noqa: E402

TRUNCATE = '--truncate' in sys.argv


def main():
    print(f'Source: {_mask(SOURCE_URL)}')
    print(f'Target: {_mask(NEON_URL)}')

    src_engine = sa.create_engine(SOURCE_URL)
    dst_engine = sa.create_engine(NEON_URL, connect_args={'connect_timeout': 15})

    src_meta = sa.MetaData()
    src_meta.reflect(bind=src_engine)

    with app.app_context():
        # Create every model table on Neon (idempotent).
        db.metadata.create_all(bind=dst_engine)

        copied, skipped = {}, []
        for table in db.metadata.sorted_tables:  # FK-safe topological order
            name = table.name
            if name not in src_meta.tables:
                skipped.append(name)
                continue
            src_table = src_meta.tables[name]
            common = [c.name for c in table.columns if c.name in src_table.columns]

            with dst_engine.begin() as dst:
                if TRUNCATE:
                    dst.execute(sa.text(f'TRUNCATE TABLE "{name}" CASCADE'))
                rows = []
                with src_engine.connect() as src:
                    result = src.execute(sa.select(*(src_table.c[c] for c in common)))
                    for row in result.mappings():
                        rows.append({c: row[c] for c in common})
                for i in range(0, len(rows), 500):
                    dst.execute(table.insert(), rows[i:i + 500])
            copied[name] = len(rows)
            print(f'  {name}: {len(rows)} row(s) copied')

        if skipped:
            print(f'Tables with no source counterpart (left empty): {", ".join(skipped)}')
        print(f'\nDone. {sum(copied.values())} row(s) total now on Neon.')

    # Quick verification pass: row counts source vs target.
    print('\nVerification (source -> neon):')
    with src_engine.connect() as s, dst_engine.connect() as d:
        ok = True
        for name, n in copied.items():
            dst_n = d.execute(sa.text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
            mark = 'OK' if dst_n == n else 'MISMATCH'
            if dst_n != n:
                ok = False
            print(f'  {name}: {n} -> {dst_n}  {mark}')
    if not ok:
        sys.exit('Some counts did not match - check the rows above.')
    print('\nAll counts match. Next steps:')
    print('  1. Set DATABASE_URL on Render (and in your local .env) to the Neon URL.')
    print('  2. Deploy/restart the app - init_db() will sync anything still missing.')


def _mask(url):
    """Hide the password when printing connection strings."""
    if '://' not in url:
        return url
    head, rest = url.split('://', 1)
    if '@' not in rest:
        return url
    creds, host = rest.rsplit('@', 1)
    user = creds.split(':', 1)[0]
    return f'{head}://{user}:***@{host}'


if __name__ == '__main__':
    main()
