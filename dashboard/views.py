from datetime import datetime

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from organisations.models import NewspaperUpload, Organisation, ExtractedArticle


from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from organisations.models import NewspaperUpload, Organisation, ExtractedArticle
from users.models import User
from datetime import datetime


@login_required
def index(request):
    if request.user.is_staff or request.user.is_superuser:
        # Admin dashboard
        total_uploads = NewspaperUpload.objects.count()
        total_users = User.objects.filter(is_staff=False, is_superuser=False).count()
        total_organisations = Organisation.objects.count()
        total_articles = ExtractedArticle.objects.count()
        recent_uploads = NewspaperUpload.objects.all().order_by('-uploaded_at')[:10]
        organisations = Organisation.objects.all()

        registered_users = User.objects.filter(is_staff=False, is_superuser=False)
        user_data = []
        for user in registered_users:
            uploads = NewspaperUpload.objects.filter(user=user)
            last_upload = uploads.order_by('-uploaded_at').first()
            user_data.append({
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'phone': user.phone,
                'date_joined': user.date_joined,
                'upload_count': uploads.count(),
                'last_upload': last_upload.uploaded_at if last_upload else None,
            })
    
        
        context = {
            'total_uploads': total_uploads,
            'total_users': total_users,
            'total_organisations': total_organisations,
            'total_articles': total_articles,
            'recent_uploads': recent_uploads,
            'organisations': organisations,
            'registered_users': user_data,
        }
        return render(request, 'dashboard/admin_dashboard.html', context)
    else:
        # Regular users go to their uploads page
        return redirect('organisations:my_uploads')