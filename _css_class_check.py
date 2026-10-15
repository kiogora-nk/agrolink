"""Find Tailwind classes used in templates that the compiled CSS does not define.

Jinja tags are blanked out BEFORE scanning, so a class written directly against
a tag (e.g. `class="... {% if x %}lg:grid-cols-[1.05fr_0.95fr]{% endif %}"`)
is still checked. That adjacency is exactly what makes the Tailwind scanner
drop the candidate, so those are the classes most likely to be missing.
"""
import os
import re
import sys

CSS = 'static/css/tailwind.css'
SPECIAL = set(':/.[]%()#,!#&*+~<>=|$@"')
JINJA = re.compile(r'\{[{%].*?[}%]\}', re.S)


def esc(cls):
    """Tailwind's selector escaping for an arbitrary class name."""
    return '.' + ''.join(('\\' + ch) if ch in SPECIAL else ch for ch in cls)


def used_classes(html):
    src = JINJA.sub(' ', html)
    out = set()
    for m in re.findall(r'class="([^"]*)"', src):
        for tok in m.split():
            if any(x in tok for x in ('{%', '{{', '%}', '}}', '{#', '#}')):
                continue
            out.add(tok)
    return out


def main(paths):
    css = open(CSS, encoding='utf-8').read()
    total_missing = 0
    for path in paths:
        html = open(path, encoding='utf-8').read()
        classes = used_classes(html)
        missing = [c for c in sorted(classes) if esc(c) not in css]
        if missing:
            total_missing += len(missing)
            print(f'=== {path}: {len(missing)} missing ===')
            for c in missing:
                print('   ', c)
    print(f'\ntotal missing classes: {total_missing}')


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        args = []
        for root, _dirs, files in os.walk('templates'):
            for f in files:
                if f.endswith('.html'):
                    args.append(os.path.join(root, f))
    main(sorted(args))
