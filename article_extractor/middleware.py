from django.db import close_old_connections


class DBConnectionMiddleware:
    """
    Discard stale database connections before every request.

    PythonAnywhere's MySQL server closes idle SSL connections after a timeout.
    Without this middleware, the next request after the connection has been
    dropped gets an 'SSL connection has been closed unexpectedly' error.
    close_old_connections() detects and discards any connection that is past
    its maximum age or has been marked unusable, so Django opens a fresh one.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        close_old_connections()
        return self.get_response(request)
