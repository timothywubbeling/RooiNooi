"""AWS Lambda entry point (Lambda Function URL -> Flask).

Handler setting in AWS: lambda_handler.handler
Environment: DATABASE_URL = the Supabase "Transaction pooler" connection string.
"""
import os
import traceback

_handler = None


def _plain(status, text):
    return {"statusCode": status, "headers": {"Content-Type": "text/plain; charset=utf-8",
                                              "Cache-Control": "no-store"}, "body": text}


def handler(event, context):
    global _handler
    if _handler is None:
        if not os.environ.get("DATABASE_URL"):
            return _plain(503, "The Rooi Nooi shop is installed but not connected to its database yet.\n"
                               "In AWS Lambda > Configuration > Environment variables, add DATABASE_URL "
                               "(the Supabase Transaction pooler connection string).")
        try:
            # Built on the first request, not at import, so first-time setup gets the full timeout.
            from apig_wsgi import make_lambda_handler
            from shop import create_app
            _handler = make_lambda_handler(create_app(), binary_support=True)
        except Exception:
            traceback.print_exc()
            return _plain(503, "The shop could not start. Check the DATABASE_URL setting; "
                               "details are in the CloudWatch logs.")
    return _handler(event, context)
