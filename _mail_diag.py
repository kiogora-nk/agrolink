"""Diagnose Gmail SMTP: login with the raw app password vs the space-stripped one."""
import os
import smtplib
import ssl

from dotenv import load_dotenv

load_dotenv()

host = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
port = int(os.environ.get('MAIL_PORT', 587))
user = os.environ.get('MAIL_USERNAME')
raw = os.environ.get('MAIL_PASSWORD')
stripped = ''.join(raw.split()) if raw else raw

print(f'server={host}:{port}')
print(f'user={user!r}')
print(f'raw   len={len(raw) if raw else 0}')
print(f'clean len={len(stripped) if stripped else 0}')

for label, pw in (('raw', raw), ('stripped', stripped)):
    if not pw:
        print(f'[{label}] no password')
        continue
    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            s.login(user, pw)
            print(f'[{label}] LOGIN OK')
    except Exception as exc:  # noqa: BLE001
        print(f'[{label}] FAILED: {type(exc).__name__}: {exc}')
