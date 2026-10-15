"""Exercise the real email routes through the test client and report what happens."""
import logging

import app as appmod
from app import User, db

a = appmod.app
a.config['WTF_CSRF_ENABLED'] = False

logging.basicConfig(level=logging.INFO)

calls = []
orig = appmod.notifications.send_email


def spy(subject, recipients, body, html=None, attachments=None):
    ok = orig(subject, recipients, body, html=html, attachments=attachments)
    calls.append((subject, recipients, ok))
    print(f'  -> send_email(subject={subject!r}, to={recipients!r}) returned {ok}')
    return ok


appmod.notifications.send_email = spy

with a.app_context():
    users = User.query.limit(5).all()
    print('users:', [(u.id, u.username, u.email, u.is_chief_admin()) for u in users])
    target = next((u for u in users if u.email), None)

print('\n--- POST /forgot-password ---')
c = a.test_client()
r = c.post('/forgot-password', data={'method': 'email', 'identifier': target.email if target else 'x'},
           follow_redirects=True)
print('status', r.status_code)
print('calls:', calls)
