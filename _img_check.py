"""One-off: remove the uploaded_images row + file for the second test mango."""
from sqlalchemy import create_engine, text

raw = open('.env', encoding='utf-8-sig').read()
url = [l.split('=', 1)[1].strip()
       for l in raw.splitlines() if l.startswith('NEON_DATABASE_URL=')][0]
engine = create_engine(url)
with engine.begin() as c:
    rows = c.execute(text(
        "SELECT id, filename FROM uploaded_images "
        "WHERE filename LIKE '%e24629131e52467ef198199cb649cac5%'")).fetchall()
    print('rows:', rows)
    if rows:
        c.execute(text(
            "DELETE FROM uploaded_images "
            "WHERE filename LIKE '%e24629131e52467ef198199cb649cac5%'"))
        print('deleted')
