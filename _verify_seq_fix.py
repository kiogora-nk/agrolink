"""Verify the sequence-repair fix: sqlite no-op + correct SQL generated."""
from unittest.mock import patch, MagicMock
from app import create_app, db
from app import fix_id_sequences, Product, User

# --- [1] SQLite: must be a no-op, app must still start and serve ---
app = create_app()
with app.app_context():
    fix_id_sequences()
    print('[1] sqlite no-op: OK  (engine:', db.engine.name + ')')

client = app.test_client()
r = client.get('/')
print('[2] homepage after fix:', r.status_code)

# --- [2] Simulate Postgres: check the SQL fix_id_sequences would emit ---
with app.app_context():
    captured = []

    class FakeInspector:
        @staticmethod
        def get_table_names():
            return ['products', 'users', 'order_items']  # order_items: composite pk
        @staticmethod
        def get_pk_constraint(table):
            return {'constraint_columns': (
                {'products': ['id'], 'users': ['id'],
                 'order_items': ['order_id', 'product_id']}[table])}

    class FakeSession:
        def execute(self, stmt):
            captured.append(str(stmt))
        def commit(self):
            pass
        def rollback(self):
            raise AssertionError('rollback should not be called on happy path')

    fake_engine = MagicMock()
    fake_engine.name = 'postgresql'
    with patch('app.db') as fake_db, \
         patch('sqlalchemy.inspect', return_value=FakeInspector()):
        fake_db.engine = fake_engine
        fake_db.session = FakeSession()
        # app.logger stays real
        fix_id_sequences()

    print('[3] statements emitted for postgres:')
    for s in captured:
        print('   ', s)
    assert len(captured) == 2, 'composite-pk table must be skipped'
    assert "pg_get_serial_sequence('products', 'id')" in captured[0]
    assert 'MAX("id")' in captured[0]
    print('[4] products + users repaired, order_items (composite pk) skipped: OK')
print('done.')
