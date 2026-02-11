"""
Fiction-specific views for VoxLibri.

Contains views for fiction book features that don't apply to non-fiction:
- Resume Reading: Generate "story so far" summaries to help readers continue
"""

import json
import logging
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView

from .models import Book, Summary, Prompt
from .services.resume_reading_service import ResumeReadingService

logger = logging.getLogger(__name__)


class ResumeReadingView(TemplateView):
    """
    Display the resume reading interface where users can select which
    chapter they want to resume at and generate a "story so far" summary.

    GET /fiction/books/<book_id>/resume/
    """
    template_name = 'books_core/resume_reading.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        book_id = self.kwargs['book_id']

        book = get_object_or_404(Book, pk=book_id, book_type='fiction')

        # Get all main content chapters
        chapters = book.chapters.filter(
            is_front_matter=False,
            is_back_matter=False
        ).order_by('chapter_number')

        # Find which chapters have cached resume summaries
        cached_chapters = []
        try:
            prompt = Prompt.objects.get(name='summarize_to_chapter')
            cached_summaries = Summary.objects.filter(
                book=book,
                chapter__isnull=True,
                prompt=prompt,
            )

            for summary in cached_summaries:
                target = summary.content_json.get('target_chapter')
                if target:
                    cached_chapters.append(target)
        except Prompt.DoesNotExist:
            pass

        context['book'] = book
        context['chapters'] = chapters
        context['chapter_count'] = chapters.count()
        context['cached_chapters'] = cached_chapters
        context['section'] = 'fiction'

        return context


class ResumeReadingCostPreview(View):
    """
    API endpoint to estimate cost of generating a resume summary.

    GET /fiction/books/<book_id>/resume/preview/?chapter=5
    """

    def get(self, request, book_id):
        book = get_object_or_404(Book, pk=book_id, book_type='fiction')

        try:
            target_chapter = int(request.GET.get('chapter', 1))
        except (ValueError, TypeError):
            return JsonResponse({
                'error': 'Invalid chapter number'
            }, status=400)

        if target_chapter < 2:
            return JsonResponse({
                'error': 'Cannot generate resume for chapter 1 - nothing to summarize yet'
            }, status=400)

        service = ResumeReadingService()
        estimate = service.estimate_cost(book, target_chapter)

        return JsonResponse(estimate)


class GenerateResumeReading(View):
    """
    API endpoint to generate or retrieve a cached resume summary.

    POST /fiction/books/<book_id>/resume/generate/
    Body: {
        "target_chapter": 5,
        "force_regenerate": false  // optional
    }
    """

    def post(self, request, book_id):
        book = get_object_or_404(Book, pk=book_id, book_type='fiction')

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({
                'error': 'Invalid JSON body'
            }, status=400)

        target_chapter = data.get('target_chapter')
        force_regenerate = data.get('force_regenerate', False)

        if not target_chapter:
            return JsonResponse({
                'error': 'target_chapter is required'
            }, status=400)

        try:
            target_chapter = int(target_chapter)
        except (ValueError, TypeError):
            return JsonResponse({
                'error': 'Invalid chapter number'
            }, status=400)

        if target_chapter < 2:
            return JsonResponse({
                'error': 'Cannot generate resume for chapter 1 - nothing to summarize yet'
            }, status=400)

        service = ResumeReadingService()

        try:
            summary = service.get_or_generate(
                book=book,
                target_chapter=target_chapter,
                force_regenerate=force_regenerate,
            )

            return JsonResponse({
                'success': True,
                'summary_id': summary.id,
                'target_chapter': target_chapter,
                'content': summary.content_json.get('text', ''),
                'is_cached': not force_regenerate and (
                    summary.content_json.get('target_chapter') == target_chapter
                ),
                'cost_usd': str(summary.estimated_cost_usd),
                'tokens_used': summary.tokens_used,
                'chapters_summarized': summary.content_json.get('chapters_summarized', 0),
                'created_at': summary.created_at.isoformat(),
            })

        except ValueError as e:
            return JsonResponse({
                'error': str(e)
            }, status=400)

        except Exception as e:
            logger.exception(f"Failed to generate resume summary for book {book_id}")
            return JsonResponse({
                'error': f'Failed to generate summary: {str(e)}'
            }, status=500)


class ResumeReadingSummaryView(View):
    """
    Get details of an existing resume summary.

    GET /fiction/books/<book_id>/resume/<summary_id>/
    """

    def get(self, request, book_id, summary_id):
        book = get_object_or_404(Book, pk=book_id, book_type='fiction')
        summary = get_object_or_404(
            Summary,
            pk=summary_id,
            book=book,
            chapter__isnull=True
        )

        return JsonResponse({
            'summary_id': summary.id,
            'target_chapter': summary.content_json.get('target_chapter'),
            'content': summary.content_json.get('text', ''),
            'chapters_summarized': summary.content_json.get('chapters_summarized', 0),
            'cost_usd': str(summary.estimated_cost_usd),
            'tokens_used': summary.tokens_used,
            'created_at': summary.created_at.isoformat(),
        })
