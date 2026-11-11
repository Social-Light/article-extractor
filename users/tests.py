"""Tests for the social-light-platform single sign-on handoff
(sso_consume, users/views.py) and the demo upload cap it enables
(organisations/views.py:upload_newspaper).

No socket is opened and nothing here depends on the real
EXTRACTOR_SSO_SECRET value beyond it being *some* non-empty string — these
tests sign their own tokens with whatever key is actually configured, the
same way the real handoff does.
"""
from unittest import mock

from django.conf import settings
from django.core import signing
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from .models import ConsumedSsoToken, User
from .views import SSO_HANDOFF_TTL_SECONDS, SSO_SIGNING_SALT
from organisations.models import NewspaperUpload, Organisation


def _signer():
    return signing.TimestampSigner(key=settings.EXTRACTOR_SSO_SECRET, salt=SSO_SIGNING_SALT)


def _token(email='agent@example.com', org_id='11111111-1111-1111-1111-111111111111',
          org_name='Handoff Org', plan='active', nonce='fixed-nonce-1', signed_at=None):
    payload = {'email': email, 'org_id': org_id, 'org_name': org_name, 'plan': plan, 'nonce': nonce}
    signer = _signer()
    if signed_at is None:
        return signer.sign_object(payload)
    with mock.patch('django.core.signing.time.time', return_value=signed_at):
        return signer.sign_object(payload)


class SsoConsumeTests(TransactionTestCase):

    def test_a_valid_token_creates_an_agency_account_and_logs_in(self):
        token = _token(email='new.agent@client.com', plan='active')
        response = self.client.get(reverse('sso_consume', args=[token]))
        self.assertRedirects(response, reverse('dashboard:index'))

        user = User.objects.get(email__iexact='new.agent@client.com')
        self.assertEqual(user.role, User.ROLE_AGENCY)
        self.assertTrue(user.is_active)
        self.assertTrue(user.email_verified)
        self.assertEqual(user.social_light_org_id, '11111111-1111-1111-1111-111111111111')
        self.assertIn('_auth_user_id', self.client.session)

    def test_a_trial_plan_is_provisioned_as_source_not_agency(self):
        token = _token(email='trial.person@client.com', plan='trial')
        self.client.get(reverse('sso_consume', args=[token]))

        user = User.objects.get(email__iexact='trial.person@client.com')
        self.assertEqual(user.role, User.ROLE_SOURCE)

    def test_a_trial_login_provisions_an_organisation(self):
        token = _token(email='trial.org@client.com', org_id='trial-org-1',
                       org_name='Trial Client Co', plan='trial')
        self.client.get(reverse('sso_consume', args=[token]))

        org = Organisation.objects.get(social_light_org_id='trial-org-1')
        self.assertEqual(org.name, 'Trial Client Co')

    def test_a_paid_login_does_not_provision_an_organisation(self):
        """An Agency account already reaches every admin-created Organisation
        and needs no auto-provisioned one of its own."""
        token = _token(email='paid.person@client.com', org_id='paid-org-1', plan='active')
        self.client.get(reverse('sso_consume', args=[token]))

        self.assertFalse(Organisation.objects.filter(social_light_org_id='paid-org-1').exists())

    def test_two_members_of_the_same_trial_org_share_one_organisation(self):
        first = _token(email='member.one@client.com', org_id='shared-org-1',
                       org_name='Shared Co', plan='trial', nonce='nonce-shared-1')
        self.client.get(reverse('sso_consume', args=[first]))
        self.client.logout()

        second = _token(email='member.two@client.com', org_id='shared-org-1',
                        org_name='Shared Co', plan='trial', nonce='nonce-shared-2')
        self.client.get(reverse('sso_consume', args=[second]))

        self.assertEqual(Organisation.objects.filter(social_light_org_id='shared-org-1').count(), 1)

    def test_a_name_collision_is_suffixed_rather_than_failing_login(self):
        Organisation.objects.create(name='Duplicate Name Co')
        token = _token(email='collider@client.com', org_id='collide-org-1',
                       org_name='Duplicate Name Co', plan='trial')
        response = self.client.get(reverse('sso_consume', args=[token]))

        self.assertEqual(response.status_code, 302)  # login still succeeds
        org = Organisation.objects.get(social_light_org_id='collide-org-1')
        self.assertNotEqual(org.name, 'Duplicate Name Co')
        self.assertIn('Duplicate Name Co', org.name)

    def test_an_existing_account_is_upgraded_rather_than_duplicated(self):
        existing = User.objects.create_user(
            username='already.here@client.com', email='already.here@client.com',
            password='irrelevant-1', role=User.ROLE_SOURCE, is_active=False)

        token = _token(email='already.here@client.com', plan='active', nonce='nonce-upgrade')
        self.client.get(reverse('sso_consume', args=[token]))

        existing.refresh_from_db()
        self.assertEqual(existing.role, User.ROLE_AGENCY)
        self.assertTrue(existing.is_active)
        self.assertEqual(User.objects.filter(email__iexact='already.here@client.com').count(), 1)

    def test_a_tampered_token_is_refused(self):
        token = _token(nonce='nonce-tamper')
        tampered = token[:-1] + ('a' if token[-1] != 'a' else 'b')
        response = self.client.get(reverse('sso_consume', args=[tampered]))
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_an_expired_token_is_refused(self):
        import time
        old = time.time() - SSO_HANDOFF_TTL_SECONDS - 5
        token = _token(nonce='nonce-expired', signed_at=old)
        response = self.client.get(reverse('sso_consume', args=[token]))
        self.assertEqual(response.status_code, 400)

    def test_a_replayed_token_is_refused_even_within_its_window(self):
        token = _token(email='once.only@client.com', nonce='nonce-replay')
        first = self.client.get(reverse('sso_consume', args=[token]))
        self.assertEqual(first.status_code, 302)

        self.client.logout()
        second = self.client.get(reverse('sso_consume', args=[token]))
        self.assertEqual(second.status_code, 400)
        self.assertEqual(ConsumedSsoToken.objects.filter(nonce='nonce-replay').count(), 1)

    def test_a_malformed_plan_claim_is_refused(self):
        token = _token(plan='enterprise', nonce='nonce-badplan')
        response = self.client.get(reverse('sso_consume', args=[token]))
        self.assertEqual(response.status_code, 400)


class DemoUploadCapTests(TransactionTestCase):
    """The 3-newspaper limit — enforced in organisations/views.py, scoped by
    social_light_org_id, and only for the Source role (Agency accounts, and
    ordinary self-registered Source accounts with no linked org, are never
    capped)."""

    ORG = '22222222-2222-2222-2222-222222222222'

    def _demo_user(self, email):
        user = User.objects.create_user(
            username=email, email=email, password='pw-for-tests-1',
            role=User.ROLE_SOURCE, is_active=True)
        user.social_light_org_id = self.ORG
        user.save()
        return user

    def _upload(self, user, filename):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(user)
        return self.client.post(
            reverse('organisations:upload_newspaper'),
            {'file': SimpleUploadedFile(filename, b'not a real pdf')},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_three_uploads_are_allowed_a_fourth_is_refused(self):
        user = self._demo_user('demo1@client.com')
        for i in range(3):
            response = self._upload(user, f'paper{i}.pdf')
            self.assertTrue(response.json()['success'], response.json())

        fourth = self._upload(user, 'paper4.pdf')
        body = fourth.json()
        self.assertFalse(body['success'])
        self.assertIn('3-newspaper upload limit', body['errors'][0])

    def test_the_cap_is_shared_across_the_whole_org_not_per_user(self):
        first_user = self._demo_user('demo.a@client.com')
        second_user = self._demo_user('demo.b@client.com')

        for i in range(3):
            self._upload(first_user, f'shared{i}.pdf')

        response = self._upload(second_user, 'shared_over.pdf')
        self.assertFalse(response.json()['success'])

    def test_an_ordinary_independent_source_account_is_never_capped(self):
        """No social_light_org_id — an unrelated newspaper-publisher account
        that self-registered directly on the extractor, nothing to do with
        social-light-platform."""
        user = User.objects.create_user(
            username='publisher@independent.com', email='publisher@independent.com',
            password='pw-for-tests-1', role=User.ROLE_SOURCE, is_active=True)

        for i in range(5):
            response = self._upload(user, f'independent{i}.pdf')
            self.assertTrue(response.json()['success'])

    def test_an_agency_account_is_never_capped(self):
        user = self._demo_user('agency.demo@client.com')
        user.role = User.ROLE_AGENCY
        user.save()

        for i in range(5):
            response = self._upload(user, f'agency{i}.pdf')
            self.assertTrue(response.json()['success'])
