# BioFarm Fruits: deployment and operations

All hosting is on free plans. The domain is the only paid item.

| Piece | Service (free plan) | What it holds |
|---|---|---|
| Web app | Render web service (`render.yaml`) | Flask + gunicorn, 1 worker, 4 threads |
| Database | Neon Postgres (pooled `-pooler` URL) | Users, products, orders, POS sales, blog, CMS text |
| Images | Cloudinary | Product, avatar, blog and CMS pictures, served from Cloudinary's CDN |
| DNS / HTTPS / CDN | Cloudflare | `biofarmfruits.co.ke` |

Free-plan behaviour to expect:

- **Render free** sleeps after about 15 minutes without traffic. The first visit afterwards waits while it wakes (often 30 to 60 seconds). The filesystem is wiped on every deploy and restart, so nothing that must last is written to disk.
- **Neon free** has monthly limits. When one is used up, Neon refuses every connection with *"Your account or project has exceeded the quota"* until the limit resets or the plan changes. Pages that need the database then answer with a "We'll be back shortly" page (HTTP 503) that shows the phone and WhatsApp numbers. Pages that need no database keep working.
- **Cloudinary free** has a monthly credit allowance shared between storage, bandwidth and transformations. Check usage in the Cloudinary dashboard.

## Why images moved to Cloudinary

Uploads used to be stored as bytes inside Postgres and served by the app. Every product card on every page view woke the database and pulled the image through it. That is the most likely reason the free Neon quota ran out (the site went down on 2026-11-24). With `CLOUDINARY_URL` set:

- new uploads go to Cloudinary, and the database stores only the URL;
- pictures are delivered resized (480, 960 and 1600 px) as AVIF or WebP where the phone supports it;
- an image view never touches Render or Neon.

If Cloudinary is unreachable during an upload, the image is stored in the database as before, so an upload is never lost. The migration script moves it across later.

## One-time setup (PowerShell)

### 1. Cloudinary

1. Create a free account at https://cloudinary.com (no card needed).
2. Dashboard > **API environment variable**: copy the `cloudinary://...` value.
3. Render > the `agrolink` service > **Environment** > add `CLOUDINARY_URL` with that value > **Save**. Render redeploys.
4. Copy the existing images across, from this folder on your PC. This needs the database to be reachable. Put the same `CLOUDINARY_URL` in your local `.env` first.

```powershell
.venv\Scripts\python.exe migrate_images_to_cloudinary.py            # dry run: lists what would move
.venv\Scripts\python.exe migrate_images_to_cloudinary.py --apply    # uploads and repoints the records
```

5. Open the site and check that product pictures load. Then, to free space in Neon:

```powershell
.venv\Scripts\python.exe migrate_images_to_cloudinary.py --apply --purge-db-bytes
```

The script is safe to run repeatedly. It never uploads the same image twice, and it only deletes database copies that are already on Cloudinary and no longer used.

### 2. Render environment variables

| Variable | Value |
|---|---|
| `SITE_URL` | `https://biofarmfruits.co.ke` (no trailing slash). Used in canonical links, the sitemap and emails. |
| `CLOUDINARY_URL` | from step 1 |
| `GOOGLE_SITE_VERIFICATION` | optional, see Search Console below |
| `CF_BEACON_TOKEN` | optional, Cloudflare Web Analytics token |
| `SECRET_KEY`, `DATABASE_URL`, `MAIL_*`, `CHIEF_ADMIN_EMAIL` | as before |

### 3. Google Search Console

1. https://search.google.com/search-console > **Add property** > **URL prefix** > `https://biofarmfruits.co.ke/`.
2. Choose **HTML tag** and copy only the `content="..."` value into Render as `GOOGLE_SITE_VERIFICATION`. After the redeploy, click **Verify**.
   (Alternatively, use the **Domain** property and verify with a DNS TXT record in Cloudflare. That covers `www` and all subdomains too.)
3. **Sitemaps** > submit `sitemap.xml`.
4. Use **URL inspection** on the homepage and one product page, then **Request indexing**.

### 4. Cloudflare

- SSL/TLS mode: **Full (strict)**.
- Redirect `www.biofarmfruits.co.ke` to `https://biofarmfruits.co.ke` (Rules > Redirect Rules), so Google sees one address.
- Do not add "Cache Everything" rules for the site's HTML. Pages contain the login state and a CSRF token. Static files under `/static/` are already sent with long cache lifetimes.
- Optional free analytics without cookies: Analytics & Logs > **Web Analytics** > add the site > copy the token into `CF_BEACON_TOKEN`.

## When the site shows "We'll be back shortly"

1. Open the Neon console > project > **Usage** (or **Billing**) to see which limit is used up.
   - **Compute hours or data transfer**: these reset at the start of the next billing period. Cloudinary removes the image traffic that was using them up.
   - **Storage**: this does not reset by itself. Run the migration with `--purge-db-bytes` (step 1.5) to delete the image bytes, which are most of the stored data.
2. Once Neon accepts connections again, the site recovers on its own. No redeploy is needed.

## Deploying and rolling back

- Render deploys automatically when `main` is pushed to GitHub.
- To roll back: Render > the service > **Events** > pick the last good deploy > **Rollback**. Or run `git revert <commit>` and push.
- Keep gunicorn at **1 worker** (`--workers 1 --threads 4`). `wsgi.py` runs the database setup once per worker at boot, and the 512 MB free instance runs out of memory with more.

## Backups (free)

Neon free keeps a short restore window. For your own copy, run this from a PC with PostgreSQL client tools installed:

```powershell
pg_dump "<DATABASE_URL from Render>" --no-owner --format=custom --file "biofarm-$(Get-Date -Format yyyy-MM-dd).dump"
```

Restore into an empty database with `pg_restore --no-owner -d "<new DATABASE_URL>" <file>.dump`.

## Security notes

- Secrets live only in Render's environment settings and your local `.env` (gitignored). The GitHub repository is public, so never commit `.env`.
- In production, session cookies are sent with `Secure` + `HttpOnly` + `SameSite=Lax`, and HSTS is on.
