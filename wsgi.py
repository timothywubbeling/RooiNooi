"""Entry point for a WSGI server such as gunicorn or mod_wsgi.

    gunicorn --workers 2 --bind 127.0.0.1:8000 wsgi:app

Configuration comes from environment variables (DATABASE_URL etc., see HOSTING.md).
"""
from shop import create_app

app = application = create_app()
