"""
Views for chapter-by-chapter analysis pipeline.

Provides:
- Cost preview API for pipeline
- Pipeline trigger API
- Progress polling API
- Full report page
"""

import json
import logging

import re

from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.generic import TemplateView
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .models import Book, ProcessingJob
from .services.chapter_analysis_pipeline_service import ChapterAnalysisPipelineService
from .services.readability_charts import ReadabilityChartService
from .services.report_epub_service import ReportEpubService
from .exceptions import LimitExceededException, EmergencyStopException

logger = logging.getLogger(__name__)


class APIView(View):
    """Base API view with common JSON handling."""

    def json_response(self, data, status=200):
        return JsonResponse(data, status=status, safe=False)

    def error_response(self, message, status=400):
        return self.json_response({'error': message}, status=status)

    def parse_json_body(self, request):
        try:
            return json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return None


class PipelineCostPreviewView(APIView):
    """
    GET /books/<book_id>/analyze/cost-preview/

    Returns estimated cost for running the full analysis pipeline.
    """

    def get(self, request, book_id):
        book = get_object_or_404(Book, pk=book_id)
        model = request.GET.get('model', 'gpt-4o-mini')

        service = ChapterAnalysisPipelineService(model=model)

        try:
            estimate = service.estimate_pipeline_cost(book, model)
            return self.json_response(estimate)
        except Exception as e:
            logger.error(f"Cost preview failed for book {book_id}: {e}")
            return self.error_response(str(e), status=500)


@method_decorator(csrf_exempt, name='dispatch')
class TriggerPipelineView(APIView):
    """
    POST /books/<book_id>/analyze/

    Start the analysis pipeline for a book.
    Returns job_id for progress tracking.
    """

    def post(self, request, book_id):
        book = get_object_or_404(Book, pk=book_id)

        data = self.parse_json_body(request)
        if data is None:
            return self.error_response('Invalid JSON body')

        model = data.get('model', 'gpt-4o-mini')
        confirmed = data.get('confirmed', False)

        if not confirmed:
            return self.error_response('Must confirm before starting analysis', status=400)

        service = ChapterAnalysisPipelineService(model=model)

        try:
            job = service.run_pipeline(book, model)
            return self.json_response({
                'job_id': job.id,
                'status': job.status,
                'message': 'Pipeline started'
            })
        except ValueError as e:
            # Already running
            return self.error_response(str(e), status=409)
        except LimitExceededException as e:
            return self.error_response(str(e), status=429)
        except EmergencyStopException:
            return self.error_response('AI features are currently disabled', status=503)
        except Exception as e:
            logger.error(f"Pipeline trigger failed for book {book_id}: {e}")
            return self.error_response(str(e), status=500)


class PipelineProgressView(APIView):
    """
    GET /books/<book_id>/analyze/progress/<job_id>/

    Get current progress of a running pipeline.
    """

    def get(self, request, book_id, job_id):
        job = get_object_or_404(ProcessingJob, pk=job_id, book_id=book_id)

        return self.json_response({
            'job_id': job.id,
            'status': job.status,
            'progress_percent': job.progress_percent,
            'error_message': job.error_message,
            'metadata': job.metadata,
        })


class BookReportView(TemplateView):
    """
    GET /books/<book_id>/report/

    Display full analysis report with chapter-by-chapter breakdown.
    """
    template_name = 'books_core/book_report.html'

    def get(self, request, book_id):
        book = get_object_or_404(Book, pk=book_id)

        service = ChapterAnalysisPipelineService()

        # Get book-level rating
        book_rating = service.get_book_rating(book)

        # Get book essence (aggregated extractions)
        book_essence = service.get_book_essence(book)

        # Get all chapter analyses
        chapter_analyses = service.get_chapter_analyses(book)

        context = {
            'book': book,
            'book_rating': book_rating,
            'book_essence': book_essence,
            'chapter_analyses': chapter_analyses,
            'has_analysis': book_rating is not None,
            'has_essence': book_essence is not None,
        }

        return self.render_to_response(context)


class ExportReportEpubView(View):
    """
    GET /books/<book_id>/report/export/

    Generate and download EPUB file containing the book analysis report.
    """

    def get(self, request, book_id):
        book = get_object_or_404(Book, pk=book_id)

        service = ChapterAnalysisPipelineService()

        # Get report data (same as BookReportView)
        book_rating = service.get_book_rating(book)
        book_essence = service.get_book_essence(book)
        chapter_analyses = service.get_chapter_analyses(book)

        # Get readability metrics
        book_readability = service.get_book_readability(book)

        # Generate readability charts for EPUB
        readability_charts = {}
        if book_readability:
            chart_service = ReadabilityChartService()
            if book_readability.get('chapter_curve_data'):
                readability_charts['difficulty_curve'] = chart_service.generate_difficulty_curve_svg(
                    book_readability['chapter_curve_data']
                )
            if book_readability.get('difficulty_profile'):
                readability_charts['distribution'] = chart_service.generate_distribution_bars_svg(
                    book_readability['difficulty_profile']
                )

        # Check if analysis exists
        if not book_rating:
            return HttpResponse(
                "This book has not been analyzed yet.",
                status=404,
                content_type="text/plain"
            )

        # Generate EPUB
        try:
            epub_service = ReportEpubService(book)
            epub_bytes = epub_service.generate_report_epub(
                book_rating=book_rating,
                book_essence=book_essence,
                chapter_analyses=chapter_analyses,
                book_readability=book_readability,
                readability_charts=readability_charts
            )
        except Exception as e:
            logger.error(f"Failed to generate EPUB for book {book_id}: {e}", exc_info=True)
            return HttpResponse(
                f"Failed to generate EPUB: {str(e)}",
                status=500,
                content_type="text/plain"
            )

        # Create response with attachment
        filename = self._sanitize_filename(book.title) + "_analysis.epub"

        response = HttpResponse(
            epub_bytes,
            content_type="application/epub+zip"
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Content-Length'] = len(epub_bytes)

        return response

    def _sanitize_filename(self, title: str) -> str:
        """Remove unsafe characters from filename."""
        safe_title = re.sub(r'[^\w\s-]', '', title)
        safe_title = re.sub(r'[-\s]+', '_', safe_title)
        return safe_title[:100]  # Limit length
