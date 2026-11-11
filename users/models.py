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

    # Set only by the social-light-platform single sign-on handoff
    # (sso_consume, below) — blank for every ordinary self-registered Source
    # account and every admin-created Agency account, which is what keeps
    # those unlimited: the 3-newspaper demo cap only ever applies to an
    # account this field is set on. Refreshed on every handoff, so a trial
    # org that upgrades to paid (role flips to Agency) or an account that
    # somehow lands on this from a different org is never stale.
    social_light_org_id = models.CharField(max_length=64, blank=True, default='', db_index=True)

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


class ConsumedSsoToken(models.Model):
    """Marks one social-light-platform handoff token as spent.

    The token itself (users/views.py:sso_consume) is already signed and
    expires in 60 seconds, but that alone allows replay within the window —
    a captured URL (a log line, a proxy, browser history) could otherwise log
    someone in a second time. Recording the nonce here, before the login
    happens, closes that: a repeat attempt with the same token is refused
    even inside its 60 seconds. Old rows are harmless to keep — nothing reads
    them once they exist — but they age out with the token's own expiry, so
    a periodic cleanup is a nice-to-have, not a correctness requirement.
    """
    nonce = models.CharField(max_length=64, unique=True)
    consumed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nonce
