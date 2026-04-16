from django import forms
from .models import Organisation, KeywordCategory, Keyword, Publisher


class OrganisationForm(forms.ModelForm):
    class Meta:
        model = Organisation
        fields = ['name', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class KeywordCategoryForm(forms.ModelForm):
    class Meta:
        model = KeywordCategory
        fields = ['name', 'description', 'weight']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.TextInput(attrs={'class': 'form-control'}),
            'weight': forms.NumberInput(attrs={'class': 'form-control'}),
        }


class KeywordForm(forms.ModelForm):
    class Meta:
        model = Keyword
        fields = ['term', 'is_active']
        widgets = {
            'term': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class PublisherForm(forms.ModelForm):
    class Meta:
        model = Publisher
        fields = ['name', 'base_ave_rate', 'reach']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'base_ave_rate': forms.NumberInput(attrs={'class': 'form-control'}),
            'reach': forms.NumberInput(attrs={'class': 'form-control'}),
        }


class BulkKeywordForm(forms.Form):
    keywords = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        help_text='Enter one keyword per line'
    )