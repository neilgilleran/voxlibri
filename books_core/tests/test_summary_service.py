"""
Tests for SummaryService.

Focused tests covering:
- Version number calculation
- Version linking (previous_version FK)
- Summary creation
- Version retrieval
"""

from decimal import Decimal
from django.test import TestCase

from books_core.models import Book, Chapter, Prompt, Summary
from books_core.services.summary_service import SummaryService


class SummaryServiceTestCase(TestCase):
    """Tests for SummaryService."""

    def setUp(self):
        """Set up test fixtures."""
        self.service = SummaryService()

        # Create test book and chapter
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            epub_file='test.epub'
        )
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter 1',
            content='Chapter content here.',
            word_count=100
        )

        # Create test prompt
        self.prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Summarize: {content}',
            category='summarization'
        )

    def test_get_next_version_first_version(self):
        """Test version calculation when no summaries exist."""
        # Get next version
        version, previous = self.service.get_next_version(self.chapter, self.prompt)

        # Should be version 1 with no previous
        self.assertEqual(version, 1)
        self.assertIsNone(previous)

    def test_get_next_version_increments_correctly(self):
        """Test version increments for same chapter+prompt (1, 2, 3...)."""
        # Create first summary
        summary1 = Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'text': 'Summary 1'},
            tokens_used=100,
            model_used='gpt-4o-mini',
            version=1,
            estimated_cost_usd=Decimal('0.001')
        )

        # Get next version
        version, previous = self.service.get_next_version(self.chapter, self.prompt)

        # Should be version 2 with previous pointing to summary1
        self.assertEqual(version, 2)
        self.assertEqual(previous.id, summary1.id)

        # Create second summary
        summary2 = Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'text': 'Summary 2'},
            tokens_used=100,
            model_used='gpt-4o-mini',
            version=2,
            previous_version=summary1,
            estimated_cost_usd=Decimal('0.001')
        )

        # Get next version again
        version, previous = self.service.get_next_version(self.chapter, self.prompt)

        # Should be version 3 with previous pointing to summary2
        self.assertEqual(version, 3)
        self.assertEqual(previous.id, summary2.id)

    def test_create_summary_links_previous_version(self):
        """Test that create_summary links previous_version FK correctly."""
        # Create first summary
        summary1 = self.service.create_summary(
            chapter=self.chapter,
            prompt=self.prompt,
            content='First summary content',
            metadata={
                'tokens_used': 100,
                'model_used': 'gpt-4o-mini',
                'processing_time_ms': 1000,
                'estimated_cost_usd': Decimal('0.001')
            }
        )

        # Verify first summary
        self.assertEqual(summary1.version, 1)
        self.assertIsNone(summary1.previous_version)

        # Create second summary (re-run same prompt)
        summary2 = self.service.create_summary(
            chapter=self.chapter,
            prompt=self.prompt,
            content='Second summary content',
            metadata={
                'tokens_used': 120,
                'model_used': 'gpt-4o-mini',
                'processing_time_ms': 1100,
                'estimated_cost_usd': Decimal('0.0012')
            }
        )

        # Verify second summary links to first
        self.assertEqual(summary2.version, 2)
        self.assertEqual(summary2.previous_version.id, summary1.id)

        # Create third summary
        summary3 = self.service.create_summary(
            chapter=self.chapter,
            prompt=self.prompt,
            content='Third summary content',
            metadata={
                'tokens_used': 130,
                'model_used': 'gpt-4o-mini',
                'processing_time_ms': 1200,
                'estimated_cost_usd': Decimal('0.0013')
            }
        )

        # Verify third summary links to second
        self.assertEqual(summary3.version, 3)
        self.assertEqual(summary3.previous_version.id, summary2.id)

    def test_create_summary_updates_has_summary_flag(self):
        """Test that chapter.has_summary is set on first summary."""
        # Initially False
        self.assertFalse(self.chapter.has_summary)

        # Create summary
        self.service.create_summary(
            chapter=self.chapter,
            prompt=self.prompt,
            content='Summary',
            metadata={
                'tokens_used': 100,
                'model_used': 'gpt-4o-mini',
                'processing_time_ms': 1000,
                'estimated_cost_usd': Decimal('0.001')
            }
        )

        # Refresh from database
        self.chapter.refresh_from_db()

        # Should now be True
        self.assertTrue(self.chapter.has_summary)

    def test_get_versions_returns_correct_order(self):
        """Test that versions are returned in DESC order (newest first)."""
        # Create multiple versions
        for i in range(1, 4):
            Summary.objects.create(
                chapter=self.chapter,
                prompt=self.prompt,
                summary_type='tldr',
                content_json={'text': f'Summary {i}'},
                tokens_used=100,
                model_used='gpt-4o-mini',
                version=i,
                estimated_cost_usd=Decimal('0.001')
            )

        # Get versions
        versions = self.service.get_versions(self.chapter, self.prompt)

        # Should be ordered newest first
        version_numbers = [v.version for v in versions]
        self.assertEqual(version_numbers, [3, 2, 1])

    def test_get_latest_summary(self):
        """Test getting most recent version."""
        # Create multiple versions
        for i in range(1, 4):
            Summary.objects.create(
                chapter=self.chapter,
                prompt=self.prompt,
                summary_type='tldr',
                content_json={'text': f'Summary {i}'},
                tokens_used=100,
                model_used='gpt-4o-mini',
                version=i,
                estimated_cost_usd=Decimal('0.001')
            )

        # Get latest
        latest = self.service.get_latest_summary(self.chapter, self.prompt)

        # Should be version 3
        self.assertEqual(latest.version, 3)

    def test_compare_versions(self):
        """Test version comparison."""
        # Create two summaries
        summary1 = Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'text': 'Summary 1'},
            tokens_used=100,
            model_used='gpt-4o-mini',
            version=1,
            estimated_cost_usd=Decimal('0.001')
        )

        summary2 = Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'text': 'Summary 2'},
            tokens_used=120,
            model_used='gpt-4o-mini',
            version=2,
            estimated_cost_usd=Decimal('0.0012')
        )

        # Compare
        comparison = self.service.compare_versions(summary1, summary2)

        # Verify comparison data
        self.assertEqual(comparison['summary1']['version'], 1)
        self.assertEqual(comparison['summary2']['version'], 2)
        self.assertTrue(comparison['same_chapter'])
        self.assertTrue(comparison['same_prompt'])
