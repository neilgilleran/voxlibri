"""
Tests for ContentSplitter service.

Focused tests for chapter splitting and front/back matter detection.
"""

from django.test import TestCase

from books_core.models import Book, Chapter
from books_core.services.content_splitter import ContentSplitter


class ContentSplitterTestCase(TestCase):
    """Test ContentSplitter service."""

    def setUp(self):
        """Set up test fixtures."""
        self.splitter = ContentSplitter()
        self.book = Book.objects.create(
            title="Test Book",
            author="Test Author",
            status="processing"
        )

    def test_split_chapters_creates_chapter_objects(self):
        """Test that split_chapters creates Chapter objects in database."""
        chapters_data = [
            {
                'title': 'Chapter 1',
                'html_content': '<h1>Chapter 1</h1><p>' + ('Test content. ' * 100) + '</p>',
                'order_index': 0
            },
            {
                'title': 'Chapter 2',
                'html_content': '<h1>Chapter 2</h1><p>' + ('More content. ' * 100) + '</p>',
                'order_index': 1
            }
        ]

        created_chapters = self.splitter.split_chapters(self.book, chapters_data)

        self.assertEqual(len(created_chapters), 2)
        self.assertEqual(created_chapters[0].chapter_number, 1)
        self.assertEqual(created_chapters[1].chapter_number, 2)
        self.assertEqual(created_chapters[0].title, 'Chapter 1')
        self.assertEqual(created_chapters[1].title, 'Chapter 2')
        self.assertGreater(created_chapters[0].word_count, 0)

    def test_front_matter_detection_by_title(self):
        """Test that front matter is detected by title patterns."""
        test_cases = [
            ('Table of Contents', True),
            ('Copyright Page', True),
            ('Dedication', True),
            ('Foreword', True),
            ('Preface', True),
            ('Chapter 1', False),
        ]

        for title, expected in test_cases:
            result = self.splitter._is_front_matter(title, 0, 10)
            self.assertEqual(
                result, expected,
                f"Title '{title}' should {'be' if expected else 'not be'} front matter"
            )

    def test_back_matter_detection_by_title(self):
        """Test that back matter is detected by title patterns."""
        test_cases = [
            ('Acknowledgments', True),
            ('About the Author', True),
            ('Also by This Author', True),
            ('Epilogue', True),
            ('Afterword', True),
            ('Chapter 20', False),
        ]

        for title, expected in test_cases:
            result = self.splitter._is_back_matter(title, 19, 20)
            self.assertEqual(
                result, expected,
                f"Title '{title}' should {'be' if expected else 'not be'} back matter"
            )

    def test_low_word_count_chapters_skipped(self):
        """Test that chapters with very low word count are skipped."""
        chapters_data = [
            {
                'title': 'Valid Chapter',
                'html_content': '<p>' + ('Test content. ' * 100) + '</p>',  # High word count
                'order_index': 0
            },
            {
                'title': 'Empty Chapter',
                'html_content': '<p>Short.</p>',  # Only 1 word
                'order_index': 1
            }
        ]

        created_chapters = self.splitter.split_chapters(self.book, chapters_data)

        # Only the first chapter should be created (second has < 50 words)
        self.assertEqual(len(created_chapters), 1)
        self.assertEqual(created_chapters[0].title, 'Valid Chapter')

    def test_word_count_calculation(self):
        """Test that word count is calculated correctly."""
        text = "This is a test sentence with exactly ten words here."
        word_count = self.splitter._count_words(text)

        # Count the words: This, is, a, test, sentence, with, exactly, ten, words, here = 10 words
        self.assertEqual(word_count, 10)

    def test_chapter_numbering_sequential(self):
        """Test that chapter numbers are sequential starting from 1."""
        chapters_data = [
            {
                'title': f'Chapter {i}',
                'html_content': f'<p>{"Content. " * 100}</p>',
                'order_index': i
            }
            for i in range(5)
        ]

        created_chapters = self.splitter.split_chapters(self.book, chapters_data)

        self.assertEqual(len(created_chapters), 5)
        for i, chapter in enumerate(created_chapters, start=1):
            self.assertEqual(chapter.chapter_number, i)

    def test_front_and_back_matter_flags_set(self):
        """Test that front/back matter flags are set correctly on chapters."""
        chapters_data = [
            {
                'title': 'Table of Contents',
                # Use enough content to pass the minimum word count threshold
                'html_content': '<p>' + ('TOC content. ' * 50) + '</p>',
                'order_index': 0
            },
            {
                'title': 'Chapter 1',
                'html_content': '<p>' + ('Main content. ' * 100) + '</p>',
                'order_index': 1
            },
            {
                'title': 'About the Author',
                # Use enough content to pass the minimum word count threshold
                'html_content': '<p>' + ('Author bio. ' * 50) + '</p>',
                'order_index': 2
            }
        ]

        created_chapters = self.splitter.split_chapters(self.book, chapters_data)

        self.assertEqual(len(created_chapters), 3)

        # First chapter should be front matter
        self.assertTrue(created_chapters[0].is_front_matter)
        self.assertFalse(created_chapters[0].is_back_matter)

        # Second chapter should be main content
        self.assertFalse(created_chapters[1].is_front_matter)
        self.assertFalse(created_chapters[1].is_back_matter)

        # Third chapter should be back matter
        self.assertFalse(created_chapters[2].is_front_matter)
        self.assertTrue(created_chapters[2].is_back_matter)
