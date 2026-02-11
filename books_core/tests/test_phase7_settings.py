"""
Phase 7 Tests: Settings Management UI
Task Groups 7.1 and 7.2

Testing approach:
- Test settings page rendering and form display
- Test settings update functionality
- Test usage dashboard display
- Test form validation
- Test navigation link presence
"""
from django.test import TestCase, Client
from django.urls import reverse
from decimal import Decimal
from datetime import date

from books_core.models import Settings, UsageTracking
from books_core.forms import SettingsForm


class SettingsPageRenderingTestCase(TestCase):
    """Test settings page rendering and structure."""

    def setUp(self):
        """Set up test client and create settings instance."""
        self.client = Client()
        self.settings = Settings.get_settings()
        self.url = reverse('settings')

    def test_settings_page_accessible(self):
        """Test that settings page loads successfully."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'books_core/settings.html')

    def test_settings_page_displays_form(self):
        """Test that settings page displays the settings form."""
        response = self.client.get(self.url)
        self.assertContains(response, 'Monthly Spending Limit')
        self.assertContains(response, 'Daily Summary Limit')
        self.assertContains(response, 'Enable AI Features')
        self.assertContains(response, 'Default AI Model')

    def test_settings_page_displays_usage_dashboard(self):
        """Test that settings page displays usage statistics."""
        response = self.client.get(self.url)
        self.assertContains(response, 'Current Usage')
        self.assertContains(response, 'Daily Summaries')
        self.assertContains(response, 'Monthly Budget')

    def test_settings_link_in_navigation(self):
        """Test that settings link appears in global navigation."""
        response = self.client.get(reverse('library'))
        self.assertContains(response, 'Settings')
        self.assertContains(response, reverse('settings'))


class SettingsFormValidationTestCase(TestCase):
    """Test settings form validation and submission."""

    def setUp(self):
        """Set up test client and settings."""
        self.client = Client()
        self.settings = Settings.get_settings()
        self.url = reverse('settings')

    def test_update_settings_valid_data(self):
        """Test updating settings with valid data."""
        data = {
            'monthly_limit_usd': '10.00',
            'daily_summary_limit': '50',
            'ai_features_enabled': True,
            'default_model': 'gpt-4o-mini'
        }
        response = self.client.post(self.url, data)

        # Should redirect to settings page
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, self.url)

        # Settings should be updated
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.monthly_limit_usd, Decimal('10.00'))
        self.assertEqual(self.settings.daily_summary_limit, 50)
        self.assertTrue(self.settings.ai_features_enabled)

    def test_form_validation_negative_monthly_limit(self):
        """Test that negative monthly limit is rejected."""
        form = SettingsForm(data={
            'monthly_limit_usd': '-5.00',
            'daily_summary_limit': '100',
            'ai_features_enabled': True,
            'default_model': 'gpt-4o-mini'
        })
        self.assertFalse(form.is_valid())
        self.assertIn('monthly_limit_usd', form.errors)

    def test_form_validation_negative_daily_limit(self):
        """Test that negative daily limit is rejected."""
        form = SettingsForm(data={
            'monthly_limit_usd': '5.00',
            'daily_summary_limit': '-10',
            'ai_features_enabled': True,
            'default_model': 'gpt-4o-mini'
        })
        self.assertFalse(form.is_valid())
        self.assertIn('daily_summary_limit', form.errors)

    def test_disable_ai_features(self):
        """Test disabling AI features (emergency stop)."""
        data = {
            'monthly_limit_usd': '5.00',
            'daily_summary_limit': '100',
            'ai_features_enabled': False,
            'default_model': 'gpt-4o-mini'
        }
        response = self.client.post(self.url, data)
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.ai_features_enabled)


class UsageDashboardTestCase(TestCase):
    """Test usage dashboard display and calculations."""

    def setUp(self):
        """Set up test client, settings, and usage data."""
        self.client = Client()
        self.settings = Settings.get_settings()
        self.settings.monthly_limit_usd = Decimal('5.00')
        self.settings.daily_summary_limit = 100
        self.settings.save()

        self.url = reverse('settings')

        # Create usage data for today
        today = date.today()
        self.usage = UsageTracking.objects.create(
            date=today,
            month_year=today.strftime('%Y-%m'),
            daily_summaries_count=25,
            daily_tokens_used=5000,
            daily_cost_usd=Decimal('0.50'),
            monthly_summaries_count=75,
            monthly_tokens_used=15000,
            monthly_cost_usd=Decimal('2.50')
        )

    def test_usage_dashboard_displays_daily_stats(self):
        """Test that daily usage statistics are displayed correctly."""
        response = self.client.get(self.url)
        context = response.context

        self.assertEqual(context['usage']['daily_count'], 25)
        self.assertEqual(context['usage']['daily_limit'], 100)
        self.assertEqual(context['usage']['daily_remaining'], 75)
        self.assertEqual(context['usage']['daily_percentage'], 25.0)

    def test_usage_dashboard_displays_monthly_stats(self):
        """Test that monthly usage statistics are displayed correctly."""
        response = self.client.get(self.url)
        context = response.context

        self.assertEqual(context['usage']['monthly_cost'], Decimal('2.50'))
        self.assertEqual(context['usage']['monthly_limit'], Decimal('5.00'))
        self.assertEqual(context['usage']['monthly_remaining'], 2.50)
        self.assertEqual(context['usage']['monthly_percentage'], 50.0)

    def test_usage_dashboard_no_usage_data(self):
        """Test usage dashboard when no usage data exists."""
        # Delete usage data
        UsageTracking.objects.all().delete()

        response = self.client.get(self.url)
        context = response.context

        self.assertEqual(context['usage']['daily_count'], 0)
        self.assertEqual(context['usage']['monthly_cost'], 0)

    def test_usage_percentage_calculation(self):
        """Test that usage percentages are calculated correctly."""
        # Update usage to 80% of daily limit
        self.usage.daily_summaries_count = 80
        self.usage.save()

        response = self.client.get(self.url)
        context = response.context

        self.assertEqual(context['usage']['daily_percentage'], 80.0)


class SettingsFormFieldsTestCase(TestCase):
    """Test settings form fields and widgets."""

    def setUp(self):
        """Set up settings and form."""
        self.settings = Settings.get_settings()
        self.form = SettingsForm(instance=self.settings)

    def test_form_has_required_fields(self):
        """Test that form includes all required fields."""
        self.assertIn('monthly_limit_usd', self.form.fields)
        self.assertIn('daily_summary_limit', self.form.fields)
        self.assertIn('ai_features_enabled', self.form.fields)
        self.assertIn('default_model', self.form.fields)

    def test_form_field_labels(self):
        """Test that form fields have appropriate labels."""
        self.assertEqual(
            self.form.fields['monthly_limit_usd'].label,
            'Monthly Spending Limit (USD)'
        )
        self.assertEqual(
            self.form.fields['daily_summary_limit'].label,
            'Daily Summary Limit'
        )

    def test_form_field_help_texts(self):
        """Test that form fields have help texts."""
        self.assertIn('Maximum amount', self.form.fields['monthly_limit_usd'].help_text)
        self.assertIn('Maximum number', self.form.fields['daily_summary_limit'].help_text)

    def test_monthly_limit_widget_attributes(self):
        """Test monthly limit field has correct widget attributes."""
        widget = self.form.fields['monthly_limit_usd'].widget
        self.assertIn('min', widget.attrs)
        self.assertEqual(widget.attrs['min'], '0')
        self.assertIn('step', widget.attrs)
        self.assertEqual(widget.attrs['step'], '0.01')


class AdminEnhancementsTestCase(TestCase):
    """Test Django admin enhancements for AI models."""

    def test_settings_admin_registered(self):
        """Test that Settings model is registered in admin."""
        from django.contrib import admin
        from books_core.models import Settings

        self.assertIn(Settings, admin.site._registry)

    def test_prompt_admin_registered(self):
        """Test that Prompt model is registered in admin."""
        from django.contrib import admin
        from books_core.models import Prompt

        self.assertIn(Prompt, admin.site._registry)

    def test_summary_admin_registered(self):
        """Test that Summary model is registered in admin."""
        from django.contrib import admin
        from books_core.models import Summary

        self.assertIn(Summary, admin.site._registry)

    def test_usage_tracking_admin_registered(self):
        """Test that UsageTracking model is registered in admin."""
        from django.contrib import admin
        from books_core.models import UsageTracking

        self.assertIn(UsageTracking, admin.site._registry)

    def test_processing_job_admin_registered(self):
        """Test that ProcessingJob model is registered in admin."""
        from django.contrib import admin
        from books_core.models import ProcessingJob

        self.assertIn(ProcessingJob, admin.site._registry)
