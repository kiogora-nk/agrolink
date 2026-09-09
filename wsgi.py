from app import app, start_scheduler

# Emails the monthly reports (customer statements + chief-admin system report).
# Lock-file guarded so only one gunicorn worker runs it.
start_scheduler()

if __name__ == '__main__':
    app.run()
