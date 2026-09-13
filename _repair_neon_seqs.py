"""One-off: run fix_id_sequences() directly against the live Neon DB.

Same logic as app.fix_id_sequences(), usable without booting the whole
app (no seed/backfill side effects). Idempotent - sets each sequence to
max(id)+1. Prints the before/after state of every sequence it touches.
"""
import sys

from sqlalchemy import create_engine, text, inspect

raw = open('.env', encoding='utf-8-sig').read()
url = [l.split('=', 1)[1].strip()
       for l in raw.splitlines() if l.startswith('NEON_DATABASE_URL=')][0]

engine = create_engine(url)
insp = inspect(engine)

with engine.begin() as c:
    for table in insp.get_table_names():
        pk = insp.get_pk_constraint(table)
        cols = pk.get('constrained_columns') if pk else None
        if not cols or len(cols) != 1:
            continue
        col = cols[0]
        try:
            before = c.execute(text(
                f"SELECT last_value, is_called FROM {table}_{col}_seq"
            )).fetchone()
            c.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
                f"COALESCE((SELECT MAX(\"{col}\") FROM \"{table}\"), 0) + 1, false)"))
            after = c.execute(text(
                f"SELECT last_value, is_called FROM {table}_{col}_seq"
            )).fetchone()
            print(f'fixed {table}: {before} -> {after}', flush=True)
        except Exception as exc:  # noqa: BLE001 - table may lack a sequence
            print(f'skip  {table}: {str(exc).splitlines()[0]}', flush=True)

print('all done', flush=True)
sys.exit(0)
