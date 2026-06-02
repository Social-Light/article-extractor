from django.contrib import admin
from .models import IssueReport


@admin.register(IssueReport)
class IssueReportAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'category', 'submitted_by', 'status', 'submitted_at')
    list_filter = ('status', 'category')
    search_fields = ('title', 'description', 'submitted_by__username', 'submitted_by__email')
    readonly_fields = ('submitted_at', 'resolved_at')
