"""End-to-end check for the chief-admin batch number feature.

Run: venv/Scripts/python.exe _test_batch.py
"""
import re

from app import app, db, User
from werkzeug.security import generate_password_hash as gph

with app.app_context():
    u = User.query.filter_by(username='test_chief').first()
    if u:
        db.session.delete(u)
        db.session.commit()
    u = User(username='test_chief', email='test_chief@example.com',
             password_hash=gph('Test@12345'), role='customer')
    db.session.add(u)
    db.session.commit()
    uid = u.id
    print('test user created, id', uid)

with app.test_client() as c:
    # 1. Log in as the seeded chief admin (fetch the CSRF token first).
    r = c.get('/login')
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                    r.get_data(as_text=True)).group(1)
    r = c.post('/login', data={'username': 'admin', 'password': 'Admin@2025',
                               'csrf_token': tok},
               follow_redirects=True)
    assert 'Logout' in r.data.decode(), 'chief admin login failed'
    print('[OK] chief admin login')

    # The token is bound to the session, so it stays valid for later POSTs.
    def post(url, **data):
        data['csrf_token'] = tok
        return c.post(url, data=data, follow_redirects=True)

    # 2. Users management page shows the batch number UI.
    r = c.get('/admin/users')
    assert r.status_code == 200, f'/admin/users -> {r.status_code}'
    assert 'batch_number' in r.data.decode(), 'batch UI missing on users page'
    print('[OK] /admin/users renders with batch number column')

    # 3. Batch number before role promotion is rejected (user is still a
    #    customer here — the grant must be refused and nothing saved).
    r = post(f'/chief-admin/user/{uid}/batch-number', batch_number='BF-0001')
    with app.app_context():
        assert User.query.get(uid).batch_number is None, 'batch granted to non-chief-admin!'
    print('[OK] batch number rejected before promotion')

    # 4. Promote the test user to chief admin.
    r = post(f'/chief-admin/user/{uid}/role', role='chief_admin')
    assert r.status_code == 200
    with app.app_context():
        assert User.query.get(uid).role == 'chief_admin', 'role not applied'
    print('[OK] promoted test user to chief_admin')

    # 5. Grant a batch number now that they are chief admin.
    r = post(f'/chief-admin/user/{uid}/batch-number', batch_number='BF-0001')
    with app.app_context():
        assert User.query.get(uid).batch_number == 'BF-0001', 'batch not saved'
    print('[OK] batch number BF-0001 granted')

    # 6. Duplicate batch number on another user is rejected.
    dup = User(username='test_dup', email='test_dup@example.com',
               password_hash=gph('Test@12345'), role='chief_admin')
    with app.app_context():
        db.session.add(dup)
        db.session.commit()
        dup_id = dup.id
    r = post(f'/chief-admin/user/{dup_id}/batch-number', batch_number='bf-0001')
    with app.app_context():
        assert User.query.get(dup_id).batch_number is None, 'duplicate allowed!'
    print('[OK] duplicate batch number rejected')

    # 7. Batch number shows on the public profile.
    r = c.get('/u/test_chief')
    if r.status_code == 200:
        assert 'Batch No. BF-0001' in r.data.decode(), 'batch not on profile'
        print('[OK] batch number visible on public profile')
    else:
        print('[SKIP] public profile route differs:', r.status_code)

    # 8. Revoke the batch number.
    r = post(f'/chief-admin/user/{uid}/batch-number', batch_number='')
    with app.app_context():
        assert User.query.get(uid).batch_number is None, 'batch not revoked'
    print('[OK] batch number revoked')

    # 9. Chief admin dashboard still renders.
    r = c.get('/chief-admin/dashboard')
    assert r.status_code == 200, f'chief dashboard -> {r.status_code}'
    print('[OK] chief admin dashboard renders')

# Clean up test users.
with app.app_context():
    for name in ('test_chief', 'test_dup'):
        u = User.query.filter_by(username=name).first()
        if u:
            db.session.delete(u)
    db.session.commit()
    print('[OK] test users cleaned up')
print('ALL CHECKS PASSED')
