from datetime import datetime

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum

from organisations.models import NewspaperUpload, Organisation, ExtractedArticle, ExtractionJob
from users.models import User


@login_required
def index(request):
    user = request.user

    # ── Admin dashboard ───────────────────────────────────────────────────────
    if user.is_staff or user.is_superuser:
        total_uploads       = NewspaperUpload.objects.count()
        total_users         = User.objects.filter(is_staff=False, is_superuser=False, role=User.ROLE_SOURCE).count()
        total_agencies      = User.objects.filter(role=User.ROLE_AGENCY).count()
        total_organisations = Organisation.objects.count()
        total_articles      = ExtractedArticle.objects.count()
        recent_uploads      = NewspaperUpload.objects.all().order_by('-uploaded_at')[:10]
        organisations       = Organisation.objects.all()

        registered_users = User.objects.filter(is_staff=False, is_superuser=False, role=User.ROLE_SOURCE)
        user_data = []
        for u in registered_users:
            uploads   = NewspaperUpload.objects.filter(user=u)
            last_up   = uploads.order_by('-uploaded_at').first()
            user_data.append({
                'id':           u.id,
                'username':     u.username,
                'email':        u.email,
                'phone':        u.phone,
                'date_joined':  u.date_joined,
                'upload_count': uploads.count(),
                'last_upload':  last_up.uploaded_at if last_up else None,
            })

        context = {
            'is_admin':           True,
            'total_uploads':      total_uploads,
            'total_users':        total_users,
            'total_agencies':     total_agencies,
            'total_organisations':total_organisations,
            'total_articles':     total_articles,
            'recent_uploads':     recent_uploads,
            'organisations':      organisations,
            'registered_users':   user_data,
        }
        return render(request, 'dashboard/admin_dashboard.html', context)

    # ── Agency dashboard ──────────────────────────────────────────────────────
    if user.is_agency:
        recent_jobs     = ExtractionJob.objects.order_by('-started_at')[:8]
        total_articles  = ExtractedArticle.objects.count()
        total_orgs      = Organisation.objects.count()
        recent_articles = ExtractedArticle.objects.order_by('-created_at')[:8]

        context = {
            'is_agency':       True,
            'recent_jobs':     recent_jobs,
            'total_articles':  total_articles,
            'total_orgs':      total_orgs,
            'recent_articles': recent_articles,
        }
        return render(request, 'dashboard/index.html', context)

    # ── Source (upload-only) dashboard ────────────────────────────────────────
    total_uploads   = NewspaperUpload.objects.filter(user=user).count()
    monthly_uploads = NewspaperUpload.objects.filter(
        user=user,
        uploaded_at__month=datetime.now().month,
        uploaded_at__year=datetime.now().year,
    ).count()
    publisher_summary = (
        NewspaperUpload.objects
        .filter(user=user)
        .values('publisher_name')
        .annotate(total=Count('id'))
        .order_by('-total')
    )
    recent_uploads = NewspaperUpload.objects.filter(user=user).order_by('-uploaded_at')[:8]

    context = {
        'is_admin':         False,
        'is_agency':        False,
        'total_uploads':    total_uploads,
        'monthly_uploads':  monthly_uploads,
        'publisher_summary':publisher_summary,
        'recent_uploads':   recent_uploads,
    }
    return render(request, 'dashboard/index.html', context)
