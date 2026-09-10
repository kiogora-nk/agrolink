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

## Local Development

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt

copy .env.example .env           # then edit .env with real values
python app.py
```

The app runs at http://localhost:5000 and prints the seeded staff login on first start.
**Change the seeded passwords immediately** (Profile → Change Password) before going live.

## Configuration

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session/token signing key (long random string) |
| `SITE_URL` | Public base URL (used in emailed links) |
| `MAIL_*` | Gmail SMTP credentials (app password) for outgoing email |
| `COMPANY_*` | Contact details, social links shown across the site |
| `DATABASE_URL` | PostgreSQL connection string in production (omit for SQLite) |
| `CROP_VISION_API_KEY` / `CROP_VISION_MODEL` | Optional: image-based disease detection |

## Deployment (Render)

The repo ships a Render blueprint (`render.yaml`) that creates a web service and a
PostgreSQL database. In the Render dashboard set at least `SECRET_KEY` and `SITE_URL`,
plus the mail variables if you want email delivery.

Notes:

- Use gunicorn (the blueprint's start command) — never `python app.py` in production.
- Render's free tier has an ephemeral filesystem: uploaded images and SQLite data do not
  survive restarts. Point `DATABASE_URL` at the blueprint's Postgres for real data.

## Security

- Never commit `.env` (it is gitignored).
- Rotate any secret or password that was ever committed to git history.
- Staff passwords for seeded accounts must be changed on first login.
