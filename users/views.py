import logging

from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth import login, authenticate, logout, update_session_auth_hash
from django.contrib import messages
from django.contrib.messages import get_messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.core import signing
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from django.utils import timezone

from .forms import RegisterForm, LoginForm, CreateAgencyForm, PasswordResetForm, SetPasswordForm, ProfileForm
from .models import User, ConsumedSsoToken
from .utils import admin_required
from organisations.models import NewspaperUpload, Organisation

logger = logging.getLogger(__name__)

# Must match monitor/extractor_sso.py's HANDOFF_TTL_SECONDS and salt exactly —
# the two are verifying each other's work, not independent choices.
SSO_HANDOFF_TTL_SECONDS = 60
SSO_SIGNING_SALT = 'extractor-sso'


def _get_or_create_demo_organisation(social_light_org_id, org_name):
    """The Organisation a demo (trial) account's self-serve extraction runs
    against — looked up by social_light_org_id first, so every member of the
    same trial org shares one Organisation rather than each getting their own.

    ``Organisation.name`` is unique, and a name collision here is a real
    possibility (two different social-light-platform orgs happening to share
    a display name, or a name an admin already used for an unrelated,
    hand-created Organisation) — never let that raise and break login;
    suffix with a short slice of the org id instead, the same shape as any
    other disambiguated auto-name.
    """
    existing = Organisation.objects.filter(social_light_org_id=social_light_org_id).first()
    if existing is not None:
        return existing

    org_name = (org_name or 'Demo Organisation').strip()[:180]
    candidate = org_name
    if Organisation.objects.filter(name=candidate).exists():
        candidate = f'{org_name} ({social_light_org_id[:8]})'[:200]
    return Organisation.objects.create(name=candidate, social_light_org_id=social_light_org_id)


def sso_consume(request, token):
    """The other half of the social-light-platform handoff
    (monitor/extractor_sso.py:handoff) — verify the token, find-or-create the
    matching account, and log it straight in.

    Deliberately fails closed: a bad signature, an expired token, or a
    replayed one all land on the same "start over" page rather than ever
    falling back to trusting anything about the request itself.
    """
    signer = signing.TimestampSigner(key=settings.EXTRACTOR_SSO_SECRET, salt=SSO_SIGNING_SALT)
    try:
        payload = signer.unsign_object(token, max_age=SSO_HANDOFF_TTL_SECONDS)
    except (signing.BadSignature, signing.SignatureExpired):
        return render(request, 'users/sso_failed.html', status=400)

    email = (payload.get('email') or '').strip()
    org_id = (payload.get('org_id') or '').strip()
    org_name = (payload.get('org_name') or '').strip()
    plan = payload.get('plan')
    nonce = payload.get('nonce')
    if not (email and org_id and nonce and plan in ('active', 'trial')):
        return render(request, 'users/sso_failed.html', status=400)

    # Recorded before anything else happens: a second attempt with this same
    # token — the window is only 60s, but a captured URL is a captured URL —
    # must be refused even though the signature itself is still valid.
    _token_row, created = ConsumedSsoToken.objects.get_or_create(nonce=nonce)
    if not created:
        return render(request, 'users/sso_failed.html', status=400)

    role = User.ROLE_AGENCY if plan == 'active' else User.ROLE_SOURCE
    try:
        user, is_new = User.objects.get_or_create(
            email__iexact=email,
            defaults={'username': email, 'email': email},
        )
    except User.MultipleObjectsReturned:
        # email has no uniqueness constraint on this model, and predates it
        # in a few legacy rows (self-registration never enforced one) — an
        # ambiguous match is a data problem to go fix, never a reason to
        # 500 the person mid-login. Same fail-closed "start over" page as
        # every other validation failure above.
        logger.error(
            "sso_consume: %s matches more than one extractor account; "
            "can't pick one to log into. Needs manual dedup.", email,
        )
        return render(request, 'users/sso_failed.html', status=400)
    if is_new:
        user.set_unusable_password()
    user.role = role
    user.social_light_org_id = org_id
    user.is_active = True
    user.email_verified = True  # already verified on social-light-platform
    user.save()

    # A trial account gets its own Organisation to self-serve extraction
    # against — a paid Agency account already reaches every Organisation an
    # admin has set up and needs no auto-provisioned one of its own.
    if plan == 'trial':
        _get_or_create_demo_organisation(org_id, org_name)

    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return redirect('dashboard:index')


def verify_email(request, token):
    """Verify user email with token."""
    try:
        user = User.objects.get(email_verification_token=token)
        user.verify_email()
        user.is_active = True  # Activate the user
        user.save()
        messages.success(request, 'Your email has been verified successfully! You can now sign in to your account.')
    except User.DoesNotExist:
        messages.error(request, 'Invalid verification link. Please try registering again.')
    
    return redirect('users:login')


def resend_verification(request):
    """Resend email verification link."""
    if request.method == 'POST':
        email = request.POST.get('email')
        try:
            user = User.objects.get(email=email, email_verified=False)
            if user.email_verification_token:
                # Regenerate token and send email
                token = user.generate_verification_token()
                verification_url = f"{settings.SITE_URL}/users/verify-email/{token}/"
                
                subject = 'Verify Your Email - Social Light Extractor'
                html_message = render_to_string('users/email_verification_email.html', {
                    'user': user,
                    'verification_url': verification_url,
                })
                plain_message = strip_tags(html_message)
                
                send_mail(
                    subject,
                    plain_message,
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    html_message=html_message,
                    fail_silently=False,
                )
                messages.success(request, 'Verification email sent! Please check your email.')
            else:
                messages.error(request, 'No verification token found. Please register again.')
        except User.DoesNotExist:
            messages.error(request, 'No unverified account found with this email address.')
    
    return redirect('users:login')


def password_reset(request):
    """Send password reset email."""
    if request.method == 'POST':
        form = PasswordResetForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            try:
                user = User.objects.get(email=email, email_verified=True)
                token = user.generate_password_reset_token()
                reset_url = f"{settings.SITE_URL}/users/reset-password/{token}/"
                
                subject = 'Reset Your Password - Social Light Extractor'
                html_message = render_to_string('users/password_reset_email.html', {
                    'user': user,
                    'token': token,
                    'protocol': 'https' if request.is_secure() else 'http',
                    'domain': request.get_host(),
                })
                plain_message = strip_tags(html_message)
                
                send_mail(
                    subject,
                    plain_message,
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    html_message=html_message,
                    fail_silently=False,
                )
            except User.DoesNotExist:
                pass  # Don't reveal if email exists or not for security
            
            return redirect('users:password_reset_done')
    else:
        form = PasswordResetForm()
    
    return render(request, 'users/password_reset.html', {'form': form})


def password_reset_done(request):
    """Show password reset email sent confirmation."""
    return render(request, 'users/password_reset_done.html')


def password_reset_confirm(request, token):
    """Handle password reset confirmation."""
    try:
        user = User.objects.get(password_reset_token=token)
        if not user.is_password_reset_token_valid():
            messages.error(request, 'Password reset link has expired. Please request a new one.')
            return redirect('users:password_reset')
    except User.DoesNotExist:
        messages.error(request, 'Invalid password reset link.')
        return redirect('users:password_reset')
    
    if request.method == 'POST':
        form = SetPasswordForm(request.POST)
        if form.is_valid():
            user.set_password(form.cleaned_data['password1'])
            user.clear_password_reset_token()
            user.save()
            messages.success(request, 'Your password has been reset successfully! You can now sign in.')
            return redirect('users:password_reset_complete')
    else:
        form = SetPasswordForm()
    
    return render(request, 'users/password_reset_confirm.html', {'form': form})


def password_reset_complete(request):
    """Show password reset complete confirmation."""
    return render(request, 'users/password_reset_complete.html')


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:index')
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Account created successfully. Please check your email and click the verification link to activate your account.')
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
                if not user.email_verified:
                    messages.error(request, 'Please verify your email address before signing in. Check your email for the verification link.')
                    return redirect('users:login')
                login(request, user)
                messages.success(request, f'Welcome back, {username}!')
                return redirect('dashboard:index')
        messages.error(request, 'Invalid email address or password.')
    else:
        form = LoginForm()
    return render(request, 'users/login.html', {'form': form})


def logout_view(request):
    messages.info(request, 'You have been logged out.')
    logout(request)
    storage = get_messages(request)
    storage.used = True
    return redirect('users:login')


# ── Sources (admin only) ──────────────────────────────────────────────────────

@admin_required
def user_list(request):
    """List all newspaper source accounts (admin only)."""
    users = User.objects.filter(is_staff=False, is_superuser=False, role=User.ROLE_SOURCE)
    user_data = []
    for user in users:
        upload_count = NewspaperUpload.objects.filter(user=user).count()
        user_data.append({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'date_joined': user.date_joined,
            'last_login': user.last_login,
            'is_active': user.is_active,
            'upload_count': upload_count,
        })
    return render(request, 'users/user_list.html', {'users': user_data})


@admin_required
def user_detail(request, user_id):
    user = get_object_or_404(User, id=user_id)
    uploads = NewspaperUpload.objects.filter(user=user).order_by('-uploaded_at')
    return render(request, 'users/user_detail.html', {
        'user': user,
        'uploads': uploads,
        'total_uploads': uploads.count(),
    })


@admin_required
def deactivate_user(request, user_id):
    user = get_object_or_404(User, id=user_id)
    user.is_active = False
    user.save()
    messages.success(request, f'User "{user.username}" has been deactivated.')
    return redirect('users:user_list')


@admin_required
def activate_user(request, user_id):
    user = get_object_or_404(User, id=user_id)
    user.is_active = True
    user.save()
    messages.success(request, f'User "{user.username}" has been activated.')
    return redirect('users:user_list')


# ── Agency users (admin only) ─────────────────────────────────────────────────

@admin_required
def agency_list(request):
    """List all agency accounts (admin only)."""
    agencies = User.objects.filter(role=User.ROLE_AGENCY).order_by('username')
    agency_data = []
    for user in agencies:
        agency_data.append({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'date_joined': user.date_joined,
            'last_login': user.last_login,
            'is_active': user.is_active,
        })
    return render(request, 'users/agency_list.html', {'agencies': agency_data})


@admin_required
def create_agency(request):
    """Create a new agency user (admin only)."""
    if request.method == 'POST':
        form = CreateAgencyForm(request.POST)
        if form.is_valid():
            try:
                user = form.save()
                messages.success(request, f'Agency account "{user.email}" created successfully. An activation email has been sent.')
                return redirect('users:agency_list')
            except Exception as e:
                messages.error(request, f'Error creating agency account: {str(e)}')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
    else:
        form = CreateAgencyForm()
    return render(request, 'users/create_agency.html', {'form': form})


def activate_agency_account(request, token):
    """Allow an agency user to activate their account by setting a password."""
    try:
        user = User.objects.get(password_reset_token=token, is_active=False)
        if not user.is_password_reset_token_valid():
            messages.error(request, 'Activation link has expired. Please ask your administrator to resend the activation email.')
            return redirect('users:login')
    except User.DoesNotExist:
        messages.error(request, 'Invalid activation link.')
        return redirect('users:login')

    if request.method == 'POST':
        form = SetPasswordForm(request.POST)
        if form.is_valid():
            user.set_password(form.cleaned_data['password1'])
            user.clear_password_reset_token()
            user.is_active = True
            user.email_verified = True
            user.save()
            messages.success(request, 'Your account has been activated. You can now sign in with your email and password.')
            return redirect('users:login')
    else:
        form = SetPasswordForm()

    return render(request, 'users/password_reset_confirm.html', {'form': form})


@admin_required
def deactivate_agency(request, user_id):
    user = get_object_or_404(User, id=user_id, role=User.ROLE_AGENCY)
    user.is_active = False
    user.save()
    messages.success(request, f'Agency "{user.username}" has been deactivated.')
    return redirect('users:agency_list')


@admin_required
def activate_agency(request, user_id):
    user = get_object_or_404(User, id=user_id, role=User.ROLE_AGENCY)
    user.is_active = True
    user.save()
    messages.success(request, f'Agency "{user.username}" has been activated.')
    return redirect('users:agency_list')


@login_required
def profile(request):
    """Allow users to view and update their profile information."""
    if request.method == 'POST':
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            # Check if email changed and requires verification
            old_email = request.user.email
            new_email = form.cleaned_data['email']
            
            if old_email != new_email:
                # Email changed - require verification
                user = form.save(commit=False)
                user.email_verified = False
                user.is_active = False
                user.save()
                
                # Send verification email for new email
                token = user.generate_verification_token()
                verification_url = f"{settings.SITE_URL}/users/verify-email/{token}/"
                
                subject = 'Verify Your New Email - Social Light Extractor'
                html_message = render_to_string('users/email_verification_email.html', {
                    'user': user,
                    'verification_url': verification_url,
                })
                plain_message = strip_tags(html_message)
                
                send_mail(
                    subject,
                    plain_message,
                    settings.DEFAULT_FROM_EMAIL,
                    [new_email],
                    html_message=html_message,
                    fail_silently=False,
                )
                
                messages.warning(request, 'Email address changed. Please check your new email and click the verification link to reactivate your account.')
                logout(request)
                return redirect('users:login')
            else:
                # No email change, just save
                form.save()
                messages.success(request, 'Profile updated successfully!')
                return redirect('users:profile')
    else:
        form = ProfileForm(instance=request.user)
    
    context = {
        'form': form,
        'user': request.user,
    }
    return render(request, 'users/profile.html', context)
