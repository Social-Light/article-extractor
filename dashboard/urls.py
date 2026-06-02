from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('report-issue/', views.report_issue, name='report_issue'),
    path('issues/', views.issue_list, name='issue_list'),
]
