"""
article_extractor/api.py
─────────────────────────
Headless (non-browser, non-session) API surface for this service.

Everything else in this project is a Django template/session-authenticated HTML
view (see organisations/views.py) — this module is the one exception, added so
another service (media-monitor's print-cover pipeline) can trigger OCR
extraction over HTTP without a logged-in browser session or CSRF token.
"""
import os

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from organisations.models import Organisation, NewspaperUpload, ExtractedArticle
from organisations.views import _extract_single_upload, _to_media_relative
from users.models import User

# Owns the NewspaperUpload audit rows this endpoint creates. NewspaperUpload.user
# is a required FK, but nothing here is ever meant to log in as this account —
# inactive, unusable password, source role (no extraction-UI access either).
_SERVICE_USERNAME = 'print-cover-pipeline'


def _service_user():
    user, created = User.objects.get_or_create(
        username=_SERVICE_USERNAME,
        defaults={'role': User.ROLE_SOURCE, 'is_active': False},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
    return user


@csrf_exempt
@require_http_methods(['POST'])
def extract_cover(request):
    """
    Headless single-image OCR extraction, for automated callers only.

    Multipart form fields: organisation_id (required, must already exist here —
    see the print-cover pipeline plan's one-time provisioning step),
    extraction_type ('PR' or 'Ad', default 'PR'), publication_date (optional,
    'YYYY-MM-DD'). File field: image (required).

    Security: requests must include X-Extractor-Shared-Secret matching
    settings.PRINT_COVER_EXTRACT_SECRET (ignored when secret is empty). This is
    a distinct secret from anything the platform/media-monitor otherwise share —
    it authenticates a completely separate service-to-service hop.

    Returns the OCR result(s) as JSON on success: {"ok": true, "articles": [...]}
    (list because a single image can, in principle, contain more than one
    keyword-matched article, same as process_file's normal return shape).
    """
    secret = settings.PRINT_COVER_EXTRACT_SECRET
    if secret and request.headers.get('X-Extractor-Shared-Secret') != secret:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    image = request.FILES.get('image')
    if not image:
        return JsonResponse({'error': 'Missing image file'}, status=400)

    org_id = request.POST.get('organisation_id')
    if not org_id:
        return JsonResponse({'error': 'Missing organisation_id'}, status=400)
    try:
        organisation = Organisation.objects.get(id=org_id)
    except (Organisation.DoesNotExist, ValueError):
        return JsonResponse({'error': f'Organisation {org_id!r} not found'}, status=404)

    extraction_type = (request.POST.get('extraction_type') or 'PR').strip()
    if extraction_type.lower() == 'ad':
        extraction_type = 'Ad'
    else:
        extraction_type = 'PR'
    publication_date = (request.POST.get('publication_date') or '').strip() or None

    # Save the upload the same way upload_newspaper does — a real file on disk,
    # a NewspaperUpload row pointing at it. process_file() only needs a plain
    # filesystem path, so this is the entire "make it look like a normal
    # upload" step; no browser/session machinery involved from here on.
    upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{timestamp}_{image.name}"
    filepath = os.path.join(upload_dir, filename)
    with open(filepath, 'wb+') as f:
        for chunk in image.chunks():
            f.write(chunk)

    upload = NewspaperUpload.objects.create(
        user=_service_user(),
        file_name=filename,
        file_path=filepath,
        file_size=image.size,
        publication_date=publication_date,
        status='pending',
    )

    try:
        _, articles_data, publisher_id = _extract_single_upload(
            upload.id, settings.MEDIA_ROOT, organisation.id, extraction_type,
        )
    except Exception as exc:
        return JsonResponse({'error': f'Extraction failed: {exc}'}, status=500)

    # Persist ExtractedArticle rows for this service's own dashboard/audit
    # visibility, exactly as _run_extraction_for_job does for a normal job —
    # just with extraction_job=None (already nullable) since there's no
    # ExtractionJob batch here, only a single ad-hoc image.
    saved = []
    for data in articles_data:
        article = ExtractedArticle.objects.create(
            organisation=organisation,
            extraction_job=None,
            newspaper_upload_id=upload.id,
            title=data['title'],
            publisher_id=publisher_id,
            publisher_name=data.get('publisher_name', ''),
            section=data.get('section', ''),
            publication_date=data.get('publication_date', ''),
            page=data.get('page', 1),
            pages=','.join(str(p) for p in data.get('pages', [])),
            reach=data.get('reach', 0),
            ave=data.get('ave', 0),
            author=data.get('author', ''),
            sentiment=data.get('sentiment', 'Neutral'),
            extraction_type=data.get('extraction_type', extraction_type),
            keywords_matched=data.get('keywords_matched', ''),
            screenshot_path=_to_media_relative(data.get('screenshot_path')),
            report_path=_to_media_relative(data.get('report_path')),
        )
        saved.append({
            'title': article.title,
            'section': article.section,
            'author': article.author,
            'sentiment': article.sentiment,
            'publication_date': article.publication_date,
            'keywords_matched': article.keywords_matched,
            'reach': article.reach,
            'ave': str(article.ave),
        })

    return JsonResponse({'ok': True, 'articles': saved})
