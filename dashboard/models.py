from django.db import models
from django.conf import settings


class IssueReport(models.Model):
    CATEGORY_CHOICES = [
        ('bug', 'Bug / Error'),
        ('data', 'Data Issue'),
        ('access', 'Access Problem'),
        ('extraction', 'Extraction Issue'),
        ('other', 'Other'),
    ]
    STATUS_OPEN = 'open'
    STATUS_RESOLVED = 'resolved'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'Open'),
        (STATUS_RESOLVED, 'Resolved'),
    ]

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='issue_reports',
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    title = models.CharField(max_length=200)
    description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    submitted_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"[{self.get_category_display()}] {self.title} — {self.submitted_by}"
