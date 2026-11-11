"""Tests for demo (trial) self-serve extraction — the parts of
organisations/views.py that exist specifically for a Source account
provisioned by the social-light-platform SSO handoff (users/views.py:
sso_consume): my_keywords, the demo branch of run_extraction, my_extracts,
its exports, push_to_platform, and organisations/sentiment_ai.py's
fail-safe fallback.

TransactionTestCase, not TestCase — this project's DB is remote Neon
Postgres, whose pooler doesn't tolerate the shared connection/nested-
transaction model TestCase relies on (see sociallight-article-extractor
memory / users/tests.py for the same reasoning already applied there).
"""
from unittest import mock

from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from users.models import User
from . import sentiment_ai
from .models import ExtractedArticle, ExtractionJob, Keyword, NewspaperUpload, Organisation


def _demo_user(email, org_id, org_name=None):
    user = User.objects.create_user(
        username=email, email=email, password='pw-for-tests-1',
        role=User.ROLE_SOURCE, is_active=True)
    user.social_light_org_id = org_id
    user.save()
    org, _ = Organisation.objects.get_or_create(
        social_light_org_id=org_id, defaults={'name': org_name or f'Org {org_id}'})
    return user, org


def _independent_source(email):
    return User.objects.create_user(
        username=email, email=email, password='pw-for-tests-1',
        role=User.ROLE_SOURCE, is_active=True)


class MyKeywordsTests(TransactionTestCase):

    def test_saving_keywords_creates_a_category_scoped_to_own_org(self):
        user, org = _demo_user('kw1@client.com', 'org-kw-1')
        self.client.force_login(user)
        response = self.client.post(reverse('organisations:my_keywords'),
                                    {'keywords': 'Acme Corp, Acme Bank\nJohn Doe'})
        self.assertEqual(response.status_code, 302)
        terms = set(Keyword.objects.filter(category__organisation=org).values_list('term', flat=True))
        self.assertEqual(terms, {'Acme Corp', 'Acme Bank', 'John Doe'})

    def test_saving_again_replaces_rather_than_appends(self):
        user, org = _demo_user('kw2@client.com', 'org-kw-2')
        self.client.force_login(user)
        self.client.post(reverse('organisations:my_keywords'), {'keywords': 'First Term'})
        self.client.post(reverse('organisations:my_keywords'), {'keywords': 'Second Term'})
        terms = set(Keyword.objects.filter(category__organisation=org).values_list('term', flat=True))
        self.assertEqual(terms, {'Second Term'})

    def test_an_ordinary_source_account_cannot_reach_it(self):
        user = _independent_source('ordinary1@indep.com')
        self.client.force_login(user)
        response = self.client.get(reverse('organisations:my_keywords'))
        self.assertEqual(response.status_code, 403)


class RunExtractionScopingTests(TransactionTestCase):

    def test_a_demo_source_can_only_ever_target_their_own_organisation(self):
        """A posted organisation_id from a different org is ignored outright
        — the job is created under the caller's own auto-provisioned org
        regardless of what was submitted."""
        user, org = _demo_user('ext1@client.com', 'org-ext-1')
        other_org = Organisation.objects.create(name='Someone Elses Org')
        NewspaperUpload.objects.create(
            user=user, file_name='a.pdf', file_path='/tmp/does-not-exist-a.pdf',
            file_size=10, publication_date='2026-08-10', status='pending')

        self.client.force_login(user)
        self.client.post(reverse('organisations:run_extraction'),
                         {'organisation_id': str(other_org.id), 'month': '2026-08',
                          'extraction_type': 'PR'})

        self.assertFalse(ExtractionJob.objects.filter(organisation=other_org).exists())
        job = ExtractionJob.objects.filter(organisation=org).first()
        self.assertIsNotNone(job)

    def test_another_users_upload_in_the_same_month_is_not_included(self):
        """Uploads carry no Organisation of their own — a real agency
        deliberately scans every upload for the org it picks, but a demo
        source must never reach into a different client's uploaded
        newspapers."""
        user, org = _demo_user('ext2@client.com', 'org-ext-2')
        other_user = _independent_source('other.uploader@indep.com')
        mine = NewspaperUpload.objects.create(
            user=user, file_name='mine.pdf', file_path='/tmp/does-not-exist-mine.pdf',
            file_size=10, publication_date='2026-08-11', status='pending')
        NewspaperUpload.objects.create(
            user=other_user, file_name='theirs.pdf', file_path='/tmp/does-not-exist-theirs.pdf',
            file_size=10, publication_date='2026-08-11', status='pending')

        self.client.force_login(user)
        self.client.post(reverse('organisations:run_extraction'),
                         {'organisation_id': str(org.id), 'month': '2026-08',
                          'extraction_type': 'PR'})

        job = ExtractionJob.objects.get(organisation=org)
        included = set(job.newspaper_uploads.values_list('id', flat=True))
        self.assertEqual(included, {mine.id})

    def test_an_ordinary_source_account_cannot_reach_run_extraction(self):
        user = _independent_source('ordinary2@indep.com')
        self.client.force_login(user)
        response = self.client.get(reverse('organisations:run_extraction'))
        self.assertEqual(response.status_code, 403)


class MyExtractsScopingTests(TransactionTestCase):

    def test_articles_are_scoped_to_the_callers_own_organisation(self):
        user, org = _demo_user('extracts1@client.com', 'org-extracts-1')
        other_org = Organisation.objects.create(name='Other Org For Extracts')
        mine = ExtractedArticle.objects.create(
            organisation=org, title='My article', publisher_name='Mmegi',
            section='Business', page=1, reach=1000, ave=500)
        ExtractedArticle.objects.create(
            organisation=other_org, title='Not mine', publisher_name='Mmegi',
            section='Business', page=1, reach=1000, ave=500)

        self.client.force_login(user)
        response = self.client.get(reverse('organisations:my_extracts'))
        self.assertContains(response, 'My article')
        self.assertNotContains(response, 'Not mine')

    def test_csv_export_never_leaks_another_orgs_articles(self):
        user, org = _demo_user('extracts2@client.com', 'org-extracts-2')
        other_org = Organisation.objects.create(name='Other Org For CSV')
        ExtractedArticle.objects.create(
            organisation=org, title='Mine CSV', publisher_name='Mmegi',
            section='Business', page=1, reach=1000, ave=500)
        ExtractedArticle.objects.create(
            organisation=other_org, title='Not Mine CSV', publisher_name='Mmegi',
            section='Business', page=1, reach=1000, ave=500)

        self.client.force_login(user)
        response = self.client.get(reverse('organisations:my_extracts_export_csv'))
        body = response.content.decode()
        self.assertIn('Mine CSV', body)
        self.assertNotIn('Not Mine CSV', body)


class PushToPlatformTests(TransactionTestCase):

    def test_a_repeat_push_is_a_safe_no_op(self):
        user, org = _demo_user('push1@client.com', 'org-push-1')
        article = ExtractedArticle.objects.create(
            organisation=org, title='Pushed already', publisher_name='Mmegi',
            section='Business', page=1, reach=1000, ave=500,
            pushed_at=timezone.now())

        self.client.force_login(user)
        with mock.patch('organisations.views.requests.post') as mock_post:
            self.client.post(reverse('organisations:push_to_platform', args=[article.id]))
        mock_post.assert_not_called()

    def test_cannot_push_another_orgs_article(self):
        user, org = _demo_user('push2@client.com', 'org-push-2')
        other_org = Organisation.objects.create(name='Other Org For Push')
        other_article = ExtractedArticle.objects.create(
            organisation=other_org, title='Not mine to push', publisher_name='Mmegi',
            section='Business', page=1, reach=1000, ave=500)

        self.client.force_login(user)
        response = self.client.post(
            reverse('organisations:push_to_platform', args=[other_article.id]))
        self.assertEqual(response.status_code, 404)


class SentimentAiFallbackTests(TransactionTestCase):
    """organisations/sentiment_ai.py itself — every failure mode must return
    None, never raise, so a caller always has a safe heuristic fallback."""

    @override_settings(GROQ_API_KEY='')
    def test_no_api_key_returns_none(self):
        self.assertIsNone(sentiment_ai.analyze_sentiment('Acme Corp', 'A headline'))
        self.assertIsNone(sentiment_ai.summarize_article('A headline'))

    @override_settings(GROQ_API_KEY='fake-key')
    def test_a_groq_exception_returns_none_not_raises(self):
        with mock.patch('groq.Groq') as mock_groq:
            mock_groq.return_value.chat.completions.create.side_effect = Exception('boom')
            result = sentiment_ai.analyze_sentiment('Acme Corp', 'A headline', 'A summary')
        self.assertIsNone(result)

    @override_settings(GROQ_API_KEY='fake-key')
    def test_blank_headline_and_summary_short_circuits_without_a_call(self):
        with mock.patch('groq.Groq') as mock_groq:
            result = sentiment_ai.analyze_sentiment('Acme Corp', '', '')
        mock_groq.assert_not_called()
        self.assertIsNone(result)