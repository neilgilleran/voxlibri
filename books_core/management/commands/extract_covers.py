"""
Django management command to extract cover images from existing books.
Run with: python manage.py extract_covers
"""

from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from books_core.models import Book
from books_core.services.epub_parser import EPUBParserService
from PIL import Image
import os
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Extract cover images from existing books that are missing covers'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Re-extract covers for all books, even if they already have covers',
        )

    def handle(self, *args, **options):
        self.stdout.write("Extracting cover images from books...")
        self.stdout.write("=" * 60)

        # Get books without covers, or all books if --all flag is set
        if options['all']:
            books = Book.objects.filter(status='completed')
            self.stdout.write("Processing all books...")
        else:
            books = Book.objects.filter(status='completed', cover_image='')
            self.stdout.write("Processing books without covers...")

        if not books.exists():
            self.stdout.write(self.style.SUCCESS("No books need cover extraction."))
            return

        success_count = 0
        skip_count = 0
        error_count = 0

        for book in books:
            try:
                # Check if book has EPUB file
                if not book.epub_file or not book.epub_file.path:
                    self.stdout.write(self.style.WARNING(f"⊘ Skipped: {book.title} (no EPUB file)"))
                    skip_count += 1
                    continue

                # Parse EPUB to extract cover
                parser = EPUBParserService()
                parse_result = parser.parse_epub(book.epub_file.path)

                # Check if cover was extracted
                if not parse_result.get('cover_image'):
                    self.stdout.write(self.style.WARNING(f"⊘ No cover found: {book.title}"))
                    skip_count += 1
                    continue

                # Save cover image
                cover_data = parse_result['cover_image']
                cover_filename = f"cover_{book.id}.jpg"

                # Clear old cover if re-extracting
                if book.cover_image:
                    try:
                        book.cover_image.delete(save=False)
                    except Exception:
                        pass

                book.cover_image.save(cover_filename, ContentFile(cover_data), save=False)

                # Generate thumbnail (300x450 max, maintain aspect ratio)
                try:
                    img = Image.open(book.cover_image.path)
                    img.thumbnail((300, 450), Image.Resampling.LANCZOS)

                    thumb_filename = f"thumb_{book.id}.jpg"
                    thumb_dir = os.path.join(
                        os.path.dirname(book.cover_image.path), 'thumbs'
                    )
                    os.makedirs(thumb_dir, exist_ok=True)

                    thumb_full_path = os.path.join(thumb_dir, thumb_filename)
                    img.save(thumb_full_path, 'JPEG', quality=85)
                    book.cover_thumbnail = os.path.join('books', 'covers', 'thumbs', thumb_filename)

                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  Failed to generate thumbnail: {e}"))

                book.save()

                self.stdout.write(self.style.SUCCESS(f"✓ Extracted: {book.title}"))
                success_count += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ Error with {book.title}: {str(e)}"))
                logger.exception(f"Failed to extract cover for book {book.id}")
                error_count += 1

        self.stdout.write("=" * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f"Summary: {success_count} extracted, {skip_count} skipped, {error_count} errors"
            )
        )
