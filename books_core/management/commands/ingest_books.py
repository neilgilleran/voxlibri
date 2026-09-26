"""
Load EPUB/PDF files into VoxLibri from the command line, and optionally run the
full analysis pipeline, without the web upload page or a qcluster worker.

    python manage.py ingest_books ~/FromLaptop/books --analyze --limit 1
    python manage.py ingest_books path/to/book.epub --analyze
    python manage.py ingest_books ~/FromLaptop/books --list

Directories are searched recursively. A file is skipped when a book with the same
source filename is already loaded (so re-running over a folder only picks up new
drops). --analyze runs the chapter pipeline and the book-level aggregation inline
(Django-Q sync mode), which is what the Lemmy synopsis bot reads.

Pair with VOXLIBRI_LLM_PROVIDER=codex (or claude) to run on a subscription instead
of the paid API; the command refuses --analyze on the paid API unless --allow-paid.
"""

import os
import logging
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from books_core.models import Book
from books_core.services import subscription_llm

logger = logging.getLogger(__name__)
EXTS = ('.epub', '.pdf')


def _find(paths):
    seen = set()
    for raw in paths:
        p = Path(raw).expanduser()
        items = sorted(p.rglob('*')) if p.is_dir() else [p]
        for f in items:
            if f.is_file() and f.suffix.lower() in EXTS and f.name not in seen:
                seen.add(f.name)
                yield f


def _loaded_names():
    return {os.path.basename(b.source_file.name) for b in Book.objects.exclude(source_file='')}


def ingest_file(path: Path, book_type: str = 'nonfiction') -> Book:
    """Same steps as UploadBookView.form_valid, from a path on disk."""
    from PIL import Image
    from django.core.files.base import ContentFile
    from books_core.services.content_splitter import ContentSplitter

    is_pdf = path.suffix.lower() == '.pdf'
    book = Book.objects.create(title='Processing...', author='Unknown', status='processing',
                               file_type='pdf' if is_pdf else 'epub', book_type=book_type)
    try:
        with open(path, 'rb') as fh:
            book.source_file.save(path.name, File(fh), save=True)
        if is_pdf:
            from books_core.services.pdf_parser import PDFParserService
            parsed = PDFParserService().parse_pdf(book.source_file.path)
        else:
            from books_core.services.epub_parser import EPUBParserService
            parsed = EPUBParserService().parse_epub(book.source_file.path)
        if not parsed.get('chapters'):
            raise ValueError('No readable chapters (DRM, corrupt, or unsupported layout)')

        meta = parsed.get('metadata', {})
        book.title = meta.get('title') or path.stem
        book.author = meta.get('author') or 'Unknown Author'
        book.isbn = meta.get('isbn')
        book.language = meta.get('language')

        if parsed.get('cover_image'):
            book.cover_image.save(f'cover_{book.id}.jpg', ContentFile(parsed['cover_image']), save=False)
            try:
                img = Image.open(book.cover_image.path)
                if img.mode in ('RGBA', 'P', 'CMYK', 'LA'):
                    img = img.convert('RGB')
                img.thumbnail((300, 450), Image.Resampling.LANCZOS)
                thumb_dir = os.path.join(os.path.dirname(book.cover_image.path), 'thumbs')
                os.makedirs(thumb_dir, exist_ok=True)
                img.save(os.path.join(thumb_dir, f'thumb_{book.id}.jpg'), 'JPEG', quality=85)
                book.cover_thumbnail = os.path.join('books', 'covers', 'thumbs', f'thumb_{book.id}.jpg')
            except Exception as e:  # thumbnail is cosmetic
                logger.warning(f'thumbnail failed for book {book.id}: {e}')

        chapters = ContentSplitter().split_chapters(book=book, chapters_data=parsed['chapters'])
        book.word_count = sum(c.word_count for c in chapters if not c.is_front_matter and not c.is_back_matter)
        book.status = 'completed'
        book.processed_at = timezone.now()
        book.save()
        try:
            from books_core.services.readability_service import ReadabilityService
            ReadabilityService().compute_all_for_book(book)
        except Exception as e:
            logger.warning(f'readability failed for book {book.id}: {e}')
        return book
    except Exception as e:
        book.status = 'failed'
        book.error_message = str(e)
        book.processed_at = timezone.now()
        book.save()
        raise


def analyze(book: Book, model: str):
    """Run the chapter pipeline + aggregation inline (Django-Q sync mode)."""
    from books_core.models import ProcessingJob
    from books_core.services.chapter_analysis_pipeline_service import ChapterAnalysisPipelineService

    # Django-Q copies Q_CLUSTER into Conf at import, so flip Conf itself; setting
    # settings.Q_CLUSTER here only queued the tasks for a worker that isn't running.
    from django_q.conf import Conf
    Conf.SYNC = True
    job =ChapterAnalysisPipelineService(model=model).run_pipeline(book, model)
    job.refresh_from_db()
    return job


class Command(BaseCommand):
    help = 'Load EPUB/PDF files (files or folders) into VoxLibri; optionally analyse them.'

    def add_arguments(self, parser):
        parser.add_argument('paths', nargs='+')
        parser.add_argument('--analyze', action='store_true', help='run the full analysis pipeline after loading')
        parser.add_argument('--limit', type=int, default=0, help='stop after N new books (0 = all)')
        parser.add_argument('--list', action='store_true', help='show what would be loaded, change nothing')
        parser.add_argument('--book-type', default='nonfiction', choices=['nonfiction', 'fiction'])
        parser.add_argument('--model', default='gpt-4o-mini', help='only used on the paid API path')
        parser.add_argument('--allow-paid', action='store_true', help='permit --analyze on the paid OpenAI API')

    def handle(self, *args, **opts):
        on_plan = subscription_llm.provider() in subscription_llm.PROVIDERS
        if opts['analyze'] and not on_plan and not opts['allow_paid']:
            raise CommandError('Refusing --analyze on the paid API. Set VOXLIBRI_LLM_PROVIDER=codex '
                               '(or claude), or pass --allow-paid.')

        # Django stores "Hooked (Nir Eyal).epub" as "Hooked_Nir_Eyal.epub", so compare
        # the cleaned name; comparing raw names re-ingested every book daily.
        from django.core.files.storage import default_storage
        loaded = _loaded_names()
        todo = [f for f in _find(opts['paths'])
                if f.name not in loaded and default_storage.get_valid_name(f.name) not in loaded]
        self.stdout.write(f'{len(todo)} new file(s); provider={subscription_llm.provider()}')
        if opts['list']:
            for f in todo:
                self.stdout.write(f'  {f.name}')
            return

        done = 0
        for f in todo:
            if opts['limit'] and done >= opts['limit']:
                break
            self.stdout.write(f'→ {f.name}')
            try:
                book = ingest_file(f, opts['book_type'])
            except Exception as e:
                self.stderr.write(f'  ✗ load failed: {e}')
                continue
            chapters = book.chapters.filter(is_front_matter=False, is_back_matter=False).count()
            self.stdout.write(f'  ✓ book #{book.id} "{book.title}" by {book.author}: {chapters} chapters')
            if opts['analyze']:
                try:
                    job = analyze(book, opts['model'])
                    self.stdout.write(f'  ✓ analysis job #{job.id}: {job.status} ({job.progress_percent}%)')
                except Exception as e:
                    self.stderr.write(f'  ✗ analysis failed: {e}')
            done += 1
        self.stdout.write(f'loaded {done} book(s)')
