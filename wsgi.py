from app import create_app, start_scheduler

# Runs init_db() so every gunicorn worker gets a ready database (tables
# created, schema patched, seed data present) - this also syncs anything
# still missing after a database migration.
app = create_app()

# Emails the monthly reports (customer statements + chief-admin system report).
# Lock-file guarded so only one gunicorn worker runs it.
start_scheduler()

if __name__ == '__main__':
    app.run()
