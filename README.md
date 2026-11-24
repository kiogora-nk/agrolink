# BioFarm Fruits 🐉🥑

A fruit & seedling marketplace for BioFarm Fruits (Nairobi, Kenya): customers browse and
order produce online, and staff manage products, orders, and customer communication from
an admin panel.

## Features

- 🛒 Marketplace with admin-approved products, stock, categories, and reviews
- 📦 Order placement with PDF receipts (QR-verified, emailed to customer + admins)
- 🔐 Accounts with login/registration, profile pages, and email-based password reset
- 🌿 Crop disease detection — offline Naive Bayes symptom classifier (no API key needed;
  optional hosted vision model when `CROP_VISION_API_KEY` is set)
- 🌤️ Climate intelligence — real forecasts and farming advice via Open-Meteo (free, no key)
- ✍️ Blog + CMS for editable site content
- 👥 Role-based staff area (admin / chief admin / product manager)
- 🔔 In-app notifications and transactional email via Gmail SMTP (fails soft if unset)
- 📧 Monthly customer statement + system report emails (background scheduler)

## Tech Stack

- **Backend:** Flask, SQLAlchemy, Flask-Login, Flask-WTF (CSRF)
- **Database:** SQLite (development) / PostgreSQL (production via `DATABASE_URL`)
- **PDF:** ReportLab + qrcode
- **Deployment:** Render (see `render.yaml`)

## Local Development (Windows PowerShell)

Production runs Python 3.11 (`runtime.txt`), so use the same locally. With
[uv](https://docs.astral.sh/uv/) installed:

```powershell
uv venv .venv --python 3.11
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
Copy-Item .env.example .env      # then edit .env with real values
.venv\Scripts\python.exe app.py
```

The app runs at http://localhost:5000 and prints the seeded staff login on first start.
**Change the seeded passwords immediately** (Profile → Change Password) before going live.

> If `.env` holds the production `DATABASE_URL`, `python app.py` works on live data.
> Remove that line locally to use a throwaway SQLite database instead.

### Tests

Each script builds its own throwaway SQLite database and sends no email:

```powershell
.venv\Scripts\python.exe _test_order_reliability.py
.venv\Scripts\python.exe _test_seo_and_images.py
```

## Configuration

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session/token signing key (long random string) |
| `SITE_URL` | Public base URL, e.g. `https://biofarmfruits.co.ke` (canonical links, sitemap, emails) |
| `MAIL_*` | Gmail SMTP credentials (app password) for outgoing email |
| `COMPANY_*` | Contact details, social links shown across the site |
| `DATABASE_URL` | PostgreSQL connection string in production (omit for SQLite) |
| `CROP_VISION_API_KEY` / `CROP_VISION_MODEL` | Optional: image-based disease detection |
| `CLOUDINARY_URL` | Image storage on Cloudinary (production). Unset = images stored in the database |
| `GOOGLE_SITE_VERIFICATION` | Optional: Google Search Console HTML-tag verification value |
| `CF_BEACON_TOKEN` | Optional: Cloudflare Web Analytics (cookieless) |

## Deployment

See **[DEPLOYMENT.md](DEPLOYMENT.md)**: free hosting setup (Render, Neon, Cloudinary,
Cloudflare), Google Search Console, recovering from a database-quota outage,
backups and rollback.

## Security

- Never commit `.env` (it is gitignored).
- Rotate any secret or password that was ever committed to git history.
- Staff passwords for seeded accounts must be changed on first login.
