"""Send a real email through the app's own code path (notifications.send_email)."""
import sys

import app as appmod
import notifications

a = appmod.app
print('MAIL_SERVER  =', a.config.get('MAIL_SERVER'))
print('MAIL_PORT    =', a.config.get('MAIL_PORT'))
print('MAIL_USE_TLS =', a.config.get('MAIL_USE_TLS'))
print('MAIL_USERNAME=', a.config.get('MAIL_USERNAME'))
print('MAIL_PASSWORD configured =', bool(a.config.get('MAIL_PASSWORD')))
print('MAIL_DEFAULT_SENDER =', a.config.get('MAIL_DEFAULT_SENDER'))

to = sys.argv[1] if len(sys.argv) > 1 else a.config['COMPANY_EMAIL']
with a.app_context():
    ok = notifications.send_email(
        'BioFarm Fruits - email test',
        to,
        'This is a test message sent through the application.\n\n'
        'If you can read this, transactional email is working.',
    )
print('send_email returned:', ok)
