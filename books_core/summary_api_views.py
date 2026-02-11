"""
REST API views for AI-powered summary generation.
Implements cost preview, generation, batch processing, and prompt management.
"""
import json
import logging
import threading
from decimal import Decimal
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone

from .models import Chapter, Prompt, Summary, Settings, ProcessingJob, Book
from .services.cost_control_service import CostControlService
from .services.openai_service import OpenAIService
from .services.summary_service import SummaryService
from .services.fabric_prompt_service import FabricPromptService
from .services.batch_processing_service import BatchProcessingService
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


# Task Group 3.1: Summary Generation API


class SummaryPreviewView(APIView):
    """
    POST /api/chapters/<id>/summary-preview/

    Preview cost for generating a summary before confirmation.
    Returns estimated tokens, cost, and current usage stats.
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request, chapter_id):
        """Generate cost preview for summary generation."""
        # Parse request body
        data = self.parse_json_body(request)
        if data is None:
            return self.error_response('Invalid JSON in request body', 400)

        # Validate required fields
        prompt_id = data.get('prompt_id')
        model = data.get('model', 'gpt-4o-mini')

        if not prompt_id:
            return self.error_response('prompt_id is required', 400)

        # Check if chapter exists
        try:
            chapter = Chapter.objects.select_related('book').get(pk=chapter_id)
        except Chapter.DoesNotExist:
            return self.error_response('Chapter not found', 404)

        # Check if prompt exists
        try:
            prompt = Prompt.objects.get(pk=prompt_id)
        except Prompt.DoesNotExist:
            return self.error_response('Prompt not found', 404)

        # Check if AI features are enabled
        settings = Settings.get_settings()
        if not settings.ai_features_enabled:
            return self.error_response(
                'AI features are currently disabled. Visit Settings to enable.',
                403
            )

        try:
            # Initialize services
            cost_service = CostControlService()

            # Render prompt with chapter variables
            rendered_prompt = prompt.render_template({
                'content': chapter.content,
                'title': chapter.title or f'Chapter {chapter.chapter_number}',
                'author': chapter.book.author,
            })

            # Count tokens
            input_tokens = cost_service.count_tokens(rendered_prompt, model)
            # Estimate output tokens (typical summary is ~20% of input)
            estimated_output_tokens = max(500, int(input_tokens * 0.2))

            # Estimate cost
            cost_estimate = cost_service.estimate_cost(
                input_tokens=input_tokens,
                output_tokens=estimated_output_tokens,
                model=model
            )

            # Check limits and get usage stats
            usage_check = cost_service.check_limits(cost_estimate['estimated_cost_usd'])

            # Build response
            response_data = {
                'estimated_tokens': cost_estimate['total_tokens'],
                'input_tokens': input_tokens,
                'output_tokens': estimated_output_tokens,
                'estimated_cost_usd': str(cost_estimate['estimated_cost_usd']),
                'model': model,
                'chapter_id': chapter_id,
                'chapter_title': chapter.title or f'Chapter {chapter.chapter_number}',
                'prompt_name': prompt.name,
                'daily_usage': usage_check['daily_usage'],
                'monthly_usage': usage_check['monthly_usage'],
                'warnings': usage_check['warnings'],
            }

            return self.json_response(response_data, 200)

        except LimitExceededException as e:
            return self.error_response(str(e), 403)
        except Exception as e:
            logger.error(f'Error in summary preview: {str(e)}')
            return self.error_response(f'Internal error: {str(e)}', 500)


class SummaryGenerateView(APIView):
    """
    POST /api/chapters/<id>/summary-generate/

    Generate AI summary for a chapter (requires confirmed=true).
    Two-step confirmation flow: preview first, then generate.
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request, chapter_id):
        """Generate summary for chapter after confirmation."""
        # Parse request body
        data = self.parse_json_body(request)
        if data is None:
            return self.error_response('Invalid JSON in request body', 400)

        # Validate required fields
        prompt_id = data.get('prompt_id')
        model = data.get('model', 'gpt-4o-mini')
        confirmed = data.get('confirmed', False)

        if not prompt_id:
            return self.error_response('prompt_id is required', 400)

        if not confirmed:
            return self.error_response(
                'Two-step confirmation required: confirmed must be true',
                400
            )

        # Check if chapter exists
        try:
            chapter = Chapter.objects.select_related('book').get(pk=chapter_id)
        except Chapter.DoesNotExist:
            return self.error_response('Chapter not found', 404)

        # Check if prompt exists
        try:
            prompt = Prompt.objects.get(pk=prompt_id)
        except Prompt.DoesNotExist:
            return self.error_response('Prompt not found', 404)

        # Check if AI features are enabled
        settings = Settings.get_settings()
        if not settings.ai_features_enabled:
            return self.error_response(
                'AI features are currently disabled. Visit Settings to enable.',
                403
            )

        try:
            # Initialize services
            cost_service = CostControlService()
            openai_service = OpenAIService()
            summary_service = SummaryService()

            # Render prompt with chapter variables
            rendered_prompt = prompt.render_template({
                'content': chapter.content,
                'title': chapter.title or f'Chapter {chapter.chapter_number}',
                'author': chapter.book.author,
            })

            # Generate summary with cost control (atomic transaction)
            with transaction.atomic():
                start_time = timezone.now()

                # Call OpenAI with cost control
                result = openai_service.complete_with_cost_control(
                    prompt=rendered_prompt,
                    model=model,
                    cost_service=cost_service
                )

                end_time = timezone.now()
                processing_time_ms = int((end_time - start_time).total_seconds() * 1000)

                # Calculate next version and create summary
                version, previous = summary_service.get_next_version(chapter, prompt)

                summary = summary_service.create_summary(
                    chapter=chapter,
                    prompt=prompt,
                    content={'text': result['content']},
                    metadata={
                        'version': version,
                        'previous_version_id': previous.id if previous else None,
                        'tokens_used': result['tokens_used'],
                        'model_used': model,
                        'processing_time_ms': processing_time_ms,
                        'estimated_cost_usd': result.get('cost_usd', Decimal('0')),
                    }
                )

                # Update chapter.has_summary flag
                chapter.has_summary = True
                chapter.save(update_fields=['has_summary'])

            # Build response
            response_data = {
                'summary_id': summary.id,
                'chapter_id': chapter.id,
                'version': summary.version,
                'prompt_name': prompt.name,
                'cost_usd': str(summary.estimated_cost_usd),
                'content': result['content'],
                'content_markdown': result['content'],  # Same as content for now
                'tokens_used': summary.tokens_used,
                'model_used': model,
                'processing_time_ms': processing_time_ms,
                'created_at': summary.created_at.isoformat(),
            }

            return self.json_response(response_data, 201)

        except LimitExceededException as e:
            return self.error_response(str(e), 403)
        except EmergencyStopException as e:
            return self.error_response(str(e), 403)
        except Exception as e:
            logger.error(f'Error generating summary: {str(e)}')
            return self.error_response(f'Summary generation failed: {str(e)}', 500)


class ChapterSummariesView(APIView):
    """
    GET /api/chapters/<id>/summaries/?prompt_id=<id>

    Get all versions of summaries for a chapter and prompt.
    Returns summaries ordered by version DESC (newest first).
    """

    def get(self, request, chapter_id):
        """Get summary versions for chapter and prompt."""
        # Get prompt_id from query params (optional)
        prompt_id = request.GET.get('prompt_id')

        # Check if chapter exists
        try:
            chapter = Chapter.objects.get(pk=chapter_id)
        except Chapter.DoesNotExist:
            return self.error_response('Chapter not found', 404)

        # If prompt_id provided, filter by prompt
        if prompt_id:
            try:
                prompt = Prompt.objects.get(pk=prompt_id)
            except Prompt.DoesNotExist:
                return self.error_response('Prompt not found', 404)

            # Get all versions for this chapter and prompt
            summary_service = SummaryService()
            versions = summary_service.get_versions(chapter, prompt)
        else:
            # Get ALL summaries for this chapter (all prompts)
            versions = Summary.objects.filter(
                chapter=chapter
            ).select_related('prompt').order_by('-created_at')

        # Build response
        summaries_data = [
            {
                'id': summary.id,
                'version': summary.version,
                'prompt_name': summary.prompt.name if summary.prompt else 'Unknown',
                'prompt_id': summary.prompt.id if summary.prompt else None,
                'created_at': summary.created_at.isoformat(),
                'cost_usd': str(summary.estimated_cost_usd),
                'tokens_used': summary.tokens_used,
                'model_used': summary.model_used,
            }
            for summary in versions
        ]

        return self.json_response({'summaries': summaries_data}, 200)


class SummaryDetailView(APIView):
    """
    GET /api/summaries/<id>/

    Get full details for a specific summary.
    Returns content, metadata, and cost information.
    """

    def get(self, request, summary_id):
        """Get summary details."""
        try:
            summary = Summary.objects.select_related(
                'chapter', 'prompt'
            ).get(pk=summary_id)
        except Summary.DoesNotExist:
            return self.error_response('Summary not found', 404)

        # Extract content from content_json
        content = summary.content_json.get('text', '')

        # Build response
        response_data = {
            'id': summary.id,
            'version': summary.version,
            'content': content,
            'content_markdown': content,  # For JS compatibility
            'prompt_name': summary.prompt.name if summary.prompt else 'Unknown',
            'prompt_id': summary.prompt.id if summary.prompt else None,
            'model_used': summary.model_used,
            'cost_usd': str(summary.estimated_cost_usd),
            'tokens_used': summary.tokens_used,
            'processing_time_ms': summary.processing_time_ms,
            'created_at': summary.created_at.isoformat(),
            'chapter_id': summary.chapter.id,
            'chapter_title': summary.chapter.title or f'Chapter {summary.chapter.chapter_number}',
        }

        return self.json_response(response_data, 200)


# Task Group 3.2: Batch Processing API


class BatchPreviewView(APIView):
    """
    POST /api/summaries/batch-preview/

    Preview total cost for batch summary generation.
    Returns per-chapter estimates and total cost.
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request):
        """Generate batch cost preview."""
        # Parse request body
        data = self.parse_json_body(request)
        if data is None:
            return self.error_response('Invalid JSON in request body', 400)

        # Validate required fields
        chapter_ids = data.get('chapter_ids', [])
        prompt_id = data.get('prompt_id')
        model = data.get('model', 'gpt-4o-mini')

        if not chapter_ids:
            return self.error_response('chapter_ids is required', 400)

        if not prompt_id:
            return self.error_response('prompt_id is required', 400)

        # Check if AI features are enabled
        settings = Settings.get_settings()
        if not settings.ai_features_enabled:
            return self.error_response(
                'AI features are currently disabled. Visit Settings to enable.',
                403
            )

        # Check if prompt exists
        try:
            prompt = Prompt.objects.get(pk=prompt_id)
        except Prompt.DoesNotExist:
            return self.error_response('Prompt not found', 404)

        # Fetch all chapters
        chapters = Chapter.objects.select_related('book').filter(pk__in=chapter_ids)

        if len(chapters) != len(chapter_ids):
            return self.error_response('One or more chapters not found', 404)

        try:
            # Initialize cost service
            cost_service = CostControlService()

            # Calculate cost for each chapter
            chapters_data = []
            total_tokens = 0
            total_cost = Decimal('0')

            for chapter in chapters:
                # Render prompt
                rendered_prompt = prompt.render_template({
                    'content': chapter.content,
                    'title': chapter.title or f'Chapter {chapter.chapter_number}',
                    'author': chapter.book.author,
                })

                # Count tokens
                input_tokens = cost_service.count_tokens(rendered_prompt, model)
                estimated_output_tokens = max(500, int(input_tokens * 0.2))

                # Estimate cost
                cost_estimate = cost_service.estimate_cost(
                    input_tokens=input_tokens,
                    output_tokens=estimated_output_tokens,
                    model=model
                )

                chapters_data.append({
                    'chapter_id': chapter.id,
                    'chapter_title': chapter.title or f'Chapter {chapter.chapter_number}',
                    'estimated_tokens': cost_estimate['total_tokens'],
                    'estimated_cost': str(cost_estimate['estimated_cost_usd']),
                })

                total_tokens += cost_estimate['total_tokens']
                total_cost += cost_estimate['estimated_cost_usd']

            # Check if batch would exceed limits
            usage_check = cost_service.check_limits(total_cost)

            # Build response
            response_data = {
                'total_cost_usd': str(total_cost),
                'total_tokens': total_tokens,
                'total_chapters': len(chapters),
                'chapters': chapters_data,
                'daily_usage': usage_check['daily_usage'],
                'monthly_usage': usage_check['monthly_usage'],
                'warnings': usage_check['warnings'],
            }

            return self.json_response(response_data, 200)

        except LimitExceededException as e:
            return self.error_response(str(e), 403)
        except Exception as e:
            logger.error(f'Error in batch preview: {str(e)}')
            return self.error_response(f'Internal error: {str(e)}', 500)


class BatchGenerateView(APIView):
    """
    POST /api/summaries/batch-generate/

    Initiate batch summary generation (requires confirmed=true).
    Creates ProcessingJob and triggers background processing.
    Returns job_id for WebSocket tracking.
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request):
        """Create batch processing job and trigger background processing."""
        # Parse request body
        data = self.parse_json_body(request)
        if data is None:
            return self.error_response('Invalid JSON in request body', 400)

        # Validate required fields
        chapter_ids = data.get('chapter_ids', [])
        prompt_id = data.get('prompt_id')
        model = data.get('model', 'gpt-4o-mini')
        confirmed = data.get('confirmed', False)

        if not chapter_ids:
            return self.error_response('chapter_ids is required', 400)

        if not prompt_id:
            return self.error_response('prompt_id is required', 400)

        if not confirmed:
            return self.error_response(
                'Two-step confirmation required: confirmed must be true',
                400
            )

        # Check if AI features are enabled
        settings = Settings.get_settings()
        if not settings.ai_features_enabled:
            return self.error_response(
                'AI features are currently disabled. Visit Settings to enable.',
                403
            )

        # Check if prompt exists
        try:
            prompt = Prompt.objects.get(pk=prompt_id)
        except Prompt.DoesNotExist:
            return self.error_response('Prompt not found', 404)

        # Fetch all chapters and get book
        chapters = Chapter.objects.select_related('book').filter(pk__in=chapter_ids)

        if len(chapters) != len(chapter_ids):
            return self.error_response('One or more chapters not found', 404)

        if not chapters:
            return self.error_response('No chapters provided', 400)

        # Get book from first chapter (all chapters should be from same book)
        book = chapters[0].book

        try:
            # Create ProcessingJob
            with transaction.atomic():
                job = ProcessingJob.objects.create(
                    book=book,
                    job_type='batch_summarization',
                    status='pending',
                    progress_percent=0,
                    metadata={
                        'chapter_ids': chapter_ids,
                        'prompt_id': prompt_id,
                        'model': model,
                        'total_chapters': len(chapter_ids),
                        'chapters': {},  # Will be populated during processing
                    }
                )

            # Trigger batch processing in background thread
            def run_batch_processing():
                """Background thread function to process batch."""
                try:
                    batch_service = BatchProcessingService()
                    batch_service.process_batch(
                        job_id=str(job.id),
                        chapter_ids=chapter_ids,
                        prompt_id=prompt_id,
                        model=model
                    )
                except Exception as e:
                    logger.error(f'Background batch processing failed for job {job.id}: {str(e)}', exc_info=True)

            # Start background thread
            thread = threading.Thread(target=run_batch_processing, daemon=True)
            thread.start()

            logger.info(f'Batch processing job {job.id} created and background thread started')

            # Build response
            response_data = {
                'job_id': str(job.id),
                'status': job.status,
                'total_chapters': len(chapter_ids),
                'message': 'Batch job created. Connect to WebSocket to track progress.',
                'websocket_url': f'ws://localhost:8000/ws/batch/{job.id}/',
            }

            return self.json_response(response_data, 202)

        except Exception as e:
            logger.error(f'Error creating batch job: {str(e)}')
            return self.error_response(f'Failed to create batch job: {str(e)}', 500)


# Task Group 3.3: Prompt Management API


class PromptsListView(APIView):
    """
    GET /api/prompts/?is_fabric=<bool>

    List all prompts with optional filtering.
    Returns prompt metadata for UI dropdowns.
    """

    def get(self, request):
        """List all prompts."""
        # Get optional filter
        is_fabric = request.GET.get('is_fabric')

        # Build query
        prompts = Prompt.objects.all()

        if is_fabric is not None:
            is_fabric_bool = is_fabric.lower() in ('true', '1', 'yes')
            prompts = prompts.filter(is_fabric=is_fabric_bool)

        # Order by category and name
        prompts = prompts.order_by('category', 'name')

        # Build response
        prompts_data = [
            {
                'id': prompt.id,
                'name': prompt.name,
                'category': prompt.category,
                'is_fabric': prompt.is_fabric,
                'is_custom': prompt.is_custom,
                'version': prompt.version,
                'default_model': prompt.default_model,
            }
            for prompt in prompts
        ]

        return self.json_response({'prompts': prompts_data}, 200)


class FabricSyncView(APIView):
    """
    POST /api/prompts/fabric/sync/

    Sync Fabric prompts from GitHub.
    Fetches default prompts and creates/updates Prompt records.
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request):
        """Sync Fabric prompts from GitHub."""
        try:
            # Initialize Fabric prompt service
            fabric_service = FabricPromptService()

            # Use default prompt list
            default_prompts = [
                'extract_wisdom',
                'summarize',
                'analyze_prose',
                'explain_code',
                'extract_article_wisdom',
                'create_reading_plan',
                'rate_content',
            ]

            # Sync prompts
            result = fabric_service.sync_prompts(default_prompts)

            # Build response
            response_data = {
                'synced': result['synced'],
                'failed': result['failed'],
                'errors': result['errors'],
                'total': len(default_prompts),
            }

            return self.json_response(response_data, 200)

        except Exception as e:
            logger.error(f'Error syncing Fabric prompts: {str(e)}')
            return self.error_response(f'Failed to sync prompts: {str(e)}', 500)


class PromptPreviewView(APIView):
    """
    GET /api/prompts/<id>/preview/?variables=<json>

    Preview rendered prompt with sample or provided variables.
    Useful for showing users what the prompt looks like before use.
    """

    def get(self, request, prompt_id):
        """Preview prompt with sample variables."""
        # Check if prompt exists
        try:
            prompt = Prompt.objects.get(pk=prompt_id)
        except Prompt.DoesNotExist:
            return self.error_response('Prompt not found', 404)

        # Get variables from query param (optional)
        variables_param = request.GET.get('variables')

        if variables_param:
            try:
                variables = json.loads(variables_param)
            except json.JSONDecodeError:
                return self.error_response('Invalid JSON in variables parameter', 400)
        else:
            # Use sample variables
            variables = {
                'content': 'Sample chapter content...',
                'title': 'Sample Chapter Title',
                'author': 'Sample Author',
            }

        # Render prompt
        try:
            fabric_service = FabricPromptService()
            rendered_text = fabric_service.preview_prompt(prompt, variables)

            # Build response
            response_data = {
                'prompt_id': prompt.id,
                'prompt_name': prompt.name,
                'rendered_text': rendered_text,
                'variables_used': variables,
            }

            return self.json_response(response_data, 200)

        except Exception as e:
            logger.error(f'Error previewing prompt: {str(e)}')
            return self.error_response(f'Failed to preview prompt: {str(e)}', 500)


# Task Group 3.4: Settings & Usage API


class SettingsView(APIView):
    """
    GET /api/settings/ - Get current settings
    PUT /api/settings/ - Update settings

    Manage application-wide AI settings and limits.
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request):
        """Get current settings."""
        settings = Settings.get_settings()

        response_data = {
            'monthly_limit_usd': str(settings.monthly_limit_usd),
            'daily_summary_limit': settings.daily_summary_limit,
            'ai_features_enabled': settings.ai_features_enabled,
            'default_model': settings.default_model,
            'created_at': settings.created_at.isoformat(),
            'updated_at': settings.updated_at.isoformat(),
        }

        return self.json_response(response_data, 200)

    def put(self, request):
        """Update settings."""
        # Parse request body
        data = self.parse_json_body(request)
        if data is None:
            return self.error_response('Invalid JSON in request body', 400)

        # Get current settings
        settings = Settings.get_settings()

        # Update fields
        if 'monthly_limit_usd' in data:
            try:
                monthly_limit = Decimal(str(data['monthly_limit_usd']))
                if monthly_limit < 0:
                    return self.error_response('monthly_limit_usd must be non-negative', 400)
                settings.monthly_limit_usd = monthly_limit
            except (ValueError, TypeError):
                return self.error_response('Invalid monthly_limit_usd value', 400)

        if 'daily_summary_limit' in data:
            try:
                daily_limit = int(data['daily_summary_limit'])
                if daily_limit < 0:
                    return self.error_response('daily_summary_limit must be non-negative', 400)
                settings.daily_summary_limit = daily_limit
            except (ValueError, TypeError):
                return self.error_response('Invalid daily_summary_limit value', 400)

        if 'ai_features_enabled' in data:
            if not isinstance(data['ai_features_enabled'], bool):
                return self.error_response('ai_features_enabled must be boolean', 400)
            settings.ai_features_enabled = data['ai_features_enabled']

        if 'default_model' in data:
            settings.default_model = str(data['default_model'])

        # Save settings
        try:
            settings.save()

            response_data = {
                'monthly_limit_usd': str(settings.monthly_limit_usd),
                'daily_summary_limit': settings.daily_summary_limit,
                'ai_features_enabled': settings.ai_features_enabled,
                'default_model': settings.default_model,
                'updated_at': settings.updated_at.isoformat(),
            }

            return self.json_response(response_data, 200)

        except Exception as e:
            logger.error(f'Error updating settings: {str(e)}')
            return self.error_response(f'Failed to update settings: {str(e)}', 500)


class UsageStatsView(APIView):
    """
    GET /api/settings/usage/

    Get current usage statistics for cost control.
    Returns daily and monthly usage with percentage of limits.
    """

    def get(self, request):
        """Get current usage stats."""
        try:
            # Initialize cost control service
            cost_service = CostControlService()

            # Get current usage
            usage_stats = cost_service.get_current_usage()

            return self.json_response(usage_stats, 200)

        except Exception as e:
            logger.error(f'Error fetching usage stats: {str(e)}')
            return self.error_response(f'Failed to fetch usage stats: {str(e)}', 500)


class UsageHistoryView(APIView):
    """
    GET /api/settings/usage/history/?days=<int>

    Get historical usage data for the last N days.
    Useful for displaying usage trends and charts.
    """

    def get(self, request):
        """Get usage history."""
        from datetime import timedelta
        from .models import UsageTracking

        # Get days parameter (default to 30)
        try:
            days = int(request.GET.get('days', 30))
            if days < 1 or days > 365:
                return self.error_response('days must be between 1 and 365', 400)
        except ValueError:
            return self.error_response('Invalid days parameter', 400)

        # Calculate date range
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days-1)

        # Fetch usage records
        usage_records = UsageTracking.objects.filter(
            date__gte=start_date,
            date__lte=end_date
        ).order_by('-date')

        # Build response
        history_data = [
            {
                'date': record.date.isoformat(),
                'daily_summaries_count': record.daily_summaries_count,
                'daily_tokens_used': record.daily_tokens_used,
                'daily_cost_usd': str(record.daily_cost_usd),
                'monthly_summaries_count': record.monthly_summaries_count,
                'monthly_tokens_used': record.monthly_tokens_used,
                'monthly_cost_usd': str(record.monthly_cost_usd),
            }
            for record in usage_records
        ]

        return self.json_response({
            'history': history_data,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'days': days,
        }, 200)
