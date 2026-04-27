from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.crypto import get_random_string
from datetime import timedelta
from django.utils import timezone


class User(AbstractUser):
    """Platform user.

    Roles:
      source  — newspaper upload account (default).  No extraction access.
      agency  — can run extractions and view orgs/publishers, but cannot
                create/edit/delete them and cannot see the sources list.
      admin   — full access via is_staff / is_superuser (unchanged).
    """

    ROLE_SOURCE = 'source'
    ROLE_AGENCY = 'agency'
    ROLE_CHOICES = [
        (ROLE_SOURCE, 'Newspaper Source'),
        (ROLE_AGENCY, 'Agency'),
    ]

    role  = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=ROLE_SOURCE,
        help_text='source = upload-only; agency = extraction access',
    )
    email_verified = models.BooleanField(default=False)
    email_verification_token = models.CharField(max_length=64, blank=True, null=True)
    password_reset_token = models.CharField(max_length=64, blank=True, null=True)
    password_reset_expires = models.DateTimeField(blank=True, null=True)

    @property
    def is_agency(self):
        return self.role == self.ROLE_AGENCY

    @property
    def is_admin(self):
        return self.is_staff or self.is_superuser

    def generate_verification_token(self):
        """Generate a new email verification token."""
        self.email_verification_token = get_random_string(64)
        self.save()
        return self.email_verification_token

    def verify_email(self):
        """Mark email as verified."""
        self.email_verified = True
        self.email_verification_token = None
        self.save()

    def generate_password_reset_token(self):
        """Generate a password reset token that expires in 24 hours."""
        self.password_reset_token = get_random_string(64)
        self.password_reset_expires = timezone.now() + timedelta(hours=24)
        self.save()
        return self.password_reset_token

    def clear_password_reset_token(self):
        """Clear the password reset token."""
        self.password_reset_token = None
        self.password_reset_expires = None
        self.save()

    def is_password_reset_token_valid(self):
        """Check if the password reset token is still valid."""
        if not self.password_reset_token or not self.password_reset_expires:
            return False
        return timezone.now() <= self.password_reset_expires

    def __str__(self):
        return self.username

    class Meta:
        db_table = 'users'
