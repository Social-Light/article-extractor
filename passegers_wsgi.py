import sys
import os

# Add project path
sys.path.insert(0, os.path.dirname(__file__))

# Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'article_extractor.settings')

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
