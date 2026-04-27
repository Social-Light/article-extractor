from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth import login, authenticate, logout
from django.contrib import messages
from django.contrib.messages import get_messages

from django.contrib.auth.decorators import login_required
from .forms import RegisterForm, LoginForm
from django.contrib.admin.views.decorators import staff_member_required
from organisations.models import NewspaperUpload
from .models import User

def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:index')
    
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, 'Account created successfully. Please sign in to continue.')
            return redirect('users:login')
    else:
        form = RegisterForm()
    
    return render(request, 'users/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:index')
    
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f'Welcome back, {username}!')
                return redirect('dashboard:index')
        messages.error(request, 'Invalid username or password.')
    else:
        form = LoginForm()
    
    return render(request, 'users/login.html', {'form': form})


def logout_view(request):
    messages.info(request, 'You have been logged out.')
    logout(request)
    # Flush all messages from session to prevent cross-user bleed
    storage = get_messages(request)
    storage.used = True
    return redirect('users:login')

@staff_member_required
def user_list(request):
    """List all newspaper sources (admin only)"""
    users = User.objects.filter(is_staff=False, is_superuser=False)
    
    user_data = []
    for user in users:
        upload_count = NewspaperUpload.objects.filter(user=user).count()
        user_data.append({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'phone': user.phone,
            'date_joined': user.date_joined,
            'last_login': user.last_login,
            'is_active': user.is_active,
            'upload_count': upload_count,
        })
    
    return render(request, 'users/user_list.html', {'users': user_data})


@staff_member_required
def user_detail(request, user_id):
    """View user details and their uploads"""
    user = get_object_or_404(User, id=user_id)
    uploads = NewspaperUpload.objects.filter(user=user).order_by('-uploaded_at')
    
    context = {
        'user': user,
        'uploads': uploads,
        'total_uploads': uploads.count(),
    }
    return render(request, 'users/user_detail.html', context)


@staff_member_required
def deactivate_user(request, user_id):
    """Deactivate a user account"""
    user = get_object_or_404(User, id=user_id)
    user.is_active = False
    user.save()
    messages.success(request, f'User "{user.username}" has been deactivated.')
    return redirect('users:user_list')


@staff_member_required
def activate_user(request, user_id):
    """Activate a user account"""
    user = get_object_or_404(User, id=user_id)
    user.is_active = True
    user.save()
    messages.success(request, f'User "{user.username}" has been activated.')
    return redirect('users:user_list')