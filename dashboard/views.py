from datetime import datetime

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Count, Sum
from django.utils import timezone

from organisations.models import NewspaperUpload, Organisation, ExtractedArticle, ExtractionJob
from users.models import User
from .models import IssueReport
from .forms import IssueReportForm


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
            'open_issues':        IssueReport.objects.filter(status=IssueReport.STATUS_OPEN).count(),
        }
        return render(request, 'dashboard/admin_dashboard.html', context)

    # ── Agency dashboard ──────────────────────────────────────────────────────
    if user.is_agency:
        recent_jobs      = ExtractionJob.objects.filter(run_by=user).order_by('-started_at')[:8]
        total_articles   = ExtractedArticle.objects.filter(extraction_job__run_by=user).count()
        total_orgs       = Organisation.objects.count()
        recent_articles  = ExtractedArticle.objects.filter(extraction_job__run_by=user).order_by('-created_at')[:8]
        total_uploads    = NewspaperUpload.objects.filter(user=user).count()
        monthly_uploads  = NewspaperUpload.objects.filter(
            user=user,
            uploaded_at__month=datetime.now().month,
            uploaded_at__year=datetime.now().year,
        ).count()
        recent_uploads   = NewspaperUpload.objects.filter(user=user).order_by('-uploaded_at')[:8]

        context = {
            'is_agency':        True,
            'recent_jobs':      recent_jobs,
            'total_articles':   total_articles,
            'total_orgs':       total_orgs,
            'recent_articles':  recent_articles,
            'total_uploads':    total_uploads,
            'monthly_uploads':  monthly_uploads,
            'recent_uploads':   recent_uploads,
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


@login_required
def report_issue(request):
    if request.method == 'POST':
        form = IssueReportForm(request.POST)
        if form.is_valid():
            report = form.save(commit=False)
            report.submitted_by = request.user
            report.save()

            user = request.user
            subject = f"[Issue Report] {report.get_category_display()}: {report.title}"
            body = (
                f"A new issue has been reported on Social Light.\n\n"
                f"Submitted by: {user.get_full_name() or user.username} ({user.email})\n"
                f"Category:     {report.get_category_display()}\n"
                f"Title:        {report.title}\n\n"
                f"Description:\n{report.description}\n\n"
                f"Submitted at: {report.submitted_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            )
            support_email = getattr(settings, 'SUPPORT_EMAIL', 'tony@sociallightbw.com')
            try:
                send_mail(
                    subject,
                    body,
                    settings.DEFAULT_FROM_EMAIL,
                    [support_email],
                    fail_silently=False,
                )
            except Exception:
                pass  # don't block the user if email fails

            messages.success(request, 'Your issue has been submitted. Our support team will be in touch.')
            return redirect('dashboard:index')
    else:
        form = IssueReportForm()

    return render(request, 'dashboard/report_issue.html', {'form': form})


@staff_member_required
def issue_list(request):
    status_filter = request.GET.get('status', 'open')
    issues = IssueReport.objects.all()
    if status_filter in ('open', 'resolved'):
        issues = issues.filter(status=status_filter)

    if request.method == 'POST':
        issue_id = request.POST.get('issue_id')
        action = request.POST.get('action')
        issue = get_object_or_404(IssueReport, id=issue_id)
        if action == 'resolve':
            issue.status = IssueReport.STATUS_RESOLVED
            issue.resolved_at = timezone.now()
            issue.save()
            messages.success(request, f'Issue #{issue.id} marked as resolved.')
        elif action == 'reopen':
            issue.status = IssueReport.STATUS_OPEN
            issue.resolved_at = None
            issue.save()
            messages.success(request, f'Issue #{issue.id} reopened.')
        return redirect(f"{request.path}?status={status_filter}")

    context = {
        'issues': issues,
        'status_filter': status_filter,
        'open_count': IssueReport.objects.filter(status=IssueReport.STATUS_OPEN).count(),
        'resolved_count': IssueReport.objects.filter(status=IssueReport.STATUS_RESOLVED).count(),
    }
    return render(request, 'dashboard/issue_list.html', context)
