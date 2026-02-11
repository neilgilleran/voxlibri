"""
Phase 6: Frontend Modals & Real-Time Progress Tests

Tests for modals, HTMX integration, and WebSocket client functionality.
Following minimal testing philosophy: 2-8 focused tests per task group.
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from books_core.models import Book, Chapter, Prompt, Summary, Settings
from decimal import Decimal
import json


class CostPreviewModalTests(TestCase):
    """
    Task Group 6.1: Cost Preview Modal
    Tests: 2-8 focused tests for modal behavior
    """

    def setUp(self):
        """Set up test data"""
        self.client = Client()

        # Create settings
        Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True
        )

        # Create a mock EPUB file
        epub_content = b'Mock EPUB file content'
        epub_file = SimpleUploadedFile(
            "test_book.epub",
            epub_content,
            content_type="application/epub+zip"
        )

        # Create a test book with chapters
        self.book = Book.objects.create(
            title="Test Book for Modals",
            author="Test Author",
            epub_file=epub_file,
            word_count=10000,
            status='completed'
        )

        # Create chapters
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title="Chapter 1",
            content="Content for chapter 1. " * 100,
            word_count=500
        )

        # Create a prompt
        self.prompt = Prompt.objects.create(
            name="Test Summarize",
            template_text="Summarize this: {{ content }}",
            category="summarization",
            is_fabric=True
        )

    def test_reading_view_includes_modal_css(self):
        """Test that reading view includes modals.css"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'modals.css')

    def test_reading_view_includes_modal_javascript(self):
        """Test that reading view includes modals.js"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'modals.js')

    def test_cost_preview_modal_exists_in_template(self):
        """Test that cost preview modal structure exists"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="cost-preview-modal"')
        self.assertContains(response, 'Cost Preview')
        self.assertContains(response, 'Confirm & Generate')

    def test_modal_has_accessibility_attributes(self):
        """Test that modals have proper ARIA attributes"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="dialog"')
        self.assertContains(response, 'aria-modal="true"')
        self.assertContains(response, 'aria-labelledby')

    def test_modal_has_close_button(self):
        """Test that modals have close buttons"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'modal-close')
        self.assertContains(response, 'aria-label="Close modal"')

    def test_cost_preview_api_endpoint_available(self):
        """Test that cost preview API endpoint works"""
        url = f'/api/chapters/{self.chapter.id}/summary-preview/'
        data = {
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini'
        }

        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertIn('estimated_tokens', response_data)
        self.assertIn('estimated_cost_usd', response_data)
        self.assertIn('daily_usage', response_data)
        self.assertIn('monthly_usage', response_data)

    def test_generate_button_present(self):
        """Test that generate summary button is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="generate-summary"')
        self.assertContains(response, 'Generate Summary')


class BatchModalTests(TestCase):
    """
    Task Group 6.2: Batch Preview & Progress Modals
    Tests: 2-8 focused tests for batch processing UI
    """

    def setUp(self):
        """Set up test data"""
        self.client = Client()

        # Create settings
        Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True
        )

        # Create a mock EPUB file
        epub_content = b'Mock EPUB file content'
        epub_file = SimpleUploadedFile(
            "test_book.epub",
            epub_content,
            content_type="application/epub+zip"
        )

        # Create a test book with chapters
        self.book = Book.objects.create(
            title="Test Book for Batch",
            author="Test Author",
            epub_file=epub_file,
            word_count=10000,
            status='completed'
        )

        # Create multiple chapters
        self.chapters = []
        for i in range(1, 4):
            chapter = Chapter.objects.create(
                book=self.book,
                chapter_number=i,
                title=f"Chapter {i}",
                content=f"Content for chapter {i}. " * 100,
                word_count=500
            )
            self.chapters.append(chapter)

        # Create a prompt
        self.prompt = Prompt.objects.create(
            name="Test Summarize",
            template_text="Summarize this: {{ content }}",
            category="summarization",
            is_fabric=True
        )

    def test_batch_preview_modal_exists(self):
        """Test that batch preview modal structure exists"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="batch-preview-modal"')
        self.assertContains(response, 'Batch Generation Preview')
        self.assertContains(response, 'Confirm & Start Batch')

    def test_batch_progress_modal_exists(self):
        """Test that batch progress modal structure exists"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="batch-progress-modal"')
        self.assertContains(response, 'Batch Generation Progress')

    def test_batch_generate_button_present(self):
        """Test that batch generate button is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="batch-generate"')
        self.assertContains(response, 'Batch Generate')

    def test_batch_preview_api_endpoint_available(self):
        """Test that batch preview API endpoint works"""
        url = '/api/summaries/batch-preview/'
        data = {
            'chapter_ids': [c.id for c in self.chapters],
            'prompt_id': self.prompt.id,
            'model': 'gpt-4o-mini'
        }

        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertIn('total_cost_usd', response_data)
        self.assertIn('total_tokens', response_data)
        self.assertIn('chapters', response_data)
        self.assertEqual(len(response_data['chapters']), 3)

    def test_batch_controls_present(self):
        """Test that batch control buttons are present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="select-all"')
        self.assertContains(response, 'id="deselect-all"')
        self.assertContains(response, 'Select All')
        self.assertContains(response, 'Deselect All')

    def test_batch_count_text_present(self):
        """Test that batch count text element is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="batch-count-text"')
        self.assertContains(response, 'No chapters selected')


class VersionComparisonModalTests(TestCase):
    """
    Task Group 6.3: Version Comparison Modal
    Tests: 2-8 focused tests for comparison modal
    """

    def setUp(self):
        """Set up test data"""
        self.client = Client()

        # Create settings
        Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True
        )

        # Create a mock EPUB file
        epub_content = b'Mock EPUB file content'
        epub_file = SimpleUploadedFile(
            "test_book.epub",
            epub_content,
            content_type="application/epub+zip"
        )

        # Create a test book with chapters
        self.book = Book.objects.create(
            title="Test Book for Versions",
            author="Test Author",
            epub_file=epub_file,
            word_count=10000,
            status='completed'
        )

        # Create chapter
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title="Chapter 1",
            content="Content for chapter 1. " * 100,
            word_count=500
        )

        # Create a prompt
        self.prompt = Prompt.objects.create(
            name="Test Summarize",
            template_text="Summarize this: {{ content }}",
            category="summarization",
            is_fabric=True
        )

        # Create multiple summary versions
        self.summary1 = Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'text': 'Summary version 1'},
            tokens_used=500,
            model_used='gpt-4o-mini',
            processing_time_ms=1000,
            version=1,
            estimated_cost_usd=Decimal('0.001')
        )

        self.summary2 = Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'text': 'Summary version 2'},
            tokens_used=520,
            model_used='gpt-4o-mini',
            processing_time_ms=1100,
            version=2,
            previous_version=self.summary1,
            estimated_cost_usd=Decimal('0.0012')
        )

    def test_version_comparison_modal_exists(self):
        """Test that version comparison modal structure exists"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="version-comparison-modal"')
        self.assertContains(response, 'Compare Versions')

    def test_compare_versions_button_present(self):
        """Test that compare versions button is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="compare-versions"')
        self.assertContains(response, 'Compare Versions')

    def test_version_selector_present(self):
        """Test that version selector dropdown is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="version-selector"')

    def test_summary_detail_api_endpoint_available(self):
        """Test that summary detail API endpoint works"""
        url = f'/api/summaries/{self.summary1.id}/'

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertIn('id', response_data)
        self.assertIn('version', response_data)
        self.assertIn('content', response_data)


class HTMXIntegrationTests(TestCase):
    """
    Task Group 6.4: HTMX Integration & Dynamic Updates
    Tests: 2-8 focused tests for HTMX interactions
    """

    def setUp(self):
        """Set up test data"""
        self.client = Client()

        # Create settings
        Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True
        )

        # Create a mock EPUB file
        epub_content = b'Mock EPUB file content'
        epub_file = SimpleUploadedFile(
            "test_book.epub",
            epub_content,
            content_type="application/epub+zip"
        )

        # Create a test book with chapters
        self.book = Book.objects.create(
            title="Test Book for HTMX",
            author="Test Author",
            epub_file=epub_file,
            word_count=10000,
            status='completed'
        )

        # Create chapter
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title="Chapter 1",
            content="Content for chapter 1. " * 100,
            word_count=500
        )

        # Create prompts
        for i in range(1, 4):
            Prompt.objects.create(
                name=f"Test Prompt {i}",
                template_text=f"Template {i}: {{{{ content }}}}",
                category="summarization",
                is_fabric=True
            )

    def test_htmx_library_included(self):
        """Test that HTMX library is included"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'htmx.org')

    def test_prompts_api_endpoint_available(self):
        """Test that prompts API endpoint works"""
        url = '/api/prompts/?is_fabric=true'

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertIn('prompts', response_data)
        self.assertEqual(len(response_data['prompts']), 3)

    def test_prompt_selector_present(self):
        """Test that prompt selector dropdown is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="prompt-selector"')
        self.assertContains(response, 'Select AI prompt')

    def test_model_selector_present(self):
        """Test that model selector dropdown is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="model-selector"')
        self.assertContains(response, 'gpt-4o-mini')

    def test_summary_display_area_present(self):
        """Test that summary display area is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="summary-display"')
        self.assertContains(response, 'summary-empty-state')

    def test_reading_view_data_attributes_present(self):
        """Test that reading view data attributes are present for JavaScript"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="reading-view-data"')
        self.assertContains(response, f'data-book-id="{self.book.id}"')
        self.assertContains(response, f'data-chapter-id="{self.chapter.id}"')
        self.assertContains(response, 'data-chapter-number="1"')
