from django import forms
from .models import IssueReport


class IssueReportForm(forms.ModelForm):
    class Meta:
        model = IssueReport
        fields = ['category', 'title', 'description']
        widgets = {
            'category': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Brief summary of the issue'}),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 6,
                'placeholder': 'Describe what happened, what you expected, and any steps to reproduce…',
            }),
        }
