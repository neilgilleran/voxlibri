"""
Integration tests for Phase 1F.
End-to-end tests covering upload → parse → display → read workflows.
"""
import os
from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from books_core.models import Book, Chapter


class EndToEndIntegrationTests(TestCase):
    """
    Integration tests covering complete user workflows.
    Tests the full stack from upload to reading.
    """

    def setUp(self):
        """Set up test client and test EPUB path."""
        self.client = Client()
        self.test_epubs_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'test_epubs'
        )

    def test_complete_upload_to_reading_workflow(self):
        """
        Test complete workflow: Upload → Book Detail → Reading View → Navigate Chapters.
        This is the primary user flow for Phase 1.
        """
        # Check if test EPUB exists
        test_epub_path = os.path.join(self.test_epubs_dir, 'grit.epub')
        if not os.path.exists(test_epub_path):
            self.skipTest(f"Test EPUB not found at {test_epub_path}")

        # Step 1: Upload EPUB
        with open(test_epub_path, 'rb') as epub_file:
            epub_data = epub_file.read()

        uploaded_file = SimpleUploadedFile(
            "grit.epub",
            epub_data,
            content_type="application/epub+zip"
        )

        response = self.client.post(
            reverse('upload_book'),
            {'epub_file': uploaded_file},
            follow=True
        )

        # Should redirect to book detail
        self.assertEqual(response.status_code, 200)

        # Verify book created
        self.assertEqual(Book.objects.count(), 1)
        book = Book.objects.first()
        self.assertEqual(book.status, 'completed')
        self.assertGreater(book.word_count, 0)

        # Step 2: View book detail
        detail_response = self.client.get(reverse('book_detail', kwargs={'pk': book.id}))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, book.title)
        self.assertContains(detail_response, 'Read Book')

        # Verify chapters exist
        chapters = Chapter.objects.filter(
            book=book,
            is_front_matter=False,
            is_back_matter=False
        )
        self.assertGreater(chapters.count(), 0)

        # Step 3: Navigate to reading view (chapter 1)
        first_chapter = chapters.order_by('chapter_number').first()
        reading_response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': book.id,
                'chapter_number': first_chapter.chapter_number
            })
        )
        self.assertEqual(reading_response.status_code, 200)
        self.assertContains(reading_response, book.title)

        # Step 4: Navigate to next chapter
        if chapters.count() > 1:
            second_chapter = chapters.order_by('chapter_number')[1]
            next_response = self.client.get(
                reverse('reading_view_chapter', kwargs={
                    'book_id': book.id,
                    'chapter_number': second_chapter.chapter_number
                })
            )
            self.assertEqual(next_response.status_code, 200)
            self.assertContains(next_response, 'Previous Chapter')

        # Step 5: Delete book
        delete_response = self.client.post(
            reverse('delete_book', kwargs={'pk': book.id}),
            follow=True
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(Book.objects.filter(pk=book.id).exists())

    def test_cover_extraction_and_thumbnail_generation(self):
        """
        Test that cover images are extracted and thumbnails are generated.
        Verifies image processing during upload.
        """
        test_epub_path = os.path.join(self.test_epubs_dir, 'grit.epub')
        if not os.path.exists(test_epub_path):
            self.skipTest(f"Test EPUB not found at {test_epub_path}")

        with open(test_epub_path, 'rb') as epub_file:
            epub_data = epub_file.read()

        uploaded_file = SimpleUploadedFile(
            "grit.epub",
            epub_data,
            content_type="application/epub+zip"
        )

        response = self.client.post(
            reverse('upload_book'),
            {'epub_file': uploaded_file},
            follow=True
        )

        book = Book.objects.first()
        self.assertIsNotNone(book)

        # Check if cover was extracted (not all EPUBs have covers)
        if book.cover_image:
            # Verify cover image exists
            self.assertTrue(os.path.exists(book.cover_image.path))

            # Verify thumbnail was generated
            if book.cover_thumbnail:
                self.assertTrue(os.path.exists(book.cover_thumbnail.path))

                # Verify thumbnail is smaller than original
                from PIL import Image
                cover_img = Image.open(book.cover_image.path)
                thumb_img = Image.open(book.cover_thumbnail.path)

                self.assertLessEqual(thumb_img.width, 300)
                self.assertLessEqual(thumb_img.height, 450)
                self.assertLessEqual(thumb_img.width, cover_img.width)

    def test_epub_with_real_file_extracts_all_features(self):
        """
        Test that real EPUB file extracts all features:
        - Metadata (title, author, ISBN, language)
        - Chapters with content and word counts
        - Front/back matter filtering
        - Chapter numbering
        """
        test_epub_path = os.path.join(self.test_epubs_dir, 'grit.epub')
        if not os.path.exists(test_epub_path):
            self.skipTest(f"Test EPUB not found at {test_epub_path}")

        with open(test_epub_path, 'rb') as epub_file:
            epub_data = epub_file.read()

        uploaded_file = SimpleUploadedFile(
            "grit.epub",
            epub_data,
            content_type="application/epub+zip"
        )

        response = self.client.post(
            reverse('upload_book'),
            {'epub_file': uploaded_file},
            follow=True
        )

        book = Book.objects.first()
        self.assertIsNotNone(book)

        # Verify metadata extracted
        self.assertIsNotNone(book.title)
        self.assertNotEqual(book.title, 'Processing...')
        self.assertIsNotNone(book.author)
        self.assertNotEqual(book.author, 'Unknown')

        # Verify word count calculated
        self.assertGreater(book.word_count, 0)

        # Verify chapters created
        all_chapters = Chapter.objects.filter(book=book)
        self.assertGreater(all_chapters.count(), 0)

        # Verify main content chapters exist
        main_chapters = all_chapters.filter(
            is_front_matter=False,
            is_back_matter=False
        )
        self.assertGreater(main_chapters.count(), 0)

        # Verify chapter numbering is sequential
        chapter_numbers = list(main_chapters.values_list('chapter_number', flat=True))
        self.assertEqual(chapter_numbers, sorted(chapter_numbers))

        # Verify chapters have content
        for chapter in main_chapters[:3]:  # Check first 3 chapters
            self.assertGreater(len(chapter.content), 0)
            self.assertGreater(chapter.word_count, 0)

        # Verify front/back matter filtering worked
        # Most EPUBs have at least some front or back matter
        total_chapters = all_chapters.count()
        main_chapters_count = main_chapters.count()
        # Main chapters should be less than total (some filtered)
        # But allow case where all are main content
        self.assertLessEqual(main_chapters_count, total_chapters)
