"""
Phase 8 Integration Tests: End-to-End Workflow Tests

Tests complete user workflows from start to finish, ensuring all
components work together correctly.
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from unittest.mock import patch, MagicMock
from decimal import Decimal
import json
from datetime import date

from books_core.models import (
    Book, Chapter, Prompt, Summary, UsageTracking,
    Settings, ProcessingJob
)
from books_core.services.cost_control_service import CostControlService


class EndToEndSummaryWorkflowTest(TestCase):
    """Test complete summary generation workflow from UI to database."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.user = User.objects.create_user('testuser', 'test@test.com', 'password')
        self.client.login(username='testuser', password='password')

        # Create book and chapter
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            status='completed'
        )
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter 1',
            content='Test content for chapter 1.',
            word_count=100
        )

        # Create prompt
        self.prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Summarize: {{content}}',
            category='summarization',
            is_fabric=False
        )

        # Set up settings
        self.settings = Settings.get_settings()
        self.settings.ai_features_enabled = True
        self.settings.monthly_limit_usd = Decimal('5.00')
        self.settings.daily_summary_limit = 100
        self.settings.save()

    @patch('books_core.services.openai_service.OpenAIService.complete')
    def test_full_summary_generation_workflow(self, mock_openai):
        """Test complete flow: preview -> confirm -> generate -> display."""
        # Mock OpenAI response
        mock_openai.return_value = {
            'content': 'This is a test summary.',
            'tokens_used': 150,
            'model': 'gpt-4o-mini'
        }

        # Step 1: Preview cost
        preview_url = reverse('api_summary_preview', args=[self.chapter.id])
        preview_data = {
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini'
        }
        response = self.client.post(
            preview_url,
            json.dumps(preview_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        preview = response.json()
        self.assertIn('estimated_tokens', preview)
        self.assertIn('estimated_cost_usd', preview)

        # Step 2: Generate summary
        generate_url = reverse('api_summary_generate', args=[self.chapter.id])
        generate_data = {
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini',
            'confirmed': True
        }
        response = self.client.post(
            generate_url,
            json.dumps(generate_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()

        # Verify response
        self.assertIn('summary_id', result)
        self.assertEqual(result['version'], 1)
        self.assertIn('cost_usd', result)

        # Step 3: Verify database records
        summary = Summary.objects.get(id=result['summary_id'])
        self.assertEqual(summary.chapter, self.chapter)
        self.assertEqual(summary.prompt, self.prompt)
        self.assertEqual(summary.version, 1)
        self.assertIsNotNone(summary.content_json)

        # Step 4: Verify usage tracking
        cost_service = CostControlService()
        usage = cost_service.get_current_usage()
        self.assertGreater(usage['daily']['summaries_count'], 0)
        self.assertGreater(float(usage['daily']['cost_usd']), 0)

        # Step 5: Generate second version
        response = self.client.post(
            generate_url,
            json.dumps(generate_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        result2 = response.json()
        self.assertEqual(result2['version'], 2)

        # Verify version linking
        summary_v2 = Summary.objects.get(id=result2['summary_id'])
        self.assertEqual(summary_v2.previous_version, summary)


class BatchProcessingWorkflowTest(TestCase):
    """Test batch processing workflow with multiple chapters."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.user = User.objects.create_user('testuser', 'test@test.com', 'password')
        self.client.login(username='testuser', password='password')

        # Create book with multiple chapters
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            status='completed'
        )
        self.chapters = []
        for i in range(3):
            chapter = Chapter.objects.create(
                book=self.book,
                chapter_number=i + 1,
                title=f'Chapter {i + 1}',
                content=f'Test content for chapter {i + 1}.',
                word_count=100
            )
            self.chapters.append(chapter)

        # Create prompt
        self.prompt = Prompt.objects.create(
            name='batch_prompt',
            template_text='Summarize: {{content}}',
            category='summarization',
            is_fabric=False
        )

        # Enable AI features
        settings = Settings.get_settings()
        settings.ai_features_enabled = True
        settings.save()

    @patch('books_core.services.openai_service.OpenAIService.complete')
    def test_batch_preview_and_cost_calculation(self, mock_openai):
        """Test batch cost preview calculates total correctly."""
        mock_openai.return_value = {
            'content': 'Test summary',
            'tokens_used': 150,
            'model': 'gpt-4o-mini'
        }

        # Preview batch
        preview_url = reverse('api_batch_preview')
        preview_data = {
            'chapter_ids': [c.id for c in self.chapters],
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini'
        }
        response = self.client.post(
            preview_url,
            json.dumps(preview_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        preview = response.json()

        # Verify batch cost calculation
        self.assertIn('total_cost_usd', preview)
        self.assertIn('total_tokens', preview)
        self.assertEqual(len(preview['chapters']), 3)

        # Total should be sum of individual costs
        self.assertGreater(float(preview['total_cost_usd']), 0)


class CostControlEnforcementTest(TestCase):
    """Test that cost limits are enforced correctly."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.user = User.objects.create_user('testuser', 'test@test.com', 'password')
        self.client.login(username='testuser', password='password')

        # Create book and chapter
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            status='completed'
        )
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter 1',
            content='Test content.',
            word_count=100
        )

        # Create prompt
        self.prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Summarize: {{content}}',
            category='summarization'
        )

        # Set very low monthly limit
        settings = Settings.get_settings()
        settings.monthly_limit_usd = Decimal('0.01')
        settings.daily_summary_limit = 1
        settings.ai_features_enabled = True
        settings.save()

    @patch('books_core.services.openai_service.OpenAIService.complete')
    def test_monthly_limit_enforcement(self, mock_openai):
        """Test that monthly limit blocks operations when exceeded."""
        mock_openai.return_value = {
            'content': 'Test summary',
            'tokens_used': 1000,
            'model': 'gpt-4o-mini'
        }

        # Create usage that exceeds limit
        today = date.today()
        month_year = today.strftime('%Y-%m')
        usage, _ = UsageTracking.objects.get_or_create(
            date=today,
            defaults={'month_year': month_year}
        )
        usage.monthly_cost_usd = Decimal('0.02')  # Exceeds $0.01 limit
        usage.save()

        # Attempt to generate summary
        generate_url = reverse('api_summary_generate', args=[self.chapter.id])
        generate_data = {
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini',
            'confirmed': True
        }
        response = self.client.post(
            generate_url,
            json.dumps(generate_data),
            content_type='application/json'
        )

        # Should be blocked
        self.assertEqual(response.status_code, 400)
        error = response.json()
        self.assertIn('error', error)
        self.assertIn('monthly limit', error['error'].lower())

    @patch('books_core.services.openai_service.OpenAIService.complete')
    def test_daily_summary_limit_enforcement(self, mock_openai):
        """Test that daily summary limit blocks operations when exceeded."""
        mock_openai.return_value = {
            'content': 'Test summary',
            'tokens_used': 100,
            'model': 'gpt-4o-mini'
        }

        # Create usage that exceeds daily limit (limit is 1)
        today = date.today()
        month_year = today.strftime('%Y-%m')
        usage, _ = UsageTracking.objects.get_or_create(
            date=today,
            defaults={'month_year': month_year}
        )
        usage.daily_summaries_count = 2  # Exceeds limit of 1
        usage.save()

        # Attempt to generate summary
        generate_url = reverse('api_summary_generate', args=[self.chapter.id])
        generate_data = {
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini',
            'confirmed': True
        }
        response = self.client.post(
            generate_url,
            json.dumps(generate_data),
            content_type='application/json'
        )

        # Should be blocked
        self.assertEqual(response.status_code, 400)
        error = response.json()
        self.assertIn('error', error)
        self.assertIn('daily', error['error'].lower())


class VersionManagementTest(TestCase):
    """Test version history and comparison features."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.user = User.objects.create_user('testuser', 'test@test.com', 'password')
        self.client.login(username='testuser', password='password')

        # Create book and chapter
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            status='completed'
        )
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter 1',
            content='Test content.',
            word_count=100
        )

        # Create prompt
        self.prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Summarize: {{content}}',
            category='summarization'
        )

        # Enable AI features
        settings = Settings.get_settings()
        settings.ai_features_enabled = True
        settings.save()

    @patch('books_core.services.openai_service.OpenAIService.complete')
    def test_version_chain_creation(self, mock_openai):
        """Test that multiple versions create proper chain."""
        mock_openai.return_value = {
            'content': 'Test summary',
            'tokens_used': 100,
            'model': 'gpt-4o-mini'
        }

        # Generate 3 versions
        generate_url = reverse('api_summary_generate', args=[self.chapter.id])
        generate_data = {
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini',
            'confirmed': True
        }

        versions = []
        for i in range(3):
            response = self.client.post(
                generate_url,
                json.dumps(generate_data),
                content_type='application/json'
            )
            self.assertEqual(response.status_code, 200)
            result = response.json()
            versions.append(result['summary_id'])

        # Verify version chain
        v1 = Summary.objects.get(id=versions[0])
        v2 = Summary.objects.get(id=versions[1])
        v3 = Summary.objects.get(id=versions[2])

        self.assertEqual(v1.version, 1)
        self.assertEqual(v2.version, 2)
        self.assertEqual(v3.version, 3)

        self.assertIsNone(v1.previous_version)
        self.assertEqual(v2.previous_version, v1)
        self.assertEqual(v3.previous_version, v2)


class SettingsManagementTest(TestCase):
    """Test settings configuration and usage dashboard."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.user = User.objects.create_user('testuser', 'test@test.com', 'password')
        self.client.login(username='testuser', password='password')

    def test_settings_update_workflow(self):
        """Test complete settings update workflow."""
        # Get settings page
        settings_url = reverse('settings')
        response = self.client.get(settings_url)
        self.assertEqual(response.status_code, 200)

        # Update settings
        update_data = {
            'monthly_limit_usd': '10.00',
            'daily_summary_limit': 200,
            'ai_features_enabled': True,
            'default_model': 'gpt-4o-mini'
        }
        response = self.client.post(settings_url, update_data)
        self.assertEqual(response.status_code, 200)

        # Verify settings updated
        settings = Settings.get_settings()
        self.assertEqual(settings.monthly_limit_usd, Decimal('10.00'))
        self.assertEqual(settings.daily_summary_limit, 200)
        self.assertTrue(settings.ai_features_enabled)

    def test_usage_dashboard_displays_correctly(self):
        """Test that usage dashboard shows current stats."""
        # Create some usage data
        today = date.today()
        month_year = today.strftime('%Y-%m')
        usage, _ = UsageTracking.objects.get_or_create(
            date=today,
            defaults={'month_year': month_year}
        )
        usage.daily_summaries_count = 5
        usage.daily_cost_usd = Decimal('0.50')
        usage.monthly_summaries_count = 20
        usage.monthly_cost_usd = Decimal('2.00')
        usage.save()

        # Get settings page with dashboard
        settings_url = reverse('settings')
        response = self.client.get(settings_url)
        self.assertEqual(response.status_code, 200)

        # Verify dashboard data in context
        self.assertIn('usage', response.context)
        dashboard = response.context['usage']
        self.assertEqual(dashboard['daily_count'], 5)
        self.assertEqual(dashboard['monthly_cost'], Decimal('2.00'))


class FabricPromptIntegrationTest(TestCase):
    """Test Fabric prompt integration and caching."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.user = User.objects.create_user('testuser', 'test@test.com', 'password')
        self.client.login(username='testuser', password='password')

    @patch('books_core.services.fabric_prompt_service.requests.get')
    def test_fabric_prompt_sync_and_usage(self, mock_get):
        """Test syncing Fabric prompts and using them."""
        # Mock GitHub API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = 'You are an expert summarizer.\n\n{{content}}'
        mock_get.return_value = mock_response

        # Sync a Fabric prompt
        sync_url = reverse('api_fabric_sync')
        sync_data = {
            'prompts': ['summarize']
        }
        response = self.client.post(
            sync_url,
            json.dumps(sync_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)

        # Verify prompt was created
        prompt = Prompt.objects.filter(name='summarize', is_fabric=True).first()
        self.assertIsNotNone(prompt)
        self.assertTrue(prompt.is_fabric)
        self.assertIn('{{content}}', prompt.template_text)


class ErrorHandlingTest(TestCase):
    """Test error handling across the application."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.user = User.objects.create_user('testuser', 'test@test.com', 'password')
        self.client.login(username='testuser', password='password')

        # Create minimal data
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            status='completed'
        )
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter 1',
            content='Test content.',
            word_count=100
        )

    def test_missing_prompt_error(self):
        """Test error when prompt doesn't exist."""
        generate_url = reverse('api_summary_generate', args=[self.chapter.id])
        generate_data = {
            'prompt_id': 99999,  # Non-existent
            'model': 'gpt-4o-mini',
            'confirmed': True
        }
        response = self.client.post(
            generate_url,
            json.dumps(generate_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 404)

    def test_ai_features_disabled_error(self):
        """Test error when AI features are disabled."""
        # Disable AI features
        settings = Settings.get_settings()
        settings.ai_features_enabled = False
        settings.save()

        # Create prompt
        prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Test',
            category='summarization'
        )

        # Attempt to generate
        generate_url = reverse('api_summary_generate', args=[self.chapter.id])
        generate_data = {
            'prompt_id': prompt.id,
            'model': 'gpt-4o-mini',
            'confirmed': True
        }
        response = self.client.post(
            generate_url,
            json.dumps(generate_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        error = response.json()
        self.assertIn('disabled', error['error'].lower())


class ReadingViewIntegrationTest(TestCase):
    """Test reading view with AI summary integration."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.user = User.objects.create_user('testuser', 'test@test.com', 'password')
        self.client.login(username='testuser', password='password')

        # Create book with chapters
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            status='completed'
        )
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter 1',
            content='Test content.',
            word_count=100
        )

        # Create prompt and summary
        self.prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Summarize: {{content}}',
            category='summarization'
        )
        self.summary = Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'text': 'Test summary'},
            tokens_used=100,
            model_used='gpt-4o-mini',
            version=1,
            estimated_cost_usd=Decimal('0.001')
        )

    def test_reading_view_displays_summary_panel(self):
        """Test that reading view shows summary when available."""
        reading_url = reverse('reading_view', args=[self.book.id])
        response = self.client.get(reading_url)
        self.assertEqual(response.status_code, 200)

        # Verify summary panel is present
        content = response.content.decode()
        self.assertIn('summary-panel', content)
        self.assertIn('Generate Summary', content)
