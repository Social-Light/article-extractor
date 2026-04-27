from django.db import models
from django.conf import settings


class Organisation(models.Model):
    """Company to monitor (e.g., Khoemacau, De Beers)"""
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_organisations'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name


class KeywordCategory(models.Model):
    """Categories for keywords (e.g., company_names, personnel, competitors)"""
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='keyword_categories')
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=200, blank=True, null=True)
    weight = models.IntegerField(default=1)
    
    class Meta:
        unique_together = ['organisation', 'name']
    
    def __str__(self):
        return f"{self.organisation.name} - {self.name}"


class Keyword(models.Model):
    """Individual keywords for searching"""
    category = models.ForeignKey(KeywordCategory, on_delete=models.CASCADE, related_name='keywords')
    term = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['category', 'term']
    
    def __str__(self):
        return self.term


class Publisher(models.Model):
    """Newspaper publisher (Sunday Standard, Botswana Gazette, etc.)"""
    name = models.CharField(max_length=200, unique=True)
    base_ave_rate = models.DecimalField(max_digits=10, decimal_places=2, default=250.00)
    reach = models.IntegerField(default=5000)
    
    def __str__(self):
        return self.name


class NewspaperUpload(models.Model):
    """Newspaper PDF uploaded by a user"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processed', 'Processed'),
        ('failed', 'Failed'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='newspaper_uploads')
    file_name = models.CharField(max_length=200)
    file_path = models.CharField(max_length=500)
    file_size = models.IntegerField(default=0)
    page_count = models.IntegerField(default=0)
    publisher = models.ForeignKey(Publisher, on_delete=models.SET_NULL, null=True, blank=True)
    publisher_name = models.CharField(max_length=200, blank=True, null=True)
    publication_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    
    class Meta:
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"{self.publisher_name or self.file_name} - {self.user.username}"


class ExtractionJob(models.Model):
    """Extraction job run by admin"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    EXTRACTION_TYPE_CHOICES = [
        ('PR', 'PR'),
        ('Ad', 'Ad'),
    ]
    
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='extraction_jobs')
    newspaper_uploads = models.ManyToManyField(NewspaperUpload, related_name='extraction_jobs')
    month = models.CharField(max_length=7, blank=True, null=True, help_text="Format: YYYY-MM")
    extraction_type = models.CharField(max_length=20, choices=EXTRACTION_TYPE_CHOICES, default='PR')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    articles_found = models.IntegerField(default=0)
    error_message = models.TextField(blank=True, null=True)
    run_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='extraction_jobs')
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    
    class Meta:
        ordering = ['-started_at']
    
    def __str__(self):
        return f"{self.organisation.name} - {self.month} - {self.started_at.strftime('%Y-%m-%d %H:%M')}"


class ExtractedArticle(models.Model):
    """Extracted article from a newspaper"""
    SENTIMENT_CHOICES = [
        ('Positive', 'Positive'),
        ('Neutral', 'Neutral'),
        ('Negative', 'Negative'),
    ]
    
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='articles')
    extraction_job = models.ForeignKey(ExtractionJob, on_delete=models.CASCADE, related_name='articles', null=True)
    newspaper_upload = models.ForeignKey(NewspaperUpload, on_delete=models.CASCADE, related_name='articles', null=True)
    
    title = models.CharField(max_length=500)
    publisher = models.ForeignKey(Publisher, on_delete=models.SET_NULL, null=True, blank=True)
    publisher_name = models.CharField(max_length=200)
    section = models.CharField(max_length=100, default='Business')
    publication_date = models.CharField(max_length=50, blank=True, null=True)
    page = models.IntegerField()
    pages = models.CharField(max_length=100, blank=True, null=True)
    reach = models.IntegerField()
    ave = models.DecimalField(max_digits=12, decimal_places=2)
    author = models.CharField(max_length=200, blank=True, null=True)
    sentiment = models.CharField(max_length=20, choices=SENTIMENT_CHOICES, default='Neutral')
    extraction_type = models.CharField(max_length=20, choices=ExtractionJob.EXTRACTION_TYPE_CHOICES, default='PR')
    keywords_matched = models.TextField(blank=True, null=True)
    screenshot_path = models.CharField(max_length=500, blank=True, null=True)
    report_path = models.CharField(max_length=500, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.organisation.name}: {self.title[:50]}"