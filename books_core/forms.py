"""
Forms for books_core app.
"""
from django import forms
from django.core.exceptions import ValidationError
from .models import Settings, Book


class UploadBookForm(forms.Form):
    """
    Form for uploading EPUB or PDF files.
    Validates file extension, MIME type, and file size.
    """
    book_file = forms.FileField(
        label='Select Book File',
        required=True,
        widget=forms.FileInput(attrs={
            'accept': '.epub,.pdf,application/epub+zip,application/pdf',
            'id': 'id_book_file'
        })
    )

    book_type = forms.ChoiceField(
        label='Book Type',
        choices=Book.BOOK_TYPE_CHOICES,
        initial='nonfiction',
        required=True,
        widget=forms.RadioSelect(attrs={
            'class': 'book-type-radio'
        })
    )

    def clean_book_file(self):
        """
        Validate uploaded file:
        - Check file extension is .epub or .pdf
        - Check file size <= 50MB
        - Verify content type hint
        """
        book_file = self.cleaned_data.get('book_file')

        if not book_file:
            raise ValidationError("No file was uploaded.")

        filename = book_file.name.lower()

        # Check file extension
        if not (filename.endswith('.epub') or filename.endswith('.pdf')):
            raise ValidationError(
                "Invalid file extension. Please upload an .epub or .pdf file."
            )

        # Check file size (50MB = 52428800 bytes)
        max_size = 52428800  # 50MB
        if book_file.size > max_size:
            raise ValidationError(
                "File size exceeds 50MB limit. Please upload a smaller file."
            )

        # Check content type if provided
        if hasattr(book_file, 'content_type') and book_file.content_type:
            valid_types = [
                'application/epub+zip',
                'application/pdf',
                'application/zip',
                'application/octet-stream'
            ]
            if book_file.content_type not in valid_types:
                raise ValidationError(
                    f"Invalid file type. Expected EPUB or PDF file, got {book_file.content_type}."
                )

        return book_file


class SettingsForm(forms.ModelForm):
    """Form for managing application settings."""

    class Meta:
        model = Settings
        fields = ['monthly_limit_usd', 'daily_summary_limit', 'ai_features_enabled', 'default_model']
        widgets = {
            'monthly_limit_usd': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
                'step': '0.01',
                'placeholder': '5.00'
            }),
            'daily_summary_limit': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
                'placeholder': '100'
            }),
            'ai_features_enabled': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'default_model': forms.Select(attrs={
                'class': 'form-control'
            }, choices=[
                ('gpt-4o-mini', 'GPT-4o Mini'),
                ('gpt-4o', 'GPT-4o'),
                ('gpt-4-turbo', 'GPT-4 Turbo'),
            ])
        }
        labels = {
            'monthly_limit_usd': 'Monthly Spending Limit (USD)',
            'daily_summary_limit': 'Daily Summary Limit',
            'ai_features_enabled': 'Enable AI Features',
            'default_model': 'Default AI Model'
        }
        help_texts = {
            'monthly_limit_usd': 'Maximum amount to spend on AI summaries per month',
            'daily_summary_limit': 'Maximum number of summaries to generate per day',
            'ai_features_enabled': 'Uncheck to disable all AI features (emergency stop)',
            'default_model': 'Default OpenAI model for generating summaries'
        }

    def clean_monthly_limit_usd(self):
        """Validate monthly limit is non-negative."""
        value = self.cleaned_data['monthly_limit_usd']
        if value < 0:
            raise ValidationError('Monthly limit must be greater than or equal to 0')
        return value

    def clean_daily_summary_limit(self):
        """Validate daily limit is non-negative."""
        value = self.cleaned_data['daily_summary_limit']
        if value < 0:
            raise ValidationError('Daily limit must be greater than or equal to 0')
        return value
