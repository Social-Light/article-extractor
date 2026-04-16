from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db import models
from django.http import HttpResponse, FileResponse
from django.conf import settings
from datetime import datetime
import os
import csv
from article_extractor.extractor import ArticleExtractor

from .models import Organisation, KeywordCategory, Keyword, Publisher, NewspaperUpload, ExtractionJob, ExtractedArticle
from .forms import OrganisationForm, KeywordCategoryForm, KeywordForm, PublisherForm, BulkKeywordForm


# ==================== ORGANISATION CRUD (Admin only) ====================

@staff_member_required
def organisation_list(request):
    """List all organisations"""
    organisations = Organisation.objects.all().order_by('name')
    return render(request, 'organisations/list.html', {'organisations': organisations})


@staff_member_required
def organisation_create(request):
    """Create a new organisation"""
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description')
        
        if name:
            organisation = Organisation.objects.create(
                name=name,
                description=description
            )
            messages.success(request, f'Organization "{organisation.name}" created successfully!')
            return redirect('organisations:detail', organisation_id=organisation.id)
        else:
            messages.error(request, 'Organization name is required.')
            return redirect('organisations:list')
    
    return redirect('organisations:list')


@staff_member_required
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
    
    context = {
        'organisation': organisation,
        'categories': categories,
        'total_keywords': total_keywords,
        'total_articles': total_articles,
        'total_ave': total_ave,
    }
    return render(request, 'organisations/detail.html', context)


@staff_member_required
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


@staff_member_required
def organisation_delete(request, organisation_id):
    organisation = get_object_or_404(Organisation, id=organisation_id)
    if request.method == 'POST':
        organisation.delete()
        messages.success(request, 'Organisation deleted!')
        return redirect('organisations:list')
    return render(request, 'organisations/delete.html', {'organisation': organisation})


# ==================== KEYWORD CATEGORIES (Admin only) ====================

@staff_member_required
def keyword_categories(request, organisation_id):
    organisation = get_object_or_404(Organisation, id=organisation_id)
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
        'form': form
    })


@staff_member_required
def edit_category(request, category_id):
    category = get_object_or_404(KeywordCategory, id=category_id)
    if request.method == 'POST':
        form = KeywordCategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, 'Category updated!')
            return redirect('organisations:keyword_categories', organisation_id=category.organisation.id)
    else:
        form = KeywordCategoryForm(instance=category)
    return render(request, 'organisations/edit_category.html', {'form': form, 'category': category})


@staff_member_required
def delete_category(request, category_id):
    category = get_object_or_404(KeywordCategory, id=category_id)
    organisation_id = category.organisation.id
    category.delete()
    messages.success(request, 'Category deleted!')
    return redirect('organisations:keyword_categories', organisation_id=organisation_id)


@staff_member_required
def category_keywords(request, category_id):
    category = get_object_or_404(KeywordCategory, id=category_id)
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
        'bulk_form': bulk_form
    })


@staff_member_required
def delete_keyword(request, keyword_id):
    keyword = get_object_or_404(Keyword, id=keyword_id)
    category_id = keyword.category.id
    keyword.delete()
    messages.success(request, 'Keyword deleted!')
    return redirect('organisations:category_keywords', category_id=category_id)


@staff_member_required
def toggle_keyword(request, keyword_id):
    keyword = get_object_or_404(Keyword, id=keyword_id)
    keyword.is_active = not keyword.is_active
    keyword.save()
    messages.success(request, f'Keyword "{keyword.term}" {"activated" if keyword.is_active else "deactivated"}')
    return redirect('organisations:category_keywords', category_id=keyword.category.id)


# ==================== PUBLISHER MANAGEMENT (Admin only) ====================

@staff_member_required
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


@staff_member_required
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


@staff_member_required
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
    """Upload a newspaper PDF"""
    publishers = Publisher.objects.all()
    
    if request.method == 'POST':
        pdf_file = request.FILES.get('pdf_file')
        publisher_id = request.POST.get('publisher')
        publication_date = request.POST.get('publication_date')
        
        if not pdf_file or not pdf_file.name.endswith('.pdf'):
            messages.error(request, 'Please upload a valid PDF file.')
            return redirect('organisations:upload_newspaper')
        
        # Save file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{pdf_file.name}"
        filepath = os.path.join(settings.MEDIA_ROOT, 'uploads', filename)
        
        with open(filepath, 'wb+') as f:
            for chunk in pdf_file.chunks():
                f.write(chunk)
        
        publisher = get_object_or_404(Publisher, id=publisher_id) if publisher_id else None
        
        upload = NewspaperUpload.objects.create(
            user=request.user,
            file_name=filename,
            file_path=filepath,
            file_size=pdf_file.size,
            publisher=publisher,
            publisher_name=publisher.name if publisher else None,
            publication_date=publication_date or None,
            status='pending'
        )
        
        messages.success(request, f'Newspaper "{upload.file_name}" uploaded successfully!')
        return redirect('organisations:my_uploads')
    
    return render(request, 'organisations/upload.html', {'publishers': publishers})


@login_required
def my_uploads(request):
    """View user's own uploads"""
    uploads = NewspaperUpload.objects.filter(user=request.user).order_by('-uploaded_at')
    
    # Summary by publisher
    publisher_summary = uploads.values('publisher_name').annotate(
        total=models.Count('id'),
        monthly=models.Count('id', filter=models.Q(uploaded_at__month=datetime.now().month))
    ).order_by('-total')
    
    context = {
        'uploads': uploads,
        'publisher_summary': publisher_summary,
        'total_uploads': uploads.count(),
        'monthly_uploads': uploads.filter(uploaded_at__month=datetime.now().month).count(),
    }
    return render(request, 'organisations/my_uploads.html', context)


@login_required
def upload_detail(request, upload_id):
    """View details of a specific upload"""
    upload = get_object_or_404(NewspaperUpload, id=upload_id, user=request.user)
    articles = upload.articles.all()
    return render(request, 'organisations/upload_detail.html', {'upload': upload, 'articles': articles})


# ==================== EXTRACTION (Admin only) ====================

@staff_member_required
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
        
        # Get uploads for that month
        uploads = NewspaperUpload.objects.filter(
            publication_date__year=year,
            publication_date__month=month_num,
            status='pending'
        )
        
        if not uploads.exists():
            messages.warning(request, f'No pending uploads found for {month}')
            return redirect('organisations:run_extraction')
        
        # Create extraction job
        job = ExtractionJob.objects.create(
            organisation=organisation,
            month=month,
            status='running',
            run_by=request.user
        )
        job.newspaper_uploads.set(uploads)
        
        total_articles = 0
        
        try:
            # Initialize extractor
            extractor = ArticleExtractor(settings.MEDIA_ROOT, organisation)
            
            # Process each upload
            for upload in uploads:
                print(f"\nProcessing: {upload.file_name}")
                
                # Extract articles
                file_path = upload.file_path
                articles_data = extractor.extract_articles(file_path)
                
                for data in articles_data:
    # Get or create publisher
                    publisher, _ = Publisher.objects.get_or_create(
                        name=data['publisher_name'],
                        defaults={'reach': data['reach']}
                    )
                    
                    # Convert pages list to comma-separated string
                    pages_str = ','.join(str(p) for p in data['pages']) if isinstance(data['pages'], list) else str(data['page'])
                    
                    ExtractedArticle.objects.create(
                        organisation=organisation,
                        extraction_job=job,
                        newspaper_upload=upload,
                        title=data['title'],
                        publisher=publisher,
                        publisher_name=data['publisher_name'],
                        section=data['section'],
                        publication_date=data['publication_date'],
                        page=data['page'],
                        pages=pages_str,
                        reach=data['reach'],
                        ave=data['ave'],
                        author=data['author'],
                        sentiment=data['sentiment'],
                        keywords_matched=data['keywords_matched'],
                        screenshot_path=data['screenshot_path'],
                        report_path=data['report_path'],
                    )
                    total_articles += 1
                
                # Update upload status
                upload.status = 'processed'
                upload.processed_at = datetime.now()
                upload.save()
            
            # Update job - MARK AS COMPLETED
            job.status = 'completed'
            job.articles_found = total_articles
            job.completed_at = datetime.now()
            job.save()
            
            messages.success(request, f'Extraction completed! Found {total_articles} articles.')
            
        except Exception as e:
            job.status = 'failed'
            job.error_message = str(e)
            job.save()
            messages.error(request, f'Extraction failed: {str(e)}')
            print(f"Error details: {e}")
        
        return redirect('organisations:extraction_results', job_id=job.id)
    
    return render(request, 'organisations/run_extraction.html', {'organisations': organisations})


@staff_member_required
def extraction_jobs(request):
    """View all extraction jobs"""
    jobs = ExtractionJob.objects.all().order_by('-started_at')
    
    context = {
        'jobs': jobs,
    }
    return render(request, 'organisations/extraction_jobs.html', context)

@staff_member_required
def rerun_extraction(request, job_id):
    """Rerun a failed extraction job"""
    original_job = get_object_or_404(ExtractionJob, id=job_id)
    
    # Only allow rerunning failed jobs
    if original_job.status != 'failed':
        messages.warning(request, 'Only failed jobs can be rerun.')
        return redirect('organisations:extraction_results', job_id=job_id)
    
    # Create new extraction job
    new_job = ExtractionJob.objects.create(
        organisation=original_job.organisation,
        month=original_job.month,
        status='running',
        run_by=request.user
    )
    
    # Copy the newspaper uploads
    new_job.newspaper_uploads.set(original_job.newspaper_uploads.all())
    
    total_articles = 0
    
    try:
        # Initialize extractor
        extractor = ArticleExtractor(settings.MEDIA_ROOT, original_job.organisation)
        
        # Process each upload
        for upload in new_job.newspaper_uploads.all():
            print(f"\nRerunning extraction for: {upload.file_name}")
            
            # Reset upload status to pending for reprocessing
            upload.status = 'pending'
            upload.save()
            
            # Extract articles
            file_path = upload.file_path
            articles_data = extractor.extract_articles(file_path)
            
            # Delete any existing articles for this upload from the original job
            ExtractedArticle.objects.filter(newspaper_upload=upload, organisation=original_job.organisation).delete()
            
            # Save new articles to database
            for data in articles_data:
                # Get or create publisher
                publisher, _ = Publisher.objects.get_or_create(
                    name=data['publisher_name'],
                    defaults={'reach': data['reach']}
                )
                
                ExtractedArticle.objects.create(
                    organisation=original_job.organisation,
                    extraction_job=new_job,
                    newspaper_upload=upload,
                    title=data['title'],
                    publisher=publisher,
                    publisher_name=data['publisher_name'],
                    section=data['section'],
                    publication_date=data['publication_date'],
                    page=data['page'],
                    pages=','.join(str(p) for p in data['pages']),
                    reach=data['reach'],
                    ave=data['ave'],
                    author=data['author'],
                    sentiment=data['sentiment'],
                    keywords_matched=data['keywords_matched'],
                    screenshot_path=data['screenshot_path'],
                    report_path=data['report_path'],
                )
                total_articles += 1
            
            # Update upload status
            upload.status = 'processed'
            upload.processed_at = datetime.now()
            upload.save()
        
        # Update new job
        new_job.status = 'completed'
        new_job.articles_found = total_articles
        new_job.completed_at = datetime.now()
        new_job.save()
        
        messages.success(request, f'Extraction rerun completed! Found {total_articles} articles.')
        
    except Exception as e:
        new_job.status = 'failed'
        new_job.error_message = str(e)
        new_job.save()
        messages.error(request, f'Extraction rerun failed: {str(e)}')
    
    return redirect('organisations:extraction_results', job_id=new_job.id)

@staff_member_required
def extraction_results(request, job_id):
    """View extraction results"""
    job = get_object_or_404(ExtractionJob, id=job_id)
    articles = job.articles.all()
    
    # Calculate totals
    total_ave = articles.aggregate(total=models.Sum('ave'))['total'] or 0
    avg_ave = articles.aggregate(avg=models.Avg('ave'))['avg'] or 0
    
    context = {
        'job': job,
        'articles': articles,
        'total_ave': total_ave,
        'avg_ave': avg_ave,
    }
    return render(request, 'organisations/extraction_results.html', context)


@staff_member_required
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

@staff_member_required
def all_articles(request):
    """View all extracted articles (admin only)"""
    articles = ExtractedArticle.objects.all().order_by('-created_at')
    
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


@login_required
def article_detail(request, article_id):
    """View article details"""
    article = get_object_or_404(ExtractedArticle, id=article_id)
    return render(request, 'organisations/article_detail.html', {'article': article})


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


@staff_member_required
def export_articles_csv(request):
    """Export articles to CSV"""
    articles = ExtractedArticle.objects.all().order_by('-created_at')
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="articles_export_{datetime.now().strftime("%Y%m%d")}.csv"'
    
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


@staff_member_required
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
            ),
            status='pending'
        )
        
        if not uploads.exists():
            messages.warning(request, f'No pending uploads found for {month}. Check that files are marked as pending and have a publication date or were uploaded in that month.')
            return redirect('organisations:run_extraction')
        
        # Create extraction job
        job = ExtractionJob.objects.create(
            organisation=organisation,
            month=month,
            status='running',
            run_by=request.user
        )
        job.newspaper_uploads.set(uploads)
        
        total_articles = 0
        
        try:
            # Initialize extractor (it will load publishers from database automatically)
            extractor = ArticleExtractor(settings.MEDIA_ROOT, organisation)
            
            # Process each upload
            for upload in uploads:
                print(f"\nProcessing: {upload.file_name}")
                
                # Extract articles
                file_path = upload.file_path
                articles_data = extractor.extract_articles(file_path)
                
                # Save articles to database
                for data in articles_data:
                    # Get or create publisher (should already exist from form)
                    publisher, _ = Publisher.objects.get_or_create(
                        name=data['publisher_name'],
                        defaults={'reach': data['reach']}
                    )
                    
                    ExtractedArticle.objects.create(
                        organisation=organisation,
                        extraction_job=job,
                        newspaper_upload=upload,
                        title=data['title'],
                        publisher=publisher,
                        publisher_name=data['publisher_name'],
                        section=data['section'],
                        publication_date=data['publication_date'],
                        page=data['page'],
                        pages=','.join(str(p) for p in data['pages']),
                        reach=data['reach'],
                        ave=data['ave'],
                        author=data['author'],
                        sentiment=data['sentiment'],
                        keywords_matched=data['keywords_matched'],
                        keyword_categories_matched=data['keyword_categories'],
                        source_file=data['source_file'],
                        screenshot_path=data['screenshot_path'],
                        report_path=data['report_path'],
                    )
                    total_articles += 1
                
                # Update upload status
                upload.status = 'processed'
                upload.processed_at = datetime.now()
                upload.save()
            
            # Update job
            job.status = 'completed'
            job.articles_found = total_articles
            job.completed_at = datetime.now()
            job.save()
            
            messages.success(request, f'Extraction completed! Found {total_articles} articles.')
            
        except Exception as e:
            job.status = 'failed'
            job.error_message = str(e)
            job.save()
            messages.error(request, f'Extraction failed: {str(e)}')
        
        return redirect('organisations:extraction_results', job_id=job.id)
    
    return render(request, 'organisations/run_extraction.html', {'organisations': organisations})