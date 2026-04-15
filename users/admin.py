from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'phone', 'is_staff', 'is_active')
    search_fields = ('username', 'email')
    
    fieldsets = UserAdmin.fieldsets + (
        ('Contact Info', {
            'fields': ('phone',)
        }),
    )