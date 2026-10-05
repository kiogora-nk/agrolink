#!/usr/bin/env python3
"""Copy AgroLink records from Neon Postgres into MongoDB Atlas.

This is a *copy*, never a move: Neon stays the source of truth and nothing is
ever written, updated or deleted on the Postgres side. The migration is a
snapshot that can be re-run at any time - documents are upserted by their
Postgres primary key, so running it twice updates rather than duplicates.

Usage:
  1. Put your Atlas connection string in .env (see .env.example):
       MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority
       MONGODB_DB=agrolink
  2. Check the connection first:
       venv\\Scripts\\python.exe test_mongodb.py
  3. Preview what would be copied (writes nothing):
       venv\\Scripts\\python.exe migrate_to_mongodb.py --dry-run
  4. Run it:
       venv\\Scripts\\python.exe migrate_to_mongodb.py

Options:
  --dry-run          Read Neon and report counts without writing to MongoDB.
  --tables a,b,c     Only migrate these tables (default: all public tables).
  --verify-only      Skip the copy and just compare Neon counts to MongoDB.

If the password in MONGODB_URI contains @ : / ? # & % or a space, it must be
percent-encoded or Atlas rejects the connection with "bad auth".
"""
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa

try:  # pymongo is optional so --dry-run works before Atlas is configured
    from pymongo import MongoClient, ReplaceOne
except ImportError:  # reported clearly in _mongo_db()
    MongoClient = ReplaceOne = None

try:  # pick up DATABASE_URL / MONGODB_URI from .env if set there
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BATCH = 500


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

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


def _pg_url():
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        sys.exit('DATABASE_URL is not set - point it at your Neon database in .env.')
    # SQLAlchemy 2.x rejects the legacy postgres:// scheme.
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return url


def _make_engine(url):
    """Connect to Neon, falling back if libpq is too old for channel_binding."""
    try:
        engine = sa.create_engine(url, connect_args={'connect_timeout': 15})
        with engine.connect():
            pass
        return engine
    except sa.exc.OperationalError as exc:
        if 'channel_binding' not in url:
            raise
        print(f'  note: retrying without channel_binding ({exc.orig})')
        stripped = url.split('channel_binding')[0].rstrip('&?')
        engine = sa.create_engine(stripped, connect_args={'connect_timeout': 15})
        with engine.connect():
            pass
        return engine


def _mongo_db():
    uri = os.environ.get('MONGODB_URI', '').strip()
    if not uri:
        sys.exit(
            'MONGODB_URI is not set.\n'
            'Add it to .env (Atlas > Connect > Drivers > "Python"), e.g.\n'
            '  MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/\n'
            'If you are getting "bad auth", percent-encode special characters\n'
            'in the password and confirm the database user exists under\n'
            'Atlas > Database Access.'
        )
    if MongoClient is None:
        sys.exit('pymongo is not installed. Run: venv\\Scripts\\pip install pymongo')

    name = os.environ.get('MONGODB_DB', 'agrolink').strip() or 'agrolink'
    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        client.admin.command('ping')
    except Exception as exc:
        sys.exit(f'MongoDB connection failed: {exc}\n'
                 'Fix the connection before migrating (see test_mongodb.py).')
    return client, client[name]


# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------

def _to_mongo(value):
    """Convert a Postgres value into something BSON can store.

    Mongo has no date type, so dates become midnight UTC. The app stores
    timezone-aware UTC timestamps but the columns are TIMESTAMP WITHOUT TIME
    ZONE, so naive values are read as UTC rather than local time.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        from bson import Binary
        return Binary(bytes(value))
    if isinstance(value, (dict, list)):
        return value
    return str(value)


def _document(row, pk):
    """Build the Mongo document for one Postgres row.

    The Postgres primary key becomes Mongo's _id so relationships stay
    readable and re-runs overwrite instead of duplicating. Source metadata is
    namespaced under _neon so it can never collide with a real column.
    """
    doc = {key: _to_mongo(val) for key, val in row.items()}
    if pk:
        doc['_id'] = doc.pop(pk)
    doc['_neon'] = {'source': 'neon', 'synced_at': datetime.now(timezone.utc).replace(tzinfo=None)}
    return doc


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    dry_run = '--dry-run' in args
    verify_only = '--verify-only' in args

    only = None
    if '--tables' in args:
        idx = args.index('--tables')
        if idx + 1 >= len(args):
            sys.exit('--tables needs a comma-separated list, e.g. --tables users,orders')
        only = {t.strip() for t in args[idx + 1].split(',') if t.strip()}

    pg_url = _pg_url()
    print(f'Source: Neon Postgres {_mask(pg_url)}')

    engine = _make_engine(pg_url)

    meta = sa.MetaData()
    meta.reflect(bind=engine)
    names = sorted(meta.tables)
    if only:
        missing = only - set(names)
        if missing:
            sys.exit(f'No such table(s) in Neon: {", ".join(sorted(missing))}')
        names = [n for n in names if n in only]

    mongo_db = None
    if not dry_run:
        client, mongo_db = _mongo_db()
        db_name = mongo_db.name
        print(f'Target: MongoDB Atlas {_mask(os.environ.get("MONGODB_URI", ""))} db={db_name}')
    else:
        client = None
        print('Target: MongoDB (dry run - nothing will be written)')
    print()

    source_counts = {}
    for name in names:
        table = meta.tables[name]
        pk_cols = [c.name for c in table.primary_key.columns]
        pk = pk_cols[0] if len(pk_cols) == 1 else None
        if pk is None and not dry_run:
            print(f'  {name}: skipped - no single-column primary key, '
                  f'cannot upsert idempotently')
            continue

        with engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(table)).scalar()
            source_counts[name] = count

        if dry_run or verify_only or count == 0:
            print(f'  {name}: {count} row(s)')
            continue

        cols = [c.name for c in table.columns]
        written = 0
        with engine.connect() as conn:
            result = conn.execute(sa.select(*(table.c[c] for c in cols)))
            batch = []
            for row in result.mappings():
                batch.append(ReplaceOne({'_id': _to_mongo(row[pk])},
                                        _document(dict(row), pk), upsert=True))
                if len(batch) >= BATCH:
                    mongo_db[name].bulk_write(batch, ordered=False)
                    written += len(batch)
                    batch = []
            if batch:
                mongo_db[name].bulk_write(batch, ordered=False)
                written += len(batch)
        print(f'  {name}: {written} row(s) written')

    # --- verification: Neon count vs MongoDB count -------------------------
    print('\nVerification (neon -> mongodb):')
    ok = True
    for name, expected in sorted(source_counts.items()):
        if dry_run:
            print(f'  {name}: {expected} (dry run)')
            continue
        actual = mongo_db[name].count_documents({})
        mark = 'OK' if actual == expected else 'MISMATCH'
        if actual != expected:
            ok = False
        print(f'  {name}: {expected} -> {actual}  {mark}')

    if client is not None:
        client.close()

    if dry_run:
        print('\nDry run complete. Nothing was written.')
        print('Next: re-run without --dry-run to copy the records.')
        return
    if not ok:
        sys.exit('\nSome counts did not match - check the rows above. '
                 'Neon was not modified.')
    print('\nAll counts match. Neon is untouched and remains the source of truth.')
    print('Next: point the app at MongoDB for the tables you have migrated.')


if __name__ == '__main__':
    main()
