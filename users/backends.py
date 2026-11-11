"""Authenticate by email address rather than by username.

The login form asks for an email address (users/forms.py:LoginForm), but the
User model's USERNAME_FIELD is still plain `username` — changing that on a
live custom user model is its own migration and isn't what was asked for here.
This backend bridges the two: it takes whatever was typed into the (still
internally named `username`) field, treats it as an email address, and looks
the account up by `email` instead.

`email` has no uniqueness constraint on this model (several existing rows
share addresses — support@sociallightbw.com alone has three), so this tries
every account with a matching address and returns the first whose password is
correct, rather than assuming a single match. django.contrib.auth still tries
ModelBackend afterwards (see AUTHENTICATION_BACKENDS), so a caller that still
has a real username to hand — the Django admin login, most notably — is
unaffected.
"""
from django.contrib.auth.backends import ModelBackend

from .models import User


class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = (username or kwargs.get('email') or '').strip()
        if not identifier or not password:
            return None

        for user in User.objects.filter(email__iexact=identifier):
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        return None
