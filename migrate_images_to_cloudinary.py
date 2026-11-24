"""Move images stored in the database (and the local uploads/ folder) to
Cloudinary, then point every product / avatar / blog / CMS / disease record at
the Cloudinary URL.

Safe by design:
  * DRY RUN by default. Nothing is uploaded or written without --apply.
  * Repeatable. Each image's Cloudinary public id is derived from its
    filename, so a second run overwrites the same asset instead of making a
    copy, and references that already point at Cloudinary are left alone.
  * Non-destructive. The bytes in the uploaded_images table are kept, so
    /uploads/<file> keeps working for anything not yet migrated. Only
    --purge-db-bytes deletes them, and only for images that were uploaded
    successfully and that no row references by /uploads/ any more. Run that
    after you have checked the site.

Usage (PowerShell, from the project folder, with DATABASE_URL and
CLOUDINARY_URL set in .env or the environment):

    .venv\\Scripts\\python.exe migrate_images_to_cloudinary.py            # report only
    .venv\\Scripts\\python.exe migrate_images_to_cloudinary.py --apply    # do it
    .venv\\Scripts\\python.exe migrate_images_to_cloudinary.py --apply --purge-db-bytes
"""
import argparse
import os
import sys

os.environ.setdefault('DISABLE_SCHEDULER', '1')

import image_storage  # noqa: E402
import app as appmod  # noqa: E402  (imports models; does not seed or migrate)
from app import (BlogPost, DiseaseDetection, Product, SiteContent,  # noqa: E402
                 UploadedImage, User, db, upload_filename)

# (model, column) pairs that hold an image reference.
IMAGE_COLUMNS = [(Product, 'image'), (User, 'avatar'), (BlogPost, 'image'),
                 (DiseaseDetection, 'image')]
CMS_IMAGE_KEYS = [key for key, _label, kind in appmod.CONTENT_FIELDS if kind == 'image']


def referenced_rows():
    """Yield (row, column, filename) for every reference to a served upload."""
    for model, column in IMAGE_COLUMNS:
        for row in model.query.filter(getattr(model, column).isnot(None)).all():
            name = upload_filename(getattr(row, column))
            if name:
                yield row, column, name
    for row in SiteContent.query.filter(SiteContent.key.in_(CMS_IMAGE_KEYS)).all():
        name = upload_filename(row.value)
        if name:
            yield row, 'value', name


def cloudinary_stems():
    """Filename stems of every image that rows reference by Cloudinary URL."""
    values = []
    for model, column in IMAGE_COLUMNS:
        values += [v for (v,) in db.session.query(getattr(model, column)).all()]
    values += [v for (v,) in db.session.query(SiteContent.value)
               .filter(SiteContent.key.in_(CMS_IMAGE_KEYS)).all()]
    stems = set()
    for value in values:
        if image_storage.is_cloudinary_url(value):
            stems.add(value.strip().rsplit('/', 1)[-1].rsplit('.', 1)[0])
    return stems


def image_bytes(filename):
    row = UploadedImage.query.filter_by(filename=filename).first()
    if row and row.data:
        return row.data
    path = os.path.join(appmod.app.config['UPLOAD_FOLDER'], filename)
    if os.path.isfile(path):
        with open(path, 'rb') as fh:
            return fh.read()
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--apply', action='store_true', help='upload and rewrite references')
    parser.add_argument('--purge-db-bytes', action='store_true',
                        help='after --apply, delete migrated image bytes from the database')
    args = parser.parse_args()

    if args.apply and not image_storage.enabled():
        sys.exit('CLOUDINARY_URL (or CLOUDINARY_CLOUD_NAME/API_KEY/API_SECRET) is not set.')

    with appmod.app.app_context():
        refs = list(referenced_rows())
        names = sorted({name for _r, _c, name in refs})
        print(f'{len(refs)} reference(s) to {len(names)} distinct upload(s).')

        uploaded, missing, failed = {}, [], []
        for name in names:
            data = image_bytes(name)
            if data is None:
                missing.append(name)
                continue
            if not args.apply:
                continue
            try:
                uploaded[name] = image_storage.upload(data, name)['url']
                print(f'  uploaded {name}')
            except image_storage.UploadError as exc:
                failed.append(name)
                print(f'  FAILED   {name}: {exc}')

        if missing:
            print(f'\n{len(missing)} referenced file(s) exist neither in the database nor in '
                  f'uploads/ and cannot be migrated (they keep showing the placeholder):')
            for name in missing:
                print(f'  {name}')

        if not args.apply:
            print(f'\nDry run: {len(names) - len(missing)} image(s) would be uploaded. '
                  'Re-run with --apply.')
            return

        changed = 0
        for row, column, name in refs:
            if name in uploaded:
                setattr(row, column, uploaded[name])
                changed += 1
        db.session.commit()
        print(f'\nRewrote {changed} reference(s). {len(failed)} upload(s) failed '
              '(re-run to retry them).')

        if args.purge_db_bytes:
            # Decided from the database as it is now, not from this run, so
            # purging works on a later run too. A stored image is purged only
            # when some row points at its Cloudinary copy and no row still
            # points at /uploads/<it>.
            still_referenced = {name for _r, _c, name in referenced_rows()}
            on_cloudinary = cloudinary_stems()
            purge = [f for (f,) in db.session.query(UploadedImage.filename).all()
                     if f.rsplit('.', 1)[0] in on_cloudinary and f not in still_referenced]
            if purge:
                UploadedImage.query.filter(UploadedImage.filename.in_(purge)) \
                    .delete(synchronize_session=False)
                db.session.commit()
            print(f'Deleted the database copy of {len(purge)} migrated image(s).')


if __name__ == '__main__':
    main()
