from django.apps import AppConfig


class ArticleExtractorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'article_extractor'

    def ready(self):
        # Configure Django admin site
        from django.contrib import admin
        admin.site.site_header = "Administration"
        admin.site.site_title = "Social Light Admin"
        admin.site.index_title = "Welcome to Administration"