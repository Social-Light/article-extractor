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

A Source account with `social_light_org_id` set (`is_demo_source`, below) is a
demo/trial account provisioned by the social-light-platform SSO handoff
(users/views.py:sso_consume) — same role, but additionally allowed to run
extraction and see results, strictly scoped to the one Organisation
auto-provisioned for their trial org (organisations/views.py's run_extraction,
my_keywords, my_extracts and neighbours). An ordinary self-registered Source
(no social-light-platform link) is unaffected by any of this.
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


def is_demo_source(user):
    """A Source account provisioned by the social-light-platform SSO handoff
    (users/views.py:sso_consume) for a *trial* org — as opposed to an
    ordinary self-registered Source with no social-light-platform link at
    all, which stays exactly as unrestricted (and as un-privileged) as it
    always was."""
    return (
        user.is_active
        and getattr(user, 'role', '') == user.ROLE_SOURCE
        and bool(getattr(user, 'social_light_org_id', ''))
    )


def extraction_access_required(view_func):
    """Admin, agency, or a demo source — the three caller types allowed onto
    extraction-related views. A demo source reaching one of these still has
    to be scoped to *their own* Organisation/uploads inside the view itself
    (this decorator only answers "may they be here at all", not "what may
    they see") — see organisations/views.py's run_extraction and neighbours
    for that scoping."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if is_agency_or_admin(request.user) or is_demo_source(request.user):
            return view_func(request, *args, **kwargs)
        raise PermissionDenied
    return _wrapped


def demo_source_required(view_func):
    """The self-serve screens (keyword entry, My Extracts) that exist
    specifically for demo accounts — admin/agency already have their own
    full-featured equivalents (the category UI, the global articles list) and
    have no need of the simplified versions."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if is_demo_source(request.user):
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
