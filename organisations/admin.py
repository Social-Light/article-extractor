from django.contrib import admin
from .models import (
    Organisation, Publisher, KeywordCategory, Keyword,
    NewspaperUpload, ExtractionJob, ExtractedArticle,
)


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'created_at')
    search_fields = ('name',)
    list_select_related = ('created_by',)


@admin.register(Publisher)
class PublisherAdmin(admin.ModelAdmin):
    list_display = ('name', 'base_ave_rate', 'reach')
    search_fields = ('name',)


@admin.register(KeywordCategory)
class KeywordCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'organisation')
    list_filter = ('organisation',)
    search_fields = ('name',)


@admin.register(Keyword)
class KeywordAdmin(admin.ModelAdmin):
    list_display = ('term', 'category', 'is_active')
    list_filter = ('is_active', 'category__organisation')
    search_fields = ('term',)


@admin.register(NewspaperUpload)
class NewspaperUploadAdmin(admin.ModelAdmin):
    list_display = ('file_name', 'publisher_name', 'user', 'status', 'uploaded_at')
    list_filter = ('status', 'publisher')
    search_fields = ('file_name', 'publisher_name')
    list_select_related = ('user', 'publisher')
    readonly_fields = ('uploaded_at', 'processed_at')


@admin.register(ExtractionJob)
class ExtractionJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'organisation', 'month', 'extraction_type', 'status', 'articles_found', 'run_by', 'started_at')
    list_filter = ('status', 'extraction_type', 'organisation')
    search_fields = ('organisation__name', 'month')
    list_select_related = ('organisation', 'run_by')
    readonly_fields = ('started_at', 'completed_at')
    actions = ['mark_as_failed']

    @admin.action(description='Force-reset selected jobs to Failed')
    def mark_as_failed(self, request, queryset):
        updated = queryset.filter(status__in=('running', 'pending')).update(
            status='failed', error_message='Manually reset via admin.'
        )
        self.message_user(request, f'{updated} job(s) reset to failed.')


@admin.register(ExtractedArticle)
class ExtractedArticleAdmin(admin.ModelAdmin):
    list_display = ('title', 'organisation', 'publisher_name', 'section', 'sentiment', 'extraction_job', 'created_at')
    list_filter = ('sentiment', 'extraction_type', 'organisation')
    search_fields = ('title', 'publisher_name', 'organisation__name')
    list_select_related = ('organisation', 'publisher', 'extraction_job')
    readonly_fields = ('created_at',)
