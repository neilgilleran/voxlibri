"""
Tests for book-level summary and analysis feature.
Focused on:
- Summary model book-level support
- Content concatenation
- Batch processing for book analysis
- Views and API endpoints
"""

from django.test import TestCase
from django.db import IntegrityError
from django.core.exceptions import ValidationError
from decimal import Decimal

from books_core.models import Book, Chapter, Prompt, Summary, ProcessingJob
from books_core.services.cost_control_service import CostControlService
from books_core.services.book_analysis_service import BookAnalysisService


class SummaryBookLevelModelTest(TestCase):
    """Tests for Summary model with book-level support."""

    def setUp(self):
        """Set up test data."""
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author'
        )
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Test Chapter',
            content='Test content',
            word_count=2
        )
        self.prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Test {content}',
            category='summarization'
        )

    def test_create_summary_with_book_fk(self):
        """Test creating a Summary with book FK (chapter=null)."""
        summary = Summary.objects.create(
            book=self.book,
            chapter=None,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'content': 'Book summary'},
            model_used='gpt-4o-mini',
            tokens_used=100,
            version=1,
            estimated_cost_usd=Decimal('0.001')
        )

        self.assertIsNotNone(summary.id)
        self.assertEqual(summary.book, self.book)
        self.assertIsNone(summary.chapter)
        self.assertEqual(summary.prompt, self.prompt)

    def test_create_summary_with_chapter_fk(self):
        """Test creating a Summary with chapter FK (book=null)."""
        summary = Summary.objects.create(
            book=None,
            chapter=self.chapter,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'content': 'Chapter summary'},
            model_used='gpt-4o-mini',
            tokens_used=100,
            version=1,
            estimated_cost_usd=Decimal('0.001')
        )

        self.assertIsNotNone(summary.id)
        self.assertIsNone(summary.book)
        self.assertEqual(summary.chapter, self.chapter)
        self.assertEqual(summary.prompt, self.prompt)

    def test_constraint_both_chapter_and_book_fails(self):
        """Test that setting both chapter AND book raises constraint violation."""
        with self.assertRaises(IntegrityError):
            Summary.objects.create(
                book=self.book,
                chapter=self.chapter,  # Both set - should fail
                prompt=self.prompt,
                summary_type='tldr',
                content_json={'content': 'Invalid'},
                model_used='gpt-4o-mini',
                tokens_used=100,
                version=1
            )

    def test_unique_constraint_book_prompt_version(self):
        """Test unique constraint for (book, prompt, version)."""
        # Create first summary
        Summary.objects.create(
            book=self.book,
            chapter=None,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'content': 'First'},
            model_used='gpt-4o-mini',
            tokens_used=100,
            version=1
        )

        # Attempting to create duplicate should fail
        with self.assertRaises(IntegrityError):
            Summary.objects.create(
                book=self.book,
                chapter=None,
                prompt=self.prompt,
                summary_type='tldr',
                content_json={'content': 'Duplicate'},
                model_used='gpt-4o-mini',
                tokens_used=100,
                version=1  # Same version - should fail
            )

    def test_version_tracking_book_summaries(self):
        """Test version tracking for book-level summaries."""
        # Create version 1
        v1 = Summary.objects.create(
            book=self.book,
            chapter=None,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'content': 'Version 1'},
            model_used='gpt-4o-mini',
            tokens_used=100,
            version=1
        )

        # Create version 2 with reference to v1
        v2 = Summary.objects.create(
            book=self.book,
            chapter=None,
            prompt=self.prompt,
            summary_type='tldr',
            content_json={'content': 'Version 2'},
            model_used='gpt-4o-mini',
            tokens_used=100,
            version=2,
            previous_version=v1
        )

        self.assertEqual(v2.version, 2)
        self.assertEqual(v2.previous_version, v1)

        # Get latest version
        latest = Summary.objects.filter(
            book=self.book,
            prompt=self.prompt
        ).order_by('-version').first()

        self.assertEqual(latest.version, 2)


class ProcessingJobBookAnalysisTest(TestCase):
    """Test ProcessingJob support for book_analysis job type."""

    def test_job_type_book_analysis_exists(self):
        """Test that 'book_analysis' is a valid job type."""
        book = Book.objects.create(
            title='Test Book',
            author='Test Author'
        )

        job = ProcessingJob.objects.create(
            book=book,
            job_type='book_analysis',
            status='pending'
        )

        self.assertEqual(job.job_type, 'book_analysis')
        self.assertEqual(job.get_job_type_display(), 'Book Analysis')


class ContentConcatenationTest(TestCase):
    """Tests for book content concatenation."""

    def setUp(self):
        """Set up test data."""
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author'
        )
        self.service = BookAnalysisService()

    def test_concatenate_all_chapters_in_order(self):
        """Test concatenating all chapters in correct order."""
        # Create chapters
        Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter One',
            content='Content of chapter one.',
            word_count=4
        )
        Chapter.objects.create(
            book=self.book,
            chapter_number=2,
            title='Chapter Two',
            content='Content of chapter two.',
            word_count=4
        )

        # Concatenate
        result = self.service.concatenate_book_content(self.book)

        # Should include both chapters in order
        self.assertIn('Chapter One', result)
        self.assertIn('Content of chapter one', result)
        self.assertIn('Chapter Two', result)
        self.assertIn('Content of chapter two', result)

        # Verify order
        idx_one = result.index('Chapter One')
        idx_two = result.index('Chapter Two')
        self.assertLess(idx_one, idx_two)

    def test_exclude_front_matter_chapters(self):
        """Test excluding front matter chapters."""
        # Create regular chapter
        Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Regular Chapter',
            content='Regular content.',
            word_count=2,
            is_front_matter=False
        )

        # Create front matter chapter
        Chapter.objects.create(
            book=self.book,
            chapter_number=2,
            title='Table of Contents',
            content='TOC content.',
            word_count=2,
            is_front_matter=True
        )

        # Concatenate
        result = self.service.concatenate_book_content(self.book)

        # Should include regular chapter but not front matter
        self.assertIn('Regular Chapter', result)
        self.assertNotIn('Table of Contents', result)

    def test_exclude_back_matter_chapters(self):
        """Test excluding back matter chapters."""
        # Create regular chapter
        Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Regular Chapter',
            content='Regular content.',
            word_count=2,
            is_back_matter=False
        )

        # Create back matter chapter
        Chapter.objects.create(
            book=self.book,
            chapter_number=2,
            title='About the Author',
            content='Author bio.',
            word_count=2,
            is_back_matter=True
        )

        # Concatenate
        result = self.service.concatenate_book_content(self.book)

        # Should include regular chapter but not back matter
        self.assertIn('Regular Chapter', result)
        self.assertNotIn('About the Author', result)

    def test_handle_book_with_no_chapters(self):
        """Test handling books with no chapters."""
        # Concatenate empty book
        result = self.service.concatenate_book_content(self.book)

        # Should return empty string or minimal content
        self.assertEqual(result, '')
