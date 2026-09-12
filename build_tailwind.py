"""Build static/css/tailwind.css with the standalone Tailwind CLI.

Downloads the CLI once (Windows x64; other platforms: adjust the URL),
then compiles the utility classes actually used by the templates. The
result is committed so deployments never depend on the Tailwind Play CDN
(the ~110KB runtime script most mobile visitors wait for on every page).

Usage:  python build_tailwind.py
"""
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(ROOT, 'tailwind-cli.exe')
URL = ('https://github.com/tailwindlabs/tailwindcss/releases/download/'
       'v3.4.17/tailwindcss-windows-x64.exe')


def main():
    if not os.path.exists(CLI):
        print('Downloading Tailwind CLI (one time)...')
        urllib.request.urlretrieve(URL, CLI)
        print('Downloaded.')

    cmd = [CLI, '-i', 'tailwind.input.css', '-o', 'static/css/tailwind.css',
           '--minify']
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    sys.stdout.write(result.stdout or '')
    sys.stderr.write(result.stderr or '')
    if result.returncode != 0:
        sys.exit(result.returncode)
    size = os.path.getsize(os.path.join(ROOT, 'static', 'css', 'tailwind.css'))
    print(f'Built static/css/tailwind.css ({size/1024:.1f} KB)')


if __name__ == '__main__':
    main()
