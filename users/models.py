from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Newspaper source user - uploads newspapers"""
    phone = models.CharField(max_length=20, blank=True, null=True)
    
    def __str__(self):
        return self.username
    
    class Meta:
        db_table = 'users'