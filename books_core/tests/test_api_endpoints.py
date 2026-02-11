"""
Tests for Phase 3: REST API Endpoints.

Covers Task Groups 3.1, 3.2, 3.3, and 3.4:
- Summary Generation API (preview, generate, versions, detail)
- Batch Processing API (preview, generate)
- Prompt Management API (list, sync, preview)
- Settings & Usage API (get/update settings, usage stats)
"""

import json
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.urls import reverse

from books_core.models import (
    Book, Chapter, Prompt, Summary, Settings, ProcessingJob, UsageTracking
)


class SummaryGenerationAPITestCase(TestCase):
    """Test Task Group 3.1: Summary Generation API endpoints."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

        # Create test book and chapter
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            status='completed'
        )
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Test Chapter',
            content='This is test chapter content for summarization.',
            word_count=8
        )

        # Create test prompt
        self.prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Summarize: {content}',
            category='summarization',
            is_fabric=False,
            variables_required=['content']
        )

        # Create settings
        Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True
        )

    @patch('books_core.services.cost_control_service.CostControlService.count_tokens')
    @patch('books_core.services.cost_control_service.CostControlService.estimate_cost')
    @patch('books_core.services.cost_control_service.CostControlService.check_limits')
    def test_summary_preview_returns_accurate_estimates(self, mock_check_limits, mock_estimate_cost, mock_count_tokens):
        """Test cost preview returns token/cost estimates."""
        # Mock token counting
        mock_count_tokens.return_value = 100

        # Mock cost estimation
        mock_estimate_cost.return_value = {
            'total_tokens': 120,
            'estimated_cost_usd': Decimal('0.000050'),
            'input_tokens': 100,
            'output_tokens': 20,
        }

        # Mock limit checking
        mock_check_limits.return_value = {
            'daily_usage': {'count': 0, 'limit': 100, 'remaining': 100},
            'monthly_usage': {'cost': Decimal('0'), 'limit': Decimal('5.00')},
            'warnings': []
        }

        # Make request
        url = reverse('api_summary_preview', kwargs={'chapter_id': self.chapter.id})
        data = {
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini'
        }
        response = self.client.post(url, json.dumps(data), content_type='application/json')

        # Assertions
        self.assertEqual(response.status_code, 200)
        response_data = json.loads(response.content)
        self.assertEqual(response_data['estimated_tokens'], 120)
        self.assertEqual(response_data['estimated_cost_usd'], '0.000050')
        self.assertEqual(response_data['chapter_id'], self.chapter.id)
        self.assertEqual(response_data['prompt_name'], 'test_prompt')

    def test_summary_preview_includes_usage_stats_and_warnings(self):
        """Test cost preview includes current usage stats."""
        with patch('books_core.services.cost_control_service.CostControlService.count_tokens', return_value=100), \
             patch('books_core.services.cost_control_service.CostControlService.estimate_cost', return_value={
                 'total_tokens': 120,
                 'estimated_cost_usd': Decimal('0.000050'),
                 'input_tokens': 100,
                 'output_tokens': 20,
             }), \
             patch('books_core.services.cost_control_service.CostControlService.check_limits', return_value={
                 'daily_usage': {'count': 80, 'limit': 100, 'remaining': 20},
                 'monthly_usage': {'cost': Decimal('4.50'), 'limit': Decimal('5.00')},
                 'warnings': ['Approaching monthly budget limit']
             }):

            url = reverse('api_summary_preview', kwargs={'chapter_id': self.chapter.id})
            data = {'prompt_id': self.prompt.id, 'model': 'gpt-4o-mini'}
            response = self.client.post(url, json.dumps(data), content_type='application/json')

            self.assertEqual(response.status_code, 200)
            response_data = json.loads(response.content)
            self.assertIn('daily_usage', response_data)
            self.assertIn('monthly_usage', response_data)
            self.assertIn('warnings', response_data)
            self.assertEqual(len(response_data['warnings']), 1)

    def test_summary_generate_requires_confirmed_true(self):
        """Test summary generation requires confirmed=true."""
        url = reverse('api_summary_generate', kwargs={'chapter_id': self.chapter.id})
        data = {
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini',
            'confirmed': False
        }
        response = self.client.post(url, json.dumps(data), content_type='application/json')

        self.assertEqual(response.status_code, 400)
        response_data = json.loads(response.content)
        self.assertIn('confirmed must be true', response_data['error'])
    @patch('books_core.summary_api_views.SummaryService')
    @patch('books_core.summary_api_views.OpenAIService')
    @patch('books_core.summary_api_views.CostControlService')
    def test_summary_generate_blocks_when_limit_exceeded(self, mock_cost_class, mock_openai_class, mock_summary_class):
        """Test summary generation blocks when limit exceeded."""
        from books_core.exceptions import LimitExceededException

        # Mock CostControlService instance
        mock_cost_instance = MagicMock()
        mock_cost_class.return_value = mock_cost_instance

        # Mock OpenAI service instance
        mock_openai_instance = MagicMock()
        mock_openai_class.return_value = mock_openai_instance

        # Mock SummaryService instance
        mock_summary_instance = MagicMock()
        mock_summary_class.return_value = mock_summary_instance

        # Mock complete_with_cost_control to raise LimitExceededException
        # This happens INSIDE the service when check_limits is called
        mock_openai_instance.complete_with_cost_control.side_effect = LimitExceededException(
            'monthly',
            Decimal('5.10'),
            Decimal('5.00')
        )

        url = reverse('api_summary_generate', kwargs={'chapter_id': self.chapter.id})
        data = {
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini',
            'confirmed': True
        }
        response = self.client.post(url, json.dumps(data), content_type='application/json')

        self.assertEqual(response.status_code, 403)
        response_data = json.loads(response.content)
        self.assertIn('limit exceeded', response_data['error'].lower())
        self.assertIn('limit exceeded', response_data['error'].lower())

    @patch('books_core.summary_api_views.SummaryService')
    @patch('books_core.summary_api_views.OpenAIService')
    @patch('books_core.summary_api_views.CostControlService')
    def test_summary_generate_creates_summary_with_version(self, mock_cost_class, mock_openai_class, mock_summary_class):
        """Test summary generation creates Summary with correct version number."""
        # Mock CostControlService instance
        mock_cost_instance = MagicMock()
        mock_cost_class.return_value = mock_cost_instance

        # Mock OpenAI service
        mock_openai_instance = MagicMock()
        mock_openai_class.return_value = mock_openai_instance

        # Mock OpenAI response
        mock_openai_instance.complete_with_cost_control.return_value = {
            'content': 'This is a test summary.',
            'tokens_used': 120,
            'model': 'gpt-4o-mini',
            'cost_usd': Decimal('0.000050')
        }

        # Mock SummaryService instance
        mock_summary_instance = MagicMock()
        mock_summary_class.return_value = mock_summary_instance

        # Mock get_next_version to return version 1 (no previous)
        mock_summary_instance.get_next_version.return_value = (1, None)

        # Create actual summary object for the test
        test_summary = Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'text': 'This is a test summary.'},
            tokens_used=120,
            model_used='gpt-4o-mini',
            version=1,
            estimated_cost_usd=Decimal('0.000050')
        )

        # Mock create_summary to return the actual object
        mock_summary_instance.create_summary.return_value = test_summary

        url = reverse('api_summary_generate', kwargs={'chapter_id': self.chapter.id})
        data = {
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini',
            'confirmed': True
        }
        response = self.client.post(url, json.dumps(data), content_type='application/json')

        self.assertEqual(response.status_code, 201)
        response_data = json.loads(response.content)
        self.assertEqual(response_data['version'], 1)
        self.assertIn('summary_id', response_data)
        self.assertIn('content', response_data)

        # Verify summary was created in database
        summary = Summary.objects.get(id=response_data['summary_id'])
        self.assertEqual(summary.version, 1)
        self.assertEqual(summary.chapter, self.chapter)
        self.assertEqual(summary.prompt, self.prompt)

    def test_get_versions_returns_summaries_ordered_by_version_desc(self):
        """Test get versions returns summaries ordered by version DESC."""
        # Create multiple versions
        for version in [1, 2, 3]:
            Summary.objects.create(
                chapter=self.chapter,
                prompt=self.prompt,
                summary_type='tldr',
                content_json={'text': f'Version {version} content'},
                tokens_used=100,
                model_used='gpt-4o-mini',
                version=version,
                estimated_cost_usd=Decimal('0.000050')
            )

        url = reverse('api_chapter_summaries', kwargs={'chapter_id': self.chapter.id})
        response = self.client.get(f'{url}?prompt_id={self.prompt.id}')

        self.assertEqual(response.status_code, 200)
        response_data = json.loads(response.content)
        versions = response_data['versions']
        self.assertEqual(len(versions), 3)
        # Check ordering (DESC)
        self.assertEqual(versions[0]['version'], 3)
        self.assertEqual(versions[1]['version'], 2)
        self.assertEqual(versions[2]['version'], 1)


class BatchProcessingAPITestCase(TestCase):
    """Test Task Group 3.2: Batch Processing API endpoints."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

        # Create test book and chapters
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            status='completed'
        )
        self.chapters = [
            Chapter.objects.create(
                book=self.book,
                chapter_number=i,
                title=f'Chapter {i}',
                content=f'Content for chapter {i}',
                word_count=10
            )
            for i in range(1, 4)
        ]

        # Create test prompt
        self.prompt = Prompt.objects.create(
            name='batch_prompt',
            template_text='Summarize: {content}',
            category='summarization',
            is_fabric=False
        )

        # Create settings
        Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True
        )

    @patch('books_core.services.cost_control_service.CostControlService.count_tokens')
    @patch('books_core.services.cost_control_service.CostControlService.estimate_cost')
    @patch('books_core.services.cost_control_service.CostControlService.check_limits')
    def test_batch_preview_calculates_total_cost(self, mock_check_limits, mock_estimate_cost, mock_count_tokens):
        """Test batch preview calculates total cost for multiple chapters."""
        # Mock responses
        mock_count_tokens.return_value = 50
        mock_estimate_cost.return_value = {
            'total_tokens': 60,
            'estimated_cost_usd': Decimal('0.000025')
        }
        mock_check_limits.return_value = {
            'daily_usage': {'count': 0, 'limit': 100},
            'monthly_usage': {'cost': Decimal('0'), 'limit': Decimal('5.00')},
            'warnings': []
        }

        url = reverse('api_batch_preview')
        data = {
            'chapter_ids': [ch.id for ch in self.chapters],
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini'
        }
        response = self.client.post(url, json.dumps(data), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        response_data = json.loads(response.content)
        self.assertEqual(response_data['total_chapters'], 3)
        self.assertEqual(len(response_data['chapters']), 3)
        # Total cost = 0.000025 * 3 = 0.000075
        self.assertEqual(response_data['total_cost_usd'], '0.000075')

    def test_batch_generate_creates_processing_job(self):
        """Test batch generate creates ProcessingJob with metadata."""
        url = reverse('api_batch_generate')
        data = {
            'chapter_ids': [ch.id for ch in self.chapters],
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini',
            'confirmed': True
        }
        response = self.client.post(url, json.dumps(data), content_type='application/json')

        self.assertEqual(response.status_code, 202)
        response_data = json.loads(response.content)
        self.assertIn('job_id', response_data)
        self.assertEqual(response_data['status'], 'pending')
        self.assertEqual(response_data['total_chapters'], 3)

        # Verify job was created
        job = ProcessingJob.objects.get(id=response_data['job_id'])
        self.assertEqual(job.status, 'pending')
        self.assertEqual(job.job_type, 'batch_summarization')
        self.assertEqual(len(job.metadata['chapter_ids']), 3)


class PromptManagementAPITestCase(TestCase):
    """Test Task Group 3.3: Prompt Management API endpoints."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

        # Create test prompts
        self.fabric_prompt = Prompt.objects.create(
            name='fabric_summarize',
            template_text='Fabric summarize: {content}',
            category='summarization',
            is_fabric=True
        )
        self.custom_prompt = Prompt.objects.create(
            name='custom_analysis',
            template_text='Custom analyze: {content}',
            category='analysis',
            is_fabric=False,
            is_custom=True
        )

    def test_prompt_list_returns_all_prompts(self):
        """Test prompt list returns all prompts with filter."""
        url = reverse('api_prompts_list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        response_data = json.loads(response.content)
        self.assertEqual(len(response_data['prompts']), 2)

        # Test with is_fabric filter
        response = self.client.get(f'{url}?is_fabric=true')
        response_data = json.loads(response.content)
        self.assertEqual(len(response_data['prompts']), 1)
        self.assertEqual(response_data['prompts'][0]['name'], 'fabric_summarize')

    @patch('books_core.services.fabric_prompt_service.FabricPromptService.sync_prompts')
    def test_fabric_sync_creates_prompts(self, mock_sync):
        """Test Fabric sync creates/updates Prompt records (mocked)."""
        # Mock sync result
        mock_sync.return_value = {
            'synced': 5,
            'failed': ['bad_prompt'],
            'errors': {'bad_prompt': 'Network error'}
        }

        url = reverse('api_fabric_sync')
        response = self.client.post(url)

        self.assertEqual(response.status_code, 200)
        response_data = json.loads(response.content)
        self.assertEqual(response_data['synced'], 5)
        self.assertEqual(len(response_data['failed']), 1)


class SettingsAPITestCase(TestCase):
    """Test Task Group 3.4: Settings & Usage API endpoints."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

        # Create settings
        self.settings = Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True,
            default_model='gpt-4o-mini'
        )

    def test_get_settings_returns_current_values(self):
        """Test GET settings returns current settings."""
        url = reverse('api_settings')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        response_data = json.loads(response.content)
        self.assertEqual(response_data['monthly_limit_usd'], '5.00')
        self.assertEqual(response_data['daily_summary_limit'], 100)
        self.assertTrue(response_data['ai_features_enabled'])

    def test_update_settings_validates_non_negative(self):
        """Test PUT settings validates non-negative values."""
        url = reverse('api_settings')
        data = {'monthly_limit_usd': '-1.00'}
        response = self.client.put(url, json.dumps(data), content_type='application/json')

        self.assertEqual(response.status_code, 400)
        response_data = json.loads(response.content)
        self.assertIn('non-negative', response_data['error'])

    def test_update_settings_persists_changes(self):
        """Test PUT settings updates and persists changes."""
        url = reverse('api_settings')
        data = {
            'monthly_limit_usd': '10.00',
            'daily_summary_limit': 200,
            'ai_features_enabled': False
        }
        response = self.client.put(url, json.dumps(data), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        response_data = json.loads(response.content)
        self.assertEqual(response_data['monthly_limit_usd'], '10.00')
        self.assertEqual(response_data['daily_summary_limit'], 200)
        self.assertFalse(response_data['ai_features_enabled'])

        # Verify in database
        settings = Settings.objects.first()
        self.assertEqual(settings.monthly_limit_usd, Decimal('10.00'))
        self.assertEqual(settings.daily_summary_limit, 200)
        self.assertFalse(settings.ai_features_enabled)

    @patch('books_core.services.cost_control_service.CostControlService.get_current_usage')
    def test_usage_stats_returns_current_usage(self, mock_get_usage):
        """Test usage stats endpoint returns current usage data."""
        # Mock usage data
        mock_get_usage.return_value = {
            'daily': {
                'count': 10,
                'tokens': 5000,
                'cost': Decimal('0.500000'),
                'limit': 100
            },
            'monthly': {
                'count': 50,
                'tokens': 25000,
                'cost': Decimal('2.500000'),
                'limit': Decimal('5.00')
            },
            'settings': {
                'monthly_limit_usd': Decimal('5.00'),
                'daily_summary_limit': 100
            }
        }

        url = reverse('api_usage_stats')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        response_data = json.loads(response.content)
        self.assertIn('daily', response_data)
        self.assertIn('monthly', response_data)
