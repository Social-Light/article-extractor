"""
Shared permission helpers for the Social Light platform.

Role summary
------------
  Admin   — is_staff / is_superuser.  Full access.
  Agency  — user.role == 'agency'.    Can run extractions, view orgs &
             publishers.  Cannot create/edit/delete orgs or publishers.
             Cannot see newspaper sources or the sources list.
  Source  — default role.             Can upload newspapers and view their
             own uploads only.
"""
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def is_admin(user):
    return user.is_active and (user.is_staff or user.is_superuser)


def is_agency_or_admin(user):
    return user.is_active and (is_admin(user) or getattr(user, 'role', '') == 'agency')


def agency_or_admin_required(view_func):
    """Allow both admin (is_staff/superuser) and agency users.  Redirect
    unauthenticated users to login; raise 403 for authenticated sources."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if is_agency_or_admin(request.user):
            return view_func(request, *args, **kwargs)
        raise PermissionDenied
    return _wrapped


def admin_required(view_func):
    """Strictly admin (is_staff / is_superuser) only.  Wraps the same
    behaviour as @staff_member_required but raises 403 instead of
    redirecting to the Django admin login page."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if is_admin(request.user):
            return view_func(request, *args, **kwargs)
        raise PermissionDenied
    return _wrapped
