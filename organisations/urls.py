from django.urls import path
from . import views

app_name = 'organisations'

urlpatterns = [
    # Organisation CRUD
    path('', views.organisation_list, name='list'),
    path('create/', views.organisation_create, name='create'),
    path('<int:organisation_id>/', views.organisation_detail, name='detail'),
    path('<int:organisation_id>/edit/', views.organisation_edit, name='edit'),
    path('<int:organisation_id>/delete/', views.organisation_delete, name='delete'),
    
    # Keyword Categories
    path('<int:organisation_id>/categories/', views.keyword_categories, name='keyword_categories'),
    path('category/<int:category_id>/edit/', views.edit_category, name='edit_category'),
    path('category/<int:category_id>/delete/', views.delete_category, name='delete_category'),
    path('category/<int:category_id>/keywords/', views.category_keywords, name='category_keywords'),
    
    # Keywords
    path('keyword/<int:keyword_id>/delete/', views.delete_keyword, name='delete_keyword'),
    path('keyword/<int:keyword_id>/toggle/', views.toggle_keyword, name='toggle_keyword'),
    
    # Publishers
    path('publishers/', views.publisher_list, name='publishers'),
    path('publisher/<int:publisher_id>/edit/', views.edit_publisher, name='edit_publisher'),
    path('publisher/<int:publisher_id>/delete/', views.delete_publisher, name='delete_publisher'),
    
    # Newspaper Uploads (for users)
    path('upload/', views.upload_newspaper, name='upload_newspaper'),
    path('my-uploads/', views.my_uploads, name='my_uploads'),
    path('upload/<int:upload_id>/', views.upload_detail, name='upload_detail'),
    path('upload/<int:upload_id>/delete/', views.delete_upload, name='delete_upload'),
    
    # Extraction (admin only)
    path('run-extraction/', views.run_extraction, name='run_extraction'),
    path('extraction-results/<int:job_id>/', views.extraction_results, name='extraction_results'),
    # Add this to urlpatterns
    path('extraction-jobs/', views.extraction_jobs, name='extraction_jobs'),
    path('extraction-results/<int:job_id>/rerun/', views.rerun_extraction, name='rerun_extraction'),
    path('all-uploads/', views.all_uploads, name='all_uploads'),
    
    # Articles
    path('all-articles/', views.all_articles, name='all_articles'),
    path('all-adverts/', views.all_adverts, name='all_adverts'),
    path('article/<int:article_id>/', views.article_detail, name='article_detail'),
    path('article/<int:article_id>/feedback/', views.submit_feedback, name='article_feedback'),
    path('article/<int:article_id>/download/', views.download_report, name='download_report'),
    path('export-csv/', views.export_articles_csv, name='export_csv'),
]