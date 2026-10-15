"""Route performance + query-count benchmark.

Measures response time and the number of SQL statements each page issues, so
"before vs after" optimisation claims are backed by real numbers rather than
guesswork.

Every run starts from the same pristine snapshot of the development database
(a copy of the committed one), so a measurement is never skewed by rows an
earlier run inserted - hitting /product/<id> seeds in-built reviews, which
would otherwise make the second run of a comparison look different for
reasons that have nothing to do with the code.

Read-only with respect to your real database; it never touches production.

    venv/Scripts/python.exe _bench_routes.py
"""
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.environ.get(
    'BENCH_SNAPSHOT',
    os.path.join(os.environ.get('TEMP', HERE), 'agrolink_head', 'instance', 'biofarm.db'))
BENCH_DB = os.path.join(os.environ.get('TEMP', HERE), 'agrolink_bench.db')

# Every run gets a fresh copy of the snapshot, and DATABASE_URL is set BEFORE
# app is imported. load_dotenv() does not override an existing environment
# variable, so this wins over .env's Neon URL and production is never touched.
if os.path.exists(SNAPSHOT):
    shutil.copyfile(SNAPSHOT, BENCH_DB)
    os.environ['DATABASE_URL'] = 'sqlite:///' + BENCH_DB.replace('\\', '/')
else:
    os.environ.setdefault('DATABASE_URL', 'sqlite:///biofarm.db')

from sqlalchemy import event  # noqa: E402

from app import app as flask_app, db, create_app  # noqa: E402

ROUTES = [
    ('/', 'home'),
    ('/products', 'products'),
    ('/product/1', 'product_detail'),
    ('/blog', 'blog'),
    ('/contact', 'contact'),
    ('/training', 'training'),
    ('/login', 'login'),
]

REPEATS = int(os.environ.get('BENCH_REPEATS', '5'))


def count_queries(app):
    """Attach a statement counter to the engine; returns (state, reset)."""
    state = {'n': 0, 'stmts': []}

    with app.app_context():
        engine = db.engine

    @event.listens_for(engine, 'before_cursor_execute')
    def _before(conn, cursor, statement, parameters, context, executemany):
        state['n'] += 1
        state['stmts'].append(statement)

    def reset():
        state['n'] = 0
        state['stmts'] = []

    return state, reset


def main():
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()
    state, reset = count_queries(app)

    print('=' * 78)
    print('BASELINE — route response time and SQL statement count')
    print('=' * 78)
    print(f'database: {app.config["SQLALCHEMY_DATABASE_URI"]}')
    print(f'repeats per route: {REPEATS}\n')

    print(f'{"route":<22} {"status":>6} {"queries":>8} {"median ms":>10} {"min ms":>8} {"bytes":>8}')
    print('-' * 78)

    totals = {}
    for path, label in ROUTES:
        # Warm up (template compilation, first-touch caches) then measure.
        client.get(path)
        reset()
        r = client.get(path)
        queries = state['n']

        times = []
        for _ in range(REPEATS):
            t0 = time.perf_counter()
            resp = client.get(path)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        median = times[len(times) // 2]
        body = resp.get_data(as_text=True)

        totals[path] = {'queries': queries, 'median': median, 'bytes': len(body)}
        print(f'{label + " " + path:<22} {resp.status_code:>6} {queries:>8} '
              f'{median:>10.1f} {times[0]:>8.1f} {len(body):>8}')

    # ---- asset weight on the homepage -------------------------------------
    print('\n--- homepage asset references ---')
    r = client.get('/')
    html = r.get_data(as_text=True)
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    local_uploads = re.findall(r'(?:src|href)="(/uploads/[^"]+)"', html)
    print(f'external (http/https) asset refs : {len(external)}')
    for url in sorted(set(external)):
        print(f'    {url[:96]}')
    print(f'local /uploads refs on homepage  : {len(local_uploads)}')

    # ---- static asset sizes ------------------------------------------------
    print('\n--- static asset sizes (bytes) ---')
    static_dir = app.static_folder
    for root, _dirs, files in os.walk(static_dir):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, static_dir).replace('\\', '/')
            size = os.path.getsize(full)
            if size > 2000:
                print(f'    {rel:<34} {size:>9,}')

    print('\n--- summary ---')
    for path, _label in ROUTES:
        t = totals[path]
        print(f'    {path:<16} {t["queries"]:>3} queries  {t["median"]:>7.1f} ms  {t["bytes"]:>7,} bytes')


if __name__ == '__main__':
    sys.exit(main())
