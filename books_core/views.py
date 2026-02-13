"""
Views for books_core app.
"""
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import FormView, ListView, DetailView, TemplateView, DeleteView
from django.contrib import messages
from django.urls import reverse_lazy
from django.utils import timezone
from django.db.models import Sum
from PIL import Image
import os
from datetime import date

from .models import Book, Chapter, Settings, UsageTracking
from .forms import UploadBookForm, SettingsForm
from .services.epub_parser import EPUBParserService
from .services.content_splitter import ContentSplitter

logger = logging.getLogger(__name__)


class UploadBookView(FormView):
    """
    View for uploading EPUB or PDF files.
    Handles file upload, parsing, and chapter extraction.
    """
    template_name = 'books_core/upload.html'
    form_class = UploadBookForm

    def get_success_url(self):
        """Return URL to book detail page after successful upload."""
        # Redirect to appropriate section based on book type
        if self.book_type == 'fiction':
            return reverse_lazy('fiction:book_detail', kwargs={'pk': self.book_id})
        return reverse_lazy('nonfiction:book_detail', kwargs={'pk': self.book_id})

    def form_valid(self, form):
        """
        Process valid form submission:
        1. Create Book instance with status='uploaded'
        2. Validate and save source file (EPUB or PDF)
        3. Parse file and extract metadata using appropriate parser
        4. Extract and save cover image + thumbnail
        5. Split content into chapters
        6. Calculate total word count
        7. Set status='completed' or 'failed' based on result
        """
        book_file = form.cleaned_data['book_file']
        book_type = form.cleaned_data.get('book_type', 'nonfiction')

        # Detect file type
        is_pdf = book_file.name.lower().endswith('.pdf')
        file_type = 'pdf' if is_pdf else 'epub'

        # Create Book instance
        book = Book.objects.create(
            title='Processing...',
            author='Unknown',
            status='uploaded',
            file_type=file_type,
            book_type=book_type
        )

        try:
            # Update status to processing
            book.status = 'processing'
            book.save()

            # Save source file
            book.source_file.save(book_file.name, book_file, save=True)

            # Parse using appropriate parser
            if is_pdf:
                from .services.pdf_parser import PDFParserService
                parser = PDFParserService()
                parse_result = parser.parse_pdf(book.source_file.path)
            else:
                parser = EPUBParserService()
                parse_result = parser.parse_epub(book.source_file.path)

            # Validate parse result
            if not parse_result.get('chapters'):
                raise ValueError(
                    f"No readable chapters found in this {file_type.upper()}. "
                    "The file may be DRM-protected, corrupted, or use an unsupported format."
                )

            # Log any parsing warnings
            if parse_result.get('warnings'):
                for warning in parse_result['warnings']:
                    logger.warning(f"EPUB parsing warning for book {book.id}: {warning}")

            # Update book metadata
            fallback_title = book_file.name.replace('.epub', '').replace('.pdf', '')
            book.title = parse_result['metadata'].get('title', fallback_title)
            book.author = parse_result['metadata'].get('author', 'Unknown Author')
            book.isbn = parse_result['metadata'].get('isbn')
            book.language = parse_result['metadata'].get('language')

            # Extract and save cover image
            if parse_result.get('cover_image'):
                cover_data = parse_result['cover_image']
                cover_filename = f"cover_{book.id}.jpg"
                cover_path = os.path.join('books', 'covers', cover_filename)

                # Save cover image
                from django.core.files.base import ContentFile
                book.cover_image.save(cover_filename, ContentFile(cover_data), save=False)

                # Generate thumbnail (300x450 max, maintain aspect ratio)
                try:
                    img = Image.open(book.cover_image.path)
                    # Convert to RGB if necessary (handles PNG with transparency, CMYK, etc.)
                    if img.mode in ('RGBA', 'P', 'CMYK', 'LA'):
                        img = img.convert('RGB')
                    img.thumbnail((300, 450), Image.Resampling.LANCZOS)

                    thumb_filename = f"thumb_{book.id}.jpg"

                    # Create thumbs directory if it doesn't exist
                    thumb_dir = os.path.join(os.path.dirname(book.cover_image.path), 'thumbs')
                    os.makedirs(thumb_dir, exist_ok=True)

                    # Save thumbnail to disk
                    thumb_full_path = os.path.join(thumb_dir, thumb_filename)
                    img.save(thumb_full_path, 'JPEG', quality=85)

                    # Set the thumbnail field (relative path from MEDIA_ROOT)
                    book.cover_thumbnail = os.path.join('books', 'covers', 'thumbs', thumb_filename)

                except Exception as e:
                    logger.warning(f"Failed to generate thumbnail for book {book.id}: {e}")

            # Split content into chapters
            splitter = ContentSplitter()
            chapters = splitter.split_chapters(
                book=book,
                chapters_data=parse_result['chapters']
            )

            # Calculate total word count (exclude front/back matter)
            book.word_count = sum(
                c.word_count for c in chapters
                if not c.is_front_matter and not c.is_back_matter
            )

            # Mark as completed
            book.status = 'completed'
            book.processed_at = timezone.now()
            book.save()

            # Compute readability metrics (local, free, ~1-2 seconds)
            try:
                from .services.readability_service import ReadabilityService
                ReadabilityService().compute_all_for_book(book)
            except Exception as e:
                logger.warning(f"Readability computation failed for book {book.id}: {e}")
                # Non-fatal - book is still usable without readability

            # Store book ID and type for success URL
            self.book_id = book.id
            self.book_type = book.book_type

            messages.success(self.request, f'Successfully uploaded "{book.title}"')
            return super().form_valid(form)

        except Exception as e:
            # Mark as failed
            book.status = 'failed'
            book.error_message = str(e)
            book.processed_at = timezone.now()
            book.save()

            logger.exception(f"Upload failed for book {book.id}")

            # Show error message
            messages.error(
                self.request,
                f'Upload failed: {str(e)}'
            )

            # Redirect to appropriate library view on error
            if book.book_type == 'fiction':
                return redirect('fiction:library')
            return redirect('nonfiction:library')


class LibraryView(ListView):
    """
    Display all books in a grid layout with sorting options.
    Base class that can be filtered by book_type.
    """
    model = Book
    template_name = 'books_core/library.html'
    context_object_name = 'books'
    book_type = None  # Override in subclasses to filter by type

    def get_queryset(self):
        """Apply sorting and optional book_type filter."""
        queryset = Book.objects.all()

        # Filter by book_type if specified
        if self.book_type:
            queryset = queryset.filter(book_type=self.book_type)

        sort = self.request.GET.get('sort', 'newest')
        if sort == 'newest':
            queryset = queryset.order_by('-uploaded_at')
        elif sort == 'oldest':
            queryset = queryset.order_by('uploaded_at')
        elif sort == 'title_az':
            queryset = queryset.order_by('title')
        elif sort == 'title_za':
            queryset = queryset.order_by('-title')
        elif sort == 'author_az':
            queryset = queryset.order_by('author', 'title')

        return queryset

    def get_context_data(self, **kwargs):
        """Add book count, current sort, and section info to context."""
        context = super().get_context_data(**kwargs)
        context['book_count'] = self.get_queryset().count()
        context['current_sort'] = self.request.GET.get('sort', 'newest')
        context['book_type'] = self.book_type
        context['section'] = self.book_type or 'all'
        return context


class NonfictionLibraryView(LibraryView):
    """Display non-fiction books only."""
    book_type = 'nonfiction'
    template_name = 'books_core/library.html'


class FictionLibraryView(LibraryView):
    """Display fiction books only."""
    book_type = 'fiction'
    template_name = 'books_core/fiction_library.html'


class BookDetailView(DetailView):
    """
    Display book details, metadata, and chapter list.
    Base class that can be filtered by book_type.
    """
    model = Book
    template_name = 'books_core/book_detail.html'
    context_object_name = 'book'
    book_type = None  # Override in subclasses to filter by type

    def get_queryset(self):
        """Filter by book_type if specified."""
        queryset = super().get_queryset()
        if self.book_type:
            queryset = queryset.filter(book_type=self.book_type)
        return queryset

    def get_context_data(self, **kwargs):
        """Add chapters list and book rating to context."""
        context = super().get_context_data(**kwargs)

        # Get main content chapters (exclude front/back matter)
        context['chapters'] = self.object.chapters.filter(
            is_front_matter=False,
            is_back_matter=False
        ).order_by('chapter_number')

        context['chapter_count'] = context['chapters'].count()
        context['book_type'] = self.object.book_type
        context['section'] = self.object.book_type

        # Get book rating if analysis has been run (non-fiction only)
        if self.object.book_type == 'nonfiction':
            from books_core.services.chapter_analysis_pipeline_service import ChapterAnalysisPipelineService
            service = ChapterAnalysisPipelineService()
            context['book_rating'] = service.get_book_rating(self.object)

        # Get readability metrics (available for all completed books)
        book_readability = self.object.readability_metrics
        if book_readability and not book_readability.get('error'):
            context['book_readability'] = book_readability
            context['has_readability'] = True

            from .services.readability_charts import ReadabilityChartService
            chart_service = ReadabilityChartService()
            readability_charts = {}
            if book_readability.get('chapter_curve_data'):
                readability_charts['difficulty_curve'] = chart_service.generate_difficulty_curve_svg(
                    book_readability['chapter_curve_data']
                )
            if book_readability.get('difficulty_profile'):
                readability_charts['distribution'] = chart_service.generate_distribution_bars_svg(
                    book_readability['difficulty_profile']
                )
            context['readability_charts'] = readability_charts
        else:
            context['has_readability'] = False
            context['readability_charts'] = {}

        return context


class NonfictionBookDetailView(BookDetailView):
    """Display non-fiction book details."""
    book_type = 'nonfiction'


class FictionBookDetailView(BookDetailView):
    """Display fiction book details."""
    book_type = 'fiction'
    template_name = 'books_core/fiction_book_detail.html'


class ReadingView(TemplateView):
    """
    Display chapter content in a reading interface with navigation.
    Base class that can be filtered by book_type.
    """
    template_name = 'books_core/reading_view.html'
    book_type = None  # Override in subclasses to filter by type

    def get_context_data(self, **kwargs):
        """Prepare reading view context with chapter content and navigation."""
        context = super().get_context_data(**kwargs)

        book_id = self.kwargs['book_id']
        chapter_number = self.kwargs.get('chapter_number', 1)

        # Build queryset with optional book_type filter
        queryset = Book.objects.all()
        if self.book_type:
            queryset = queryset.filter(book_type=self.book_type)

        book = get_object_or_404(queryset, pk=book_id)

        # Get main content chapters
        chapters = book.chapters.filter(
            is_front_matter=False,
            is_back_matter=False
        ).order_by('chapter_number')

        # Get current chapter
        current_chapter = get_object_or_404(
            chapters,
            chapter_number=chapter_number
        )

        context['book'] = book
        context['chapter'] = current_chapter
        context['chapters'] = chapters
        context['chapter_count'] = chapters.count()
        context['book_type'] = book.book_type
        context['section'] = book.book_type

        # Previous/next navigation
        context['prev_chapter'] = chapters.filter(
            chapter_number__lt=chapter_number
        ).order_by('-chapter_number').first()

        context['next_chapter'] = chapters.filter(
            chapter_number__gt=chapter_number
        ).order_by('chapter_number').first()

        # Get summaries for current chapter
        from .models import Summary
        summaries = Summary.objects.filter(
            chapter=current_chapter
        ).select_related('prompt').order_by('-created_at')

        context['summaries'] = summaries
        context['has_summaries'] = summaries.exists()

        # Get the most recent summary to display
        if summaries.exists():
            context['latest_summary'] = summaries.first()

        return context


class NonfictionReadingView(ReadingView):
    """Reading view for non-fiction books."""
    book_type = 'nonfiction'


class FictionReadingView(ReadingView):
    """Reading view for fiction books."""
    book_type = 'fiction'
    template_name = 'books_core/fiction_reading_view.html'


class DeleteBookView(DeleteView):
    """
    Delete a book and all associated chapters.
    """
    model = Book

    def get_success_url(self):
        namespace = self.request.resolver_match.namespace
        if namespace:
            return reverse_lazy(f'{namespace}:library')
        return reverse_lazy('nonfiction:library')

    def post(self, request, *args, **kwargs):
        """Handle POST request to delete book and show success message."""
        book = self.get_object()
        book_title = book.title
        response = super().post(request, *args, **kwargs)
        messages.success(request, f'Book "{book_title}" deleted successfully')
        return response


class SettingsManagementView(FormView):
    """
    View for managing application settings including cost limits and AI configuration.
    """
    template_name = 'books_core/settings.html'
    form_class = SettingsForm
    success_url = reverse_lazy('settings')

    def get_form_kwargs(self):
        """Populate form with existing settings."""
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = Settings.get_settings()
        return kwargs

    def form_valid(self, form):
        """Save settings and show success message."""
        form.save()
        messages.success(self.request, 'Settings updated successfully')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        """Add usage statistics to context."""
        context = super().get_context_data(**kwargs)
        settings = Settings.get_settings()

        # Get current usage stats
        today = date.today()
        month_year = today.strftime('%Y-%m')

        try:
            usage = UsageTracking.objects.get(date=today)
            daily_count = usage.daily_summaries_count
            daily_cost = usage.daily_cost_usd
            monthly_count = usage.monthly_summaries_count
            monthly_cost = usage.monthly_cost_usd
        except UsageTracking.DoesNotExist:
            daily_count = 0
            daily_cost = 0
            monthly_count = 0
            monthly_cost = 0

        # Calculate percentages
        daily_percentage = (daily_count / settings.daily_summary_limit * 100) if settings.daily_summary_limit > 0 else 0
        monthly_percentage = (float(monthly_cost) / float(settings.monthly_limit_usd) * 100) if settings.monthly_limit_usd > 0 else 0

        context['settings'] = settings
        context['usage'] = {
            'daily_count': daily_count,
            'daily_limit': settings.daily_summary_limit,
            'daily_remaining': max(0, settings.daily_summary_limit - daily_count),
            'daily_percentage': round(daily_percentage, 1),
            'monthly_cost': monthly_cost,
            'monthly_limit': settings.monthly_limit_usd,
            'monthly_remaining': max(0, float(settings.monthly_limit_usd) - float(monthly_cost)),
            'monthly_percentage': round(monthly_percentage, 1),
        }

        return context
