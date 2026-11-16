"""Entry point for hosts that run Python apps with Phusion Passenger ("Setup Python App").

Passenger looks for a WSGI callable called `application` in this file.
Configuration comes from environment variables (DATABASE_URL etc., see HOSTING.md).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shop import create_app  # noqa: E402

application = create_app()
