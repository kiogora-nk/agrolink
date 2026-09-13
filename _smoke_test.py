"""Smoke test for the notification bell, dashboard avatar, and change-password flow."""
import re

import app as a

c = a.app.test_client()

# Anonymous homepage — exercises updated context processor
r = c.get('/')
print('GET / (anon):', r.status_code)
assert b'notifToggle' not in r.data  # bell hidden for anonymous

# Login as seeded admin (fetch CSRF token from the login form first)
r = c.get('/login')
tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                r.data.decode()).group(1)
r = c.post('/login', data={'username': 'admin', 'password': 'Admin@2025',
                           'csrf_token': tok},
           follow_redirects=True)
print('login:', r.status_code)

# Dashboard with avatar + bell
r = c.get('/dashboard')
print('GET /dashboard:', r.status_code)
assert b'notifToggle' in r.data, 'bell missing'
assert 'Welcome back' in r.data.decode()
print('bell on dashboard: OK')

# Notifications page still renders
r = c.get('/notifications')
print('GET /notifications:', r.status_code)

# Profile page — change password form
r = c.get('/profile')
print('GET /profile:', r.status_code)
assert b'/change-password' in r.data, 'change-password form missing'

tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                r.data.decode()).group(1)

# Wrong current password -> rejected
r = c.post('/change-password', data={
    'csrf_token': tok, 'current_password': 'WRONG',
    'new_password': 'NewPass123', 'confirm_password': 'NewPass123'},
    follow_redirects=True)
assert b'incorrect' in r.data, 'wrong current password not rejected'
print('wrong current pw rejected: True')

# Mismatched confirm -> rejected (current pw is still Admin@2025 here)
r = c.post('/change-password', data={
    'csrf_token': tok, 'current_password': 'Admin@2025',
    'new_password': 'abcdef1', 'confirm_password': 'zzzzzz2'},
    follow_redirects=True)
assert b'do not match' in r.data, 'mismatch not rejected'
print('mismatch rejected: True')

# Too short -> rejected
r = c.post('/change-password', data={
    'csrf_token': tok, 'current_password': 'Admin@2025',
    'new_password': 'abc', 'confirm_password': 'abc'},
    follow_redirects=True)
assert b'at least 6' in r.data, 'short password not rejected'
print('too short rejected: True')

# Valid change -> accepted, then change back so the seed password still works
r = c.post('/change-password', data={
    'csrf_token': tok, 'current_password': 'Admin@2025',
    'new_password': 'NewPass123', 'confirm_password': 'NewPass123'},
    follow_redirects=True)
assert b'changed successfully' in r.data, 'valid change rejected'
print('valid change accepted: True')

r = c.post('/change-password', data={
    'csrf_token': tok, 'current_password': 'NewPass123',
    'new_password': 'Admin@2025', 'confirm_password': 'Admin@2025'},
    follow_redirects=True)
assert b'changed successfully' in r.data, 'password not changed back'
print('changed back: True')

print('ALL SMOKE TESTS PASSED')
