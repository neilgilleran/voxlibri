"""
Views for book-level summary and analysis feature.

Provides:
- Book summary page rendering
- Cost preview API for book analysis
- Book analysis generation API
"""

import json
import logging
import threading
from decimal import Decimal

from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.shortcuts import get_object_or_404, render
from django.db import connection
from django.utils import timezone

from .models import Book, Prompt, Summary, Settings, ProcessingJob
from .services.book_analysis_service import BookAnalysisService
from .services.cost_control_service import CostControlService
from .exceptions import LimitExceededException, EmergencyStopException

logger = logging.getLogger(__name__)


class APIView(View):
    """Base API view with common JSON handling."""

    def json_response(self, data, status=200):
        """Return JSON response with proper content type."""
        return JsonResponse(data, status=status, safe=False)

    def error_response(self, message, status=400):
        """Return error response."""
        return self.json_response({'error': message}, status=status)

    def parse_json_body(self, request):
        """Parse JSON body from request."""
        try:
            return json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return None


class BookSummaryView(TemplateView):
    """
    GET /books/<book_id>/summary/

    Display book-level analysis page.
    Shows either empty state or results from 9 Fabric prompts.
    """
    template_name = 'books_core/book_summary.html'

    def get(self, request, book_id):
        """Render book summary page."""
        # Get book or 404
        book = get_object_or_404(Book, pk=book_id)

        # Get settings for model dropdown
        settings = Settings.get_settings()

        # Initialize service
        service = BookAnalysisService()

        # Get ALL summaries grouped by prompt (all versions)
        from .models import Summary
        from collections import defaultdict

        all_summaries = Summary.objects.filter(book=book).select_related('prompt').order_by('prompt__name', '-version')

        # Group by prompt name with all versions
        summaries_by_prompt = defaultdict(list)
        for summary in all_summaries:
            prompt_name = summary.prompt.name
            summaries_by_prompt[prompt_name].append(summary)

        # Check if we have any summaries
        has_summaries = len(summaries_by_prompt) > 0

        # Get model used (from first summary if exists)
        model_used = None
        generation_timestamp = None
        if has_summaries:
            first_prompt_versions = list(summaries_by_prompt.values())[0]
            if first_prompt_versions:
                model_used = first_prompt_versions[0].model_used
                generation_timestamp = first_prompt_versions[0].created_at

        # Organize summaries by prompt name
        # Show ALL summaries that exist, not just the hardcoded list
        ordered_summaries = []

        # First add summaries in the preferred order (if they exist)
        for prompt_name in service.PROMPT_NAMES:
            if prompt_name in summaries_by_prompt:
                versions = summaries_by_prompt[prompt_name]
                # Add content field to each version for easy template access
                versions_with_content = []
                for v in versions:
                    versions_with_content.append({
                        'id': v.id,
                        'version': v.version,
                        'content': v.content_json.get('content', ''),
                        'tokens_used': v.tokens_used,
                        'estimated_cost_usd': v.estimated_cost_usd,
                        'created_at': v.created_at,
                        'model_used': v.model_used,
                    })

                ordered_summaries.append({
                    'prompt_name': prompt_name,
                    'prompt_display': prompt_name.replace('_', ' ').title(),
                    'versions': versions_with_content,
                    'latest_version': versions[0],
                    'content': versions[0].content_json.get('content', '')
                })

        # Then add any additional summaries not in the default list
        for prompt_name, versions in summaries_by_prompt.items():
            if prompt_name not in service.PROMPT_NAMES:
                # Add content field to each version for easy template access
                versions_with_content = []
                for v in versions:
                    versions_with_content.append({
                        'id': v.id,
                        'version': v.version,
                        'content': v.content_json.get('content', ''),
                        'tokens_used': v.tokens_used,
                        'estimated_cost_usd': v.estimated_cost_usd,
                        'created_at': v.created_at,
                        'model_used': v.model_used,
                    })

                ordered_summaries.append({
                    'prompt_name': prompt_name,
                    'prompt_display': prompt_name.replace('_', ' ').title(),
                    'versions': versions_with_content,
                    'latest_version': versions[0],
                    'content': versions[0].content_json.get('content', '')
                })

        # Get all available prompts for checkboxes
        from .models import Prompt
        available_prompts = Prompt.objects.all().order_by('category', 'name')

        context = {
            'book': book,
            'has_summaries': has_summaries,
            'summaries': ordered_summaries,
            'model_used': model_used,
            'generation_timestamp': generation_timestamp,
            'default_model': settings.default_model,
            'ai_enabled': settings.ai_features_enabled,
            'total_prompts': len(service.PROMPT_NAMES),
            'available_prompts': available_prompts,
        }

        return render(request, self.template_name, context)


class BookAnalysisCostPreviewView(APIView):
    """
    GET /books/<book_id>/summary/cost-preview/?model=<model_name>

    Return cost estimate for book analysis generation.
    """

    def get(self, request, book_id):
        """Generate cost preview for book analysis."""
        # Get book or 404
        try:
            book = Book.objects.get(pk=book_id)
        except Book.DoesNotExist:
            return self.error_response('Book not found', 404)

        # Get model from query params
        model = request.GET.get('model', 'gpt-4o-mini')

        # Get selected prompt IDs from query params (can be multiple)
        prompt_ids = request.GET.getlist('prompt_ids')
        prompt_ids = [int(pid) for pid in prompt_ids if pid.isdigit()] if prompt_ids else None

        # Check if AI features are enabled
        settings = Settings.get_settings()
        if not settings.ai_features_enabled:
            return self.error_response(
                'AI features are currently disabled. Visit Settings to enable.',
                403
            )

        try:
            # Initialize service
            service = BookAnalysisService()
            cost_control = CostControlService(model=model)

            # If prompt_ids provided, validate and get prompt names
            prompt_names_for_estimate = None
            if prompt_ids:
                from .models import Prompt
                valid_prompts = Prompt.objects.filter(id__in=prompt_ids)
                if len(valid_prompts) != len(prompt_ids):
                    return self.error_response('One or more invalid prompt IDs provided', 400)
                prompt_names_for_estimate = [p.name for p in valid_prompts]

            # Get cost estimate (pass prompt names if provided)
            cost_estimate = service.estimate_cost(book, model, prompt_names=prompt_names_for_estimate)

            # Check model TPM limits BEFORE allowing generation
            from books_core.services.openai_service import OpenAIService
            model_tpm_limit = OpenAIService.MODEL_TPM_LIMITS.get(model, None)

            # Each prompt will send: input_tokens + output_tokens
            tokens_per_prompt = cost_estimate['input_tokens'] + cost_estimate['output_tokens']

            if model_tpm_limit and tokens_per_prompt > model_tpm_limit:
                return self.json_response({
                    'error': (
                        f"Request exceeds {model} token limit. "
                        f"Limit: {model_tpm_limit:,} TPM, "
                        f"Requested: {tokens_per_prompt:,} tokens per prompt "
                        f"({cost_estimate['input_tokens']:,} input + {cost_estimate['output_tokens']:,} output). "
                        f"Please use a different model (e.g., gpt-4o-mini has a 200K limit) or reduce content size."
                    ),
                    'limit_exceeded': True,
                    'model_limit': model_tpm_limit,
                    'requested_tokens': tokens_per_prompt
                })

            # Get current usage stats
            usage_info = cost_control.get_current_usage()

            # Build response
            response_data = {
                'book': {
                    'id': book.id,
                    'title': book.title,
                    'author': book.author
                },
                'estimate': {
                    'input_tokens': cost_estimate['input_tokens'],
                    'output_tokens': cost_estimate['output_tokens'],
                    'total_tokens': cost_estimate['total_tokens'],
                    'estimated_cost_usd': str(cost_estimate['estimated_cost_usd']),
                    'per_prompt_cost': str(cost_estimate['per_prompt_cost']),
                    'num_prompts': cost_estimate['num_prompts'],
                    'model': model
                },
                'usage': {
                    'daily': usage_info['daily'],
                    'monthly': usage_info['monthly']
                },
                'warnings': []
            }

            # Check if generation would exceed cost limits (without raising exception)
            try:
                limit_check = cost_control.check_limits(cost_estimate['estimated_cost_usd'])
                response_data['warnings'] = limit_check.get('warnings', [])
            except LimitExceededException as e:
                response_data['error'] = str(e)
                response_data['limit_exceeded'] = True

            return self.json_response(response_data)

        except Exception as e:
            logger.error(f"Error generating cost preview for book {book_id}: {str(e)}", exc_info=True)
            return self.error_response(f'Failed to generate cost preview: {str(e)}', 500)


class GenerateBookAnalysisView(APIView):
    """
    POST /books/<book_id>/summary/generate/

    Generate book analysis by running 9 prompts.
    Returns job_id for progress tracking.
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request, book_id):
        """Generate book analysis."""
        # Parse request body
        data = self.parse_json_body(request)
        if data is None:
            return self.error_response('Invalid JSON in request body', 400)

        # Get model from request
        model = data.get('model', 'gpt-4o-mini')

        # Get selected prompt IDs from request (optional)
        prompt_ids = data.get('prompt_ids', None)

        # Get book or 404
        try:
            book = Book.objects.get(pk=book_id)
        except Book.DoesNotExist:
            return self.error_response('Book not found', 404)

        # Check if AI features are enabled
        settings = Settings.get_settings()
        if not settings.ai_features_enabled:
            return self.error_response(
                'AI features are currently disabled. Visit Settings to enable.',
                403
            )

        try:
            # Check if there's already a running or pending job for this book
            existing_job = ProcessingJob.objects.filter(
                book=book,
                job_type='book_analysis',
                status__in=['pending', 'running']
            ).first()

            if existing_job:
                return self.error_response(
                    f'Analysis already in progress for this book (Job #{existing_job.id}). Please wait for it to complete.',
                    409  # Conflict
                )

            # Initialize service
            service = BookAnalysisService()

            # If prompt_ids provided, validate and use them
            if prompt_ids:
                from .models import Prompt
                # Validate that all prompt IDs exist
                valid_prompts = Prompt.objects.filter(id__in=prompt_ids)
                if len(valid_prompts) != len(prompt_ids):
                    return self.error_response('One or more invalid prompt IDs provided', 400)

                prompt_names_to_run = [p.name for p in valid_prompts]
            else:
                # Use all default prompts
                prompt_names_to_run = service.PROMPT_NAMES

            # Check model TPM limits BEFORE creating job
            from books_core.services.openai_service import OpenAIService
            model_tpm_limit = OpenAIService.MODEL_TPM_LIMITS.get(model, None)

            if model_tpm_limit:
                # Quick estimate: check if request will exceed model limits
                cost_estimate = service.estimate_cost(book, model, prompt_names=prompt_names_to_run)
                tokens_per_prompt = cost_estimate['input_tokens'] + cost_estimate['output_tokens']

                if tokens_per_prompt > model_tpm_limit:
                    return self.error_response(
                        f"Request exceeds {model} token limit. "
                        f"Limit: {model_tpm_limit:,} TPM, "
                        f"Requested: {tokens_per_prompt:,} tokens per prompt. "
                        f"Please use gpt-4o-mini (200K limit) or reduce content size.",
                        400
                    )

            # Create the processing job FIRST (before starting thread)
            job = ProcessingJob.objects.create(
                book=book,
                job_type='book_analysis',
                status='pending',
                metadata={
                    'model': model,
                    'num_prompts': len(prompt_names_to_run),
                    'prompt_names': prompt_names_to_run,
                    'prompt_ids': prompt_ids if prompt_ids else []
                }
            )

            logger.info(f"Created ProcessingJob {job.id} for book {book_id} analysis")

            # Run analysis in background thread
            def run_analysis():
                try:
                    # Close the old database connection
                    # Django will create a new one in this thread
                    connection.close()
                    # Pass the existing job_id and selected prompt names
                    service.generate_book_analysis_with_job(book, model, job.id, prompt_names=prompt_names_to_run)
                except Exception as e:
                    logger.error(f"Background analysis failed for book {book_id}: {str(e)}", exc_info=True)
                    # Mark job as failed
                    try:
                        connection.close()  # Fresh connection
                        failed_job = ProcessingJob.objects.get(id=job.id)
                        failed_job.status = 'failed'
                        failed_job.error_message = str(e)
                        failed_job.completed_at = timezone.now()
                        failed_job.save()
                    except Exception as save_error:
                        logger.error(f"Failed to mark job {job.id} as failed: {str(save_error)}")
                finally:
                    # Clean up connection in thread
                    connection.close()

            # Close connection before starting thread
            connection.close()

            thread = threading.Thread(target=run_analysis)
            thread.daemon = True
            thread.start()

            return self.json_response({
                'success': True,
                'job_id': job.id,
                'message': 'Book analysis started',
                'num_prompts': len(service.PROMPT_NAMES)
            })

        except LimitExceededException as e:
            logger.warning(f"Limit exceeded for book {book_id}: {str(e)}")
            return self.error_response(str(e), 429)

        except ValueError as e:
            logger.warning(f"Invalid request for book {book_id}: {str(e)}")
            return self.error_response(str(e), 400)

        except Exception as e:
            logger.error(f"Error generating book analysis for book {book_id}: {str(e)}", exc_info=True)
            return self.error_response(f'Failed to generate book analysis: {str(e)}', 500)
