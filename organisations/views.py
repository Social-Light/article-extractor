from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import HttpResponse, FileResponse, JsonResponse
from django.urls import reverse
from django.conf import settings
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import csv
import threading
from django.utils import timezone
from article_extractor.extractor import ArticleExtractor

from .models import Organisation, KeywordCategory, Keyword, Publisher, NewspaperUpload, ExtractionJob, ExtractedArticle
from .forms import OrganisationForm, KeywordCategoryForm, KeywordForm, PublisherForm, BulkKeywordForm
from users.utils import admin_required, agency_or_admin_required


def _user_can_manage_keywords(request, organisation):
    return (
        request.user.is_staff
        or request.user.is_superuser
        or (request.user.is_agency and organisation.created_by_id == request.user.id)
    )


def _get_job_for_user(request, job_id):
    job = get_object_or_404(ExtractionJob, id=job_id)
    if request.user.is_agency and job.run_by_id != request.user.id:
        raise PermissionDenied
    return job


# ==================== ORGANISATION CRUD ====================

@agency_or_admin_required
def organisation_list(request):
    """List all organisations"""
    organisations = Organisation.objects.all().order_by('name')
    return render(request, 'organisations/list.html', {'organisations': organisations})


@agency_or_admin_required
def organisation_create(request):
    """Create a new organisation"""
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description')
        
        if name:
            organisation = Organisation.objects.create(
                name=name,
                description=description,
                created_by=request.user
            )
            messages.success(request, f'Organization "{organisation.name}" created successfully!')
            return redirect('organisations:detail', organisation_id=organisation.id)
        else:
            messages.error(request, 'Organization name is required.')
            return redirect('organisations:list')
    
    return redirect('organisations:list')


@agency_or_admin_required
def organisation_detail(request, organisation_id):
    """View organisation details and statistics"""
    organisation = get_object_or_404(Organisation, id=organisation_id)
    categories = organisation.keyword_categories.all()
    
    # Count total keywords
    total_keywords = 0
    for cat in categories:
        total_keywords += cat.keywords.filter(is_active=True).count()
    
    # Get article statistics
    articles = ExtractedArticle.objects.filter(organisation=organisation)
    total_articles = articles.count()
    total_ave = articles.aggregate(total=models.Sum('ave'))['total'] or 0
    
    can_manage_keywords = (
        request.user.is_staff
        or request.user.is_superuser
        or (request.user.is_agency and organisation.created_by_id == request.user.id)
    )

    context = {
        'organisation': organisation,
        'categories': categories,
        'total_keywords': total_keywords,
        'total_articles': total_articles,
        'total_ave': total_ave,
        'can_manage_keywords': can_manage_keywords,
    }
    return render(request, 'organisations/detail.html', context)


@admin_required
def organisation_edit(request, organisation_id):
    organisation = get_object_or_404(Organisation, id=organisation_id)
    if request.method == 'POST':
        form = OrganisationForm(request.POST, instance=organisation)
        if form.is_valid():
            form.save()
            messages.success(request, 'Organisation updated!')
            return redirect('organisations:detail', organisation_id=organisation.id)
    else:
        form = OrganisationForm(instance=organisation)
    return render(request, 'organisations/edit.html', {'form': form, 'organisation': organisation})


@admin_required
def organisation_delete(request, organisation_id):
    organisation = get_object_or_404(Organisation, id=organisation_id)
    if request.method == 'POST':
        organisation.delete()
        messages.success(request, 'Organisation deleted!')
        return redirect('organisations:list')
    return render(request, 'organisations/delete.html', {'organisation': organisation})


# ==================== KEYWORD CATEGORIES (Admin only) ====================

@agency_or_admin_required
def keyword_categories(request, organisation_id):
    organisation = get_object_or_404(Organisation, id=organisation_id)
    if not _user_can_manage_keywords(request, organisation):
        raise PermissionDenied
    categories = organisation.keyword_categories.all()
    
    if request.method == 'POST':
        form = KeywordCategoryForm(request.POST)
        if form.is_valid():
            category = form.save(commit=False)
            category.organisation = organisation
            category.save()
            messages.success(request, f'Category "{category.name}" added!')
            return redirect('organisations:keyword_categories', organisation_id=organisation.id)
    else:
        form = KeywordCategoryForm()
    
    return render(request, 'organisations/categories.html', {
        'organisation': organisation,
        'categories': categories,
        'form': form,
        'can_manage': True,
    })


@agency_or_admin_required
def edit_category(request, category_id):
    category = get_object_or_404(KeywordCategory, id=category_id)
    if not _user_can_manage_keywords(request, category.organisation):
        raise PermissionDenied
    if request.method == 'POST':
        form = KeywordCategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, 'Category updated!')
            return redirect('organisations:keyword_categories', organisation_id=category.organisation.id)
    else:
        form = KeywordCategoryForm(instance=category)
    return render(request, 'organisations/edit_category.html', {'form': form, 'category': category})


@agency_or_admin_required
def delete_category(request, category_id):
    category = get_object_or_404(KeywordCategory, id=category_id)
    if not _user_can_manage_keywords(request, category.organisation):
        raise PermissionDenied
    organisation_id = category.organisation.id
    category.delete()
    messages.success(request, 'Category deleted!')
    return redirect('organisations:keyword_categories', organisation_id=organisation_id)


@agency_or_admin_required
def category_keywords(request, category_id):
    category = get_object_or_404(KeywordCategory, id=category_id)
    if not _user_can_manage_keywords(request, category.organisation):
        raise PermissionDenied
    keywords = category.keywords.filter(is_active=True)
    
    if request.method == 'POST':
        if 'bulk' in request.POST:
            bulk_form = BulkKeywordForm(request.POST)
            if bulk_form.is_valid():
                lines = [line.strip() for line in bulk_form.cleaned_data['keywords'].split('\n') if line.strip()]
                added = 0
                for term in lines:
                    _, created = Keyword.objects.get_or_create(category=category, term=term)
                    if created:
                        added += 1
                messages.success(request, f'Added {added} keywords!')
                return redirect('organisations:category_keywords', category_id=category.id)
        else:
            form = KeywordForm(request.POST)
            if form.is_valid():
                keyword = form.save(commit=False)
                keyword.category = category
                keyword.save()
                messages.success(request, f'Keyword "{keyword.term}" added!')
                return redirect('organisations:category_keywords', category_id=category.id)
    else:
        form = KeywordForm()
        bulk_form = BulkKeywordForm()
    
    return render(request, 'organisations/keywords.html', {
        'category': category,
        'keywords': keywords,
        'form': form,
        'bulk_form': bulk_form,
        'can_manage': True,
    })


@agency_or_admin_required
def delete_keyword(request, keyword_id):
    keyword = get_object_or_404(Keyword, id=keyword_id)
    if not _user_can_manage_keywords(request, keyword.category.organisation):
        raise PermissionDenied
    category_id = keyword.category.id
    keyword.delete()
    messages.success(request, 'Keyword deleted!')
    return redirect('organisations:category_keywords', category_id=category_id)


@agency_or_admin_required
def toggle_keyword(request, keyword_id):
    keyword = get_object_or_404(Keyword, id=keyword_id)
    if not _user_can_manage_keywords(request, keyword.category.organisation):
        raise PermissionDenied
    keyword.is_active = not keyword.is_active
    keyword.save()
    messages.success(request, f'Keyword "{keyword.term}" {"activated" if keyword.is_active else "deactivated"}')
    return redirect('organisations:category_keywords', category_id=keyword.category.id)


# ==================== PUBLISHER MANAGEMENT (Admin only) ====================

@agency_or_admin_required
def publisher_list(request):
    """List all publishers and add new ones"""
    publishers = Publisher.objects.all().order_by('name')
    
    if request.method == 'POST':
        name = request.POST.get('name')
        base_ave_rate = request.POST.get('base_ave_rate')
        reach = request.POST.get('reach')
        
        if name and base_ave_rate and reach:
            publisher = Publisher.objects.create(
                name=name,
                base_ave_rate=base_ave_rate,
                reach=reach
            )
            messages.success(request, f'Publisher "{name}" added successfully!')
            return redirect('organisations:publishers')
        else:
            messages.error(request, 'Please fill in all fields.')
    
    return render(request, 'organisations/publishers.html', {'publishers': publishers})


@admin_required
def edit_publisher(request, publisher_id):
    """Edit a publisher"""
    publisher = get_object_or_404(Publisher, id=publisher_id)
    
    if request.method == 'POST':
        name = request.POST.get('name')
        base_ave_rate = request.POST.get('base_ave_rate')
        reach = request.POST.get('reach')
        
        if name and base_ave_rate and reach:
            publisher.name = name
            publisher.base_ave_rate = base_ave_rate
            publisher.reach = reach
            publisher.save()
            messages.success(request, f'Publisher "{name}" updated successfully!')
            return redirect('organisations:publishers')
        else:
            messages.error(request, 'Please fill in all fields.')
    
    return render(request, 'organisations/edit_publisher.html', {'publisher': publisher})


@admin_required
def delete_publisher(request, publisher_id):
    """Delete a publisher"""
    publisher = get_object_or_404(Publisher, id=publisher_id)
    publisher_name = publisher.name
    publisher.delete()
    messages.success(request, f'Publisher "{publisher_name}" deleted successfully!')
    return redirect('organisations:publishers')

# ==================== NEWSPAPER UPLOADS (All users) ====================

@login_required
def upload_newspaper(request):
    """Upload one or more newspaper PDFs or images"""
    publishers = Publisher.objects.all()

    if request.method == 'POST':
        files = request.FILES.getlist('file')
        publisher_id = request.POST.get('publisher')
        publication_date = request.POST.get('publication_date')
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if not files:
            if is_ajax:
                return JsonResponse({'success': False, 'errors': ['No files selected.']})
            messages.error(request, 'Please select at least one file.')
            return redirect('organisations:upload_newspaper')

        publisher = get_object_or_404(Publisher, id=publisher_id) if publisher_id else None
        allowed_extensions = ['.pdf', '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp']
        upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)

        saved, errors = [], []

        for file in files:
            if not any(file.name.lower().endswith(ext) for ext in allowed_extensions):
                errors.append(f'"{file.name}" is not a supported file type.')
                continue

            if NewspaperUpload.objects.filter(user=request.user, file_name__endswith=f'_{file.name}').exists():
                errors.append(f'"{file.name}" has already been uploaded.')
                continue

            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{file.name}"
            filepath = os.path.join(upload_dir, filename)

            with open(filepath, 'wb+') as f:
                for chunk in file.chunks():
                    f.write(chunk)

            NewspaperUpload.objects.create(
                user=request.user,
                file_name=filename,
                file_path=filepath,
                file_size=file.size,
                publisher=publisher,
                publisher_name=publisher.name if publisher else None,
                publication_date=publication_date or None,
                status='pending',
            )
            saved.append(file.name)

        if is_ajax:
            return JsonResponse({
                'success': bool(saved),
                'saved': saved,
                'errors': errors,
                'redirect': reverse('organisations:my_uploads') if saved else None,
            })

        for err in errors:
            messages.error(request, err)
        if saved:
            messages.success(request, f'{len(saved)} file(s) uploaded successfully.')
        return redirect('organisations:my_uploads' if saved else 'organisations:upload_newspaper')

    return render(request, 'organisations/upload.html', {'publishers': publishers})


@login_required
def my_uploads(request):
    """View user's own uploads"""
    uploads = NewspaperUpload.objects.filter(user=request.user).order_by('-uploaded_at')
    
    now = timezone.now()
    publisher_summary = uploads.values('publisher_name').annotate(
        total=models.Count('id'),
        monthly=models.Count('id', filter=models.Q(uploaded_at__month=now.month))
    ).order_by('-total')

    context = {
        'uploads': uploads,
        'publisher_summary': publisher_summary,
        'total_uploads': uploads.count(),
        'monthly_uploads': uploads.filter(uploaded_at__month=now.month).count(),
    }
    return render(request, 'organisations/my_uploads.html', context)


@login_required
def upload_detail(request, upload_id):
    """View details of a specific upload"""
    if request.user.is_staff or request.user.is_superuser:
        upload = get_object_or_404(NewspaperUpload, id=upload_id)
    else:
        upload = get_object_or_404(NewspaperUpload, id=upload_id, user=request.user)
    articles = upload.articles.all()
    return render(request, 'organisations/upload_detail.html', {'upload': upload, 'articles': articles})


@login_required
def delete_upload(request, upload_id):
    """Delete a newspaper upload and its file"""
    if request.method != 'POST':
        return redirect('organisations:my_uploads')

    if request.user.is_staff or request.user.is_superuser:
        upload = get_object_or_404(NewspaperUpload, id=upload_id)
    else:
        upload = get_object_or_404(NewspaperUpload, id=upload_id, user=request.user)

    file_name = upload.file_name
    # Remove the physical file if it exists
    if upload.file_path and os.path.exists(upload.file_path):
        os.remove(upload.file_path)

    upload.delete()
    messages.success(request, f'"{file_name}" has been removed.')
    return redirect('organisations:my_uploads')


# ==================== EXTRACTION (Admin only) ====================

def _extract_single_upload(upload_id, media_root, organisation_id, extraction_type):
    """Process one upload in a worker thread. Returns (upload_id, articles_data, publisher_id)."""
    from django.db import close_old_connections
    close_old_connections()
    upload = NewspaperUpload.objects.select_related('publisher').get(id=upload_id)
    organisation = Organisation.objects.get(id=organisation_id)
    extractor = ArticleExtractor(media_root, organisation, extraction_type=extraction_type)
    print(f"\nProcessing: {upload.file_name}")
    articles_data = extractor.process_file(upload.file_path)

    # Use the publisher the user chose on upload — don't guess from text
    if upload.publisher:
        for data in articles_data:
            data['publisher_name'] = upload.publisher.name
            data['reach'] = upload.publisher.reach
            data['ave'] = extractor.calculate_ave(
                upload.publisher.name, data.get('page', 1), data.get('sentiment', 'neutral')
            )

    upload.status = 'processed'
    upload.processed_at = timezone.now()
    upload.save()
    return upload_id, articles_data, upload.publisher_id


def _run_extraction_for_job(job_id):
    """Run extraction in a background thread. Cleans up DB connections when done."""
    from django.db import close_old_connections
    close_old_connections()
    try:
        job = ExtractionJob.objects.get(id=job_id)
        job.status = 'running'
        job.save()

        organisation = job.organisation
        upload_ids = list(job.newspaper_uploads.values_list('id', flat=True))
        total_articles = 0
        max_workers = min(4, len(upload_ids))

        results = {}
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            futures = {
                executor.submit(
                    _extract_single_upload,
                    uid, settings.MEDIA_ROOT, organisation.id, job.extraction_type
                ): uid
                for uid in upload_ids
            }
            for future in as_completed(futures):
                uid, articles_data, publisher_id = future.result()
                results[uid] = (articles_data, publisher_id)

        for uid in upload_ids:
            articles_data, publisher_id = results.get(uid, ([], None))
            for data in articles_data:
                if publisher_id:
                    publisher = Publisher.objects.get(id=publisher_id)
                else:
                    publisher, _ = Publisher.objects.get_or_create(
                        name=data['publisher_name'],
                        defaults={'reach': data['reach']}
                    )
                ExtractedArticle.objects.create(
                    organisation=organisation,
                    extraction_job=job,
                    newspaper_upload_id=uid,
                    title=data['title'],
                    publisher=publisher,
                    publisher_name=publisher.name,
                    section=data['section'],
                    publication_date=data['publication_date'],
                    page=data['page'],
                    pages=','.join(str(p) for p in data['pages']),
                    reach=data['reach'],
                    ave=data['ave'],
                    author=data['author'],
                    sentiment=data['sentiment'],
                    extraction_type=data.get('extraction_type', job.extraction_type),
                    keywords_matched=data['keywords_matched'],
                    screenshot_path=data['screenshot_path'],
                    report_path=data['report_path'],
                )
                total_articles += 1

        job.status = 'completed'
        job.articles_found = total_articles
        job.completed_at = timezone.now()
        job.save()

    except Exception as e:
        try:
            job = ExtractionJob.objects.get(id=job_id)
            job.status = 'failed'
            job.error_message = str(e)
            job.save()
        except Exception:
            pass
        print(f"Background extraction error (job {job_id}): {e}")
    finally:
        close_old_connections()


@agency_or_admin_required
def extraction_jobs(request):
    """View all extraction jobs"""
    if request.user.is_agency:
        jobs = ExtractionJob.objects.filter(run_by=request.user).order_by('-started_at')
    else:
        jobs = ExtractionJob.objects.all().order_by('-started_at')
    
    context = {
        'jobs': jobs,
    }
    return render(request, 'organisations/extraction_jobs.html', context)

@agency_or_admin_required
def rerun_extraction(request, job_id):
    """Rerun a failed extraction job"""
    if request.method != 'POST':
        return redirect('organisations:extraction_results', job_id=job_id)
    
    original_job = _get_job_for_user(request, job_id)
    
    # Only allow rerunning failed jobs
    if original_job.status != 'failed':
        messages.warning(request, 'Only failed jobs can be rerun.')
        return redirect('organisations:extraction_results', job_id=job_id)
    
    # Create new extraction job
    new_job = ExtractionJob.objects.create(
        organisation=original_job.organisation,
        month=original_job.month,
        extraction_type=original_job.extraction_type,
        status='pending',
        run_by=request.user
    )
    new_job.newspaper_uploads.set(original_job.newspaper_uploads.all())

    # Delete articles from the previous failed run before starting fresh
    ExtractedArticle.objects.filter(
        newspaper_upload__in=original_job.newspaper_uploads.all(),
        organisation=original_job.organisation
    ).delete()

    threading.Thread(
        target=_run_extraction_for_job, args=(new_job.id,), daemon=True
    ).start()

    messages.info(request, 'Extraction rerun started. The page will refresh automatically while it runs.')
    return redirect('organisations:extraction_results', job_id=new_job.id)

@agency_or_admin_required
def extraction_results(request, job_id):
    """View extraction results"""
    job = _get_job_for_user(request, job_id)
    articles = list(job.articles.all())
    
    for article in articles:
        article.keywords_matched_list = []
        if article.keywords_matched:
            article.keywords_matched_list = [kw.strip() for kw in article.keywords_matched.split(',') if kw.strip()]
    
    # Calculate totals
    total_ave = sum(article.ave or 0 for article in articles)
    avg_ave = (sum(article.ave or 0 for article in articles) / len(articles)) if articles else 0
    
    context = {
        'job': job,
        'articles': articles,
        'total_ave': total_ave,
        'avg_ave': avg_ave,
    }
    return render(request, 'organisations/extraction_results.html', context)


@admin_required
def all_uploads(request):
    """View all uploads from all users (admin only)"""
    uploads = NewspaperUpload.objects.all().order_by('-uploaded_at')
    
    # Filter by month if provided
    month = request.GET.get('month')
    if month:
        year, month_num = month.split('-')
        uploads = uploads.filter(publication_date__year=int(year), publication_date__month=int(month_num))
    
    context = {
        'uploads': uploads,
        'current_month': request.GET.get('month', ''),
    }
    return render(request, 'organisations/all_uploads.html', context)


# ==================== ARTICLES ====================

@agency_or_admin_required
def all_articles(request):
    """View all extracted PR articles (admin only)"""
    articles = ExtractedArticle.objects.filter(extraction_type='PR').order_by('-created_at')
    if request.user.is_agency:
        articles = articles.filter(extraction_job__run_by=request.user)
    
    # Filter by organisation
    organisation_id = request.GET.get('organisation')
    if organisation_id:
        articles = articles.filter(organisation_id=organisation_id)
    
    organisations = Organisation.objects.all()
    
    context = {
        'articles': articles,
        'organisations': organisations,
        'selected_organisation': organisation_id,
    }
    return render(request, 'organisations/all_articles.html', context)


@agency_or_admin_required
def all_adverts(request):
    """View all extracted adverts (admin only)"""
    articles = ExtractedArticle.objects.filter(extraction_type='Ad').order_by('-created_at')
    if request.user.is_agency:
        articles = articles.filter(extraction_job__run_by=request.user)
    
    # Filter by organisation
    organisation_id = request.GET.get('organisation')
    if organisation_id:
        articles = articles.filter(organisation_id=organisation_id)
    
    organisations = Organisation.objects.all()
    
    context = {
        'articles': articles,
        'organisations': organisations,
        'selected_organisation': organisation_id,
    }
    return render(request, 'organisations/all_adverts.html', context)


@login_required
def article_detail(request, article_id):
    """View article details"""
    article = get_object_or_404(ExtractedArticle, id=article_id)

    screenshot_url = None
    if article.screenshot_path and os.path.exists(article.screenshot_path):
        rel = os.path.relpath(article.screenshot_path, str(settings.MEDIA_ROOT))
        screenshot_url = settings.MEDIA_URL + rel.replace(os.sep, '/')

    user_feedback = article.feedback.filter(submitted_by=request.user).first()

    return render(request, 'organisations/article_detail.html', {
        'article': article,
        'screenshot_url': screenshot_url,
        'user_feedback': user_feedback,
    })


@login_required
def submit_feedback(request, article_id):
    """Submit or update feedback for an extracted article to improve future extractions."""
    article = get_object_or_404(ExtractedArticle, id=article_id)

    if request.method != 'POST':
        return redirect('organisations:article_detail', article_id=article_id)

    verdict = request.POST.get('verdict')
    if verdict not in ('correct', 'partial', 'incorrect'):
        messages.error(request, 'Invalid feedback.')
        return redirect('organisations:article_detail', article_id=article_id)

    from .models import ExtractionFeedback

    ExtractionFeedback.objects.update_or_create(
        article=article,
        submitted_by=request.user,
        defaults={
            'verdict': verdict,
            'corrected_title': request.POST.get('corrected_title', '').strip(),
            'corrected_section': request.POST.get('corrected_section', '').strip(),
            'corrected_sentiment': request.POST.get('corrected_sentiment', '').strip(),
            'notes': request.POST.get('notes', '').strip(),
        }
    )

    try:
        from .learning import LearningEngine
        LearningEngine().update_from_feedback(article.organisation)
    except Exception as e:
        print(f"Learning model update failed: {e}")

    messages.success(request, 'Feedback saved — thank you!')
    return redirect('organisations:article_detail', article_id=article_id)


@login_required
def download_report(request, article_id):
    """Download PDF report for article"""
    article = get_object_or_404(ExtractedArticle, id=article_id)
    if article.report_path and os.path.exists(article.report_path):
        return FileResponse(
            open(article.report_path, 'rb'),
            as_attachment=True,
            filename=f"{article.organisation.name}_{article.title[:50]}_report.pdf"
        )
    messages.error(request, 'Report file not found.')
    return redirect('organisations:article_detail', article_id=article_id)


@agency_or_admin_required
def export_articles_csv(request):
    """Export articles to CSV"""
    articles = ExtractedArticle.objects.all().order_by('-created_at')
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="articles_export_{timezone.now().strftime("%Y%m%d")}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Title', 'Organisation', 'Publisher', 'Section', 'Publication Date', 'Page', 'Reach', 'AVE', 'Author', 'Sentiment', 'Keywords Matched'])
    
    for article in articles:
        writer.writerow([
            article.title,
            article.organisation.name,
            article.publisher_name,
            article.section,
            article.publication_date,
            article.page,
            article.reach,
            article.ave,
            article.author,
            article.sentiment,
            article.keywords_matched,
        ])
    
    return response


@agency_or_admin_required
def run_extraction(request):
    """Run extraction for a specific organisation and month"""
    organisations = Organisation.objects.all()
    
    if request.method == 'POST':
        organisation_id = request.POST.get('organisation_id')
        month = request.POST.get('month')
        
        organisation = get_object_or_404(Organisation, id=organisation_id)
        
        # Parse month
        try:
            year, month_num = month.split('-')
            year = int(year)
            month_num = int(month_num)
        except:
            messages.error(request, 'Invalid month format')
            return redirect('organisations:run_extraction')
        
        # Get uploads for that month - check both publication_date and uploaded_at
        from django.db.models import Q
        uploads = NewspaperUpload.objects.filter(
            Q(
                publication_date__year=year,
                publication_date__month=month_num
            ) | Q(
                publication_date__isnull=True,
                uploaded_at__year=year,
                uploaded_at__month=month_num
            )
        )

        if not uploads.exists():
            messages.warning(request, f'No uploads found for {month}. Check that files have a publication date or were uploaded in that month.')
            return redirect('organisations:run_extraction')

        # Exclude uploads already included in a completed job for this organisation.
        # We check completed jobs (not articles) so uploads that produced 0 articles
        # are still counted as done and won't be re-queued.
        already_processed_ids = (
            ExtractionJob.objects
            .filter(organisation=organisation, status='completed')
            .values_list('newspaper_uploads__id', flat=True)
            .distinct()
        )
        skipped_count = uploads.filter(id__in=already_processed_ids).count()
        uploads = uploads.exclude(id__in=already_processed_ids)

        if skipped_count:
            messages.info(request, f'{skipped_count} file(s) were already extracted for {organisation.name} and were skipped.')

        if not uploads.exists():
            messages.warning(request, f'All uploads for {month} have already been extracted for {organisation.name}. Use Rerun on an existing job to re-process them.')
            return redirect('organisations:run_extraction')
        
        extraction_type = request.POST.get('extraction_type', 'PR')
        if extraction_type not in ['PR', 'Ad']:
            extraction_type = 'PR'
        
        # Create extraction job and start it in a background thread so the
        # response returns immediately (avoids PythonAnywhere's 30s timeout).
        job = ExtractionJob.objects.create(
            organisation=organisation,
            month=month,
            extraction_type=extraction_type,
            status='pending',
            run_by=request.user
        )
        job.newspaper_uploads.set(uploads)

        threading.Thread(
            target=_run_extraction_for_job, args=(job.id,), daemon=True
        ).start()

        messages.info(
            request,
            f'Extraction started for {organisation.name} — {month}. '
            'The page will refresh automatically while it runs.'
        )
        return redirect('organisations:extraction_results', job_id=job.id)
    
    return render(request, 'organisations/run_extraction.html', {'organisations': organisations})