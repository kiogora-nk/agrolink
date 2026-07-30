# Market2Farm Deployment Notes

## Lowest-cost hosting

For a budget under USD 1, use Render's free web service for demos and testing. Render includes managed TLS/SSL certificates, custom domains, logs, and a free Python web service, but the service sleeps after inactivity and the filesystem is ephemeral.

Use the included `render.yaml` blueprint and set these environment variables in Render:

- `SECRET_KEY`
- `JWT_SECRET_KEY`
- `SECURITY_PASSWORD_SALT`
- `DATABASE_URL`
- `OPENAI_API_KEY` if AI features are enabled
- `SITE_URL`
- Mail settings if email delivery is required

## Data persistence

Do not rely on SQLite or uploaded files for long-term production data on a free Render web service, because local files can be lost on restart or redeploy. For a demo, Render Postgres can be used at USD 0, but the free database has a short lifetime. For a real marketplace, move to a paid database and object storage.

## SSL and logs

Render manages TLS certificates automatically for the Render URL and configured custom domains. After deployment, open the Chief Admin Dashboard and use **Record SSL/Web Server Log Review** to capture the current HTTPS forwarding state in the audit log.

## Security reminder

Rotate any API key that was previously committed or shared. Keep live secrets only in the hosting provider's environment variable manager.
