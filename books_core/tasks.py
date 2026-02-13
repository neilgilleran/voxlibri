"""
Django-Q2 task definitions for VoxLibri.

Tasks for parallel chapter analysis with hook-based coordination.
Run `python manage.py qcluster` to start the task worker.
"""
import json
import logging
import time
from decimal import Decimal
from typing import Dict, Any, List, Optional

from django.db import transaction
from django.db.models import F, Max

from books_core.models import Book, Chapter, Prompt, Summary, ProcessingJob
from books_core.services.openai_service import OpenAIService
from books_core.services.cost_control_service import CostControlService
from books_core.services.summary_service import SummaryService

logger = logging.getLogger(__name__)

# Prompts to run per chapter
CHAPTER_PROMPTS = ['rate_chapter', 'summarize_chapter']
EXTRACTION_PROMPTS = ['extract_chapter_wisdom', 'extract_references']
MIN_CHAPTER_WORDS = 500


def process_chapter_analysis(
    chapter_id: int,
    job_id: int,
    model: str = 'gpt-4o-mini'
) -> Dict[str, Any]:
    """
    Process all analysis prompts for a single chapter.

    Runs: rate_chapter, summarize_chapter, extract_chapter_wisdom, extract_references

    Args:
        chapter_id: Chapter to analyze
        job_id: ProcessingJob ID for tracking
        model: AI model to use

    Returns:
        Dict with chapter results (rating, summary, extractions)
    """
    chapter = Chapter.objects.select_related('book').get(id=chapter_id)

    # Load prompts
    prompts = {}
    for prompt_name in CHAPTER_PROMPTS + EXTRACTION_PROMPTS:
        prompt = Prompt.objects.filter(name=prompt_name).first()
        if prompt:
            prompts[prompt_name] = prompt

    openai_service = OpenAIService(model=model)
    summary_service = SummaryService()

    results = {
        'chapter_id': chapter_id,
        'job_id': job_id,
        'chapter_number': chapter.chapter_number,
        'chapter_title': chapter.title,
        'rating': None,
        'summary': None,
        'extractions': {},
        'success': True,
        'errors': []
    }

    # Process each prompt
    for prompt_name in CHAPTER_PROMPTS + EXTRACTION_PROMPTS:
        if prompt_name not in prompts:
            results['errors'].append(f"Prompt {prompt_name} not found")
            continue

        prompt = prompts[prompt_name]

        try:
            start_time = time.time()
            rendered_prompt = prompt.render_template({'content': chapter.content})

            result = openai_service.complete_with_cost_control(
                prompt=rendered_prompt,
                model=model,
                max_tokens=1500,
            )

            processing_time_ms = int((time.time() - start_time) * 1000)

            summary = summary_service.create_summary(
                chapter=chapter,
                prompt=prompt,
                content=result['content'],
                metadata={
                    'tokens_used': result['tokens_used'],
                    'model_used': result['model'],
                    'processing_time_ms': processing_time_ms,
                    'estimated_cost_usd': result['actual_cost_usd'],
                    'summary_type': 'analysis' if prompt.category == 'rating' else 'tldr',
                }
            )

            # Store results for aggregation
            if prompt_name == 'rate_chapter':
                results['rating'] = _parse_rating(summary)
                if results['rating']:
                    results['rating']['chapter_number'] = chapter.chapter_number
                    results['rating']['chapter_title'] = chapter.title
            elif prompt_name == 'summarize_chapter':
                results['summary'] = {
                    'chapter_number': chapter.chapter_number,
                    'chapter_title': chapter.title or f"Chapter {chapter.chapter_number}",
                    'content': summary.content_json.get('text', '')
                }
            else:
                results['extractions'][prompt_name] = {
                    'chapter_number': chapter.chapter_number,
                    'chapter_title': chapter.title or f"Chapter {chapter.chapter_number}",
                    'content': summary.content_json.get('text', '')
                }

            logger.info(f"Processed {prompt_name} for chapter {chapter_id}")

        except Exception as e:
            logger.error(f"Error processing {prompt_name} for chapter {chapter_id}: {e}")
            results['errors'].append(f"{prompt_name}: {str(e)}")
            results['success'] = False

    return results


def chapter_analysis_complete(task):
    """
    Hook called when a chapter analysis task completes.

    Updates ProcessingJob progress and triggers aggregation if all chapters done.
    """
    result = task.result
    if not result:
        logger.warning(f"Task {task.id} completed with no result")
        return

    job_id = result.get('job_id')
    if not job_id:
        logger.warning(f"Task {task.id} result missing job_id")
        return

    try:
        job = ProcessingJob.objects.get(id=job_id)
        progress_increment = job.metadata.get('progress_per_chapter', 5)

        # Atomic update of progress and chapters_processed
        with transaction.atomic():
            # Update progress
            ProcessingJob.objects.filter(id=job_id).update(
                progress_percent=F('progress_percent') + progress_increment
            )

            # Update chapters_processed in metadata
            job.refresh_from_db()
            job.metadata['chapters_processed'] = job.metadata.get('chapters_processed', 0) + 1

            # Store chapter results for aggregation
            chapter_results = job.metadata.get('chapter_results', [])
            chapter_results.append(result)
            job.metadata['chapter_results'] = chapter_results
            job.save(update_fields=['metadata'])

        # Check if all chapters done
        chapters_processed = job.metadata.get('chapters_processed', 0)
        total_chapters = job.metadata.get('chapter_count', 0)

        logger.info(f"Job {job_id}: {chapters_processed}/{total_chapters} chapters complete")

        if chapters_processed >= total_chapters:
            # All chapters done - queue aggregation
            from django_q.tasks import async_task

            job.metadata['current_phase'] = 'aggregation'
            job.save(update_fields=['metadata'])

            async_task(
                'books_core.tasks.run_book_aggregation',
                job.book_id,
                job.id,
                job.metadata.get('model', 'gpt-4o-mini'),
                hook='books_core.tasks.aggregation_complete',
                task_name=f'aggregate_book_{job.book_id}'
            )

            logger.info(f"Job {job_id}: All chapters done, queued aggregation")

    except ProcessingJob.DoesNotExist:
        logger.error(f"ProcessingJob {job_id} not found in hook")
    except Exception as e:
        logger.error(f"Error in chapter_analysis_complete hook: {e}")


def run_book_aggregation(
    book_id: int,
    job_id: int,
    model: str = 'gpt-4o-mini'
) -> Dict[str, Any]:
    """
    Run book-level aggregation after all chapters processed.

    Aggregates ratings and summaries, generates book-level verdict.
    """
    book = Book.objects.get(id=book_id)
    job = ProcessingJob.objects.get(id=job_id)

    # Compute readability metrics if not already done (safety net)
    if not book.readability_metrics:
        from books_core.services.readability_service import ReadabilityService
        ReadabilityService().compute_all_for_book(book)

    # Collect results from metadata
    chapter_results = job.metadata.get('chapter_results', [])

    chapter_ratings = [r['rating'] for r in chapter_results if r.get('rating')]
    chapter_summaries = [r['summary'] for r in chapter_results if r.get('summary')]
    extractions = {
        'extract_chapter_wisdom': [],
        'extract_references': []
    }
    for r in chapter_results:
        for ext_type in extractions:
            if r.get('extractions', {}).get(ext_type):
                extractions[ext_type].append(r['extractions'][ext_type])

    openai_service = OpenAIService(model=model)
    results = {'success': True, 'errors': []}

    # 1. Aggregate ratings
    if chapter_ratings:
        try:
            _aggregate_book_rating(book, chapter_ratings, model, openai_service)
            logger.info(f"Book {book_id}: Rating aggregation complete")
        except Exception as e:
            logger.error(f"Error aggregating ratings: {e}")
            results['errors'].append(f"rating_aggregation: {str(e)}")

    # Update progress
    ProcessingJob.objects.filter(id=job_id).update(progress_percent=88)

    # 2. Aggregate summaries into book thesis
    if chapter_summaries:
        try:
            _aggregate_summaries(book, chapter_summaries, model, openai_service)
            logger.info(f"Book {book_id}: Summary aggregation complete")
        except Exception as e:
            logger.error(f"Error aggregating summaries: {e}")
            results['errors'].append(f"summary_aggregation: {str(e)}")

    # Update progress
    ProcessingJob.objects.filter(id=job_id).update(progress_percent=92)

    # 3. Aggregate extractions (concatenation, no AI)
    try:
        _aggregate_extractions_concat(book, extractions)
        logger.info(f"Book {book_id}: Extraction aggregation complete")
    except Exception as e:
        logger.error(f"Error aggregating extractions: {e}")
        results['errors'].append(f"extraction_aggregation: {str(e)}")

    # Update progress
    ProcessingJob.objects.filter(id=job_id).update(progress_percent=95)

    results['book_id'] = book_id
    results['job_id'] = job_id
    return results


def aggregation_complete(task):
    """
    Hook called when aggregation completes.
    Marks job as completed or failed.
    """
    result = task.result
    if not result:
        logger.warning(f"Aggregation task {task.id} completed with no result")
        return

    job_id = result.get('job_id')
    if not job_id:
        return

    try:
        job = ProcessingJob.objects.get(id=job_id)

        if result.get('errors'):
            job.error_message = '; '.join(result['errors'])

        job.status = 'completed'
        job.progress_percent = 100
        job.metadata['current_phase'] = 'done'
        job.save()

        logger.info(f"Job {job_id}: Pipeline completed")

    except ProcessingJob.DoesNotExist:
        logger.error(f"ProcessingJob {job_id} not found in aggregation hook")


# Helper functions (extracted from ChapterAnalysisPipelineService)

def _parse_rating(summary: Summary) -> Optional[Dict[str, Any]]:
    """Parse rating JSON from a rate_chapter summary."""
    try:
        content = summary.content_json.get('text', '')

        if '```json' in content:
            json_start = content.find('```json') + 7
            json_end = content.find('```', json_start)
            json_str = content[json_start:json_end].strip()
        elif '```' in content:
            json_start = content.find('```') + 3
            json_end = content.find('```', json_start)
            json_str = content[json_start:json_end].strip()
        elif '{' in content:
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            json_str = content[json_start:json_end]
        else:
            return None

        return json.loads(json_str)

    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse rating: {e}")
        return None


def _aggregate_book_rating(
    book: Book,
    chapter_ratings: List[Dict[str, Any]],
    model: str,
    openai_service: OpenAIService
) -> Summary:
    """Calculate averages and generate AI book-level verdict."""
    criteria = ['insight', 'clarity', 'evidence', 'engagement', 'actionability', 'overall']
    averages = {}

    for criterion in criteria:
        values = [r.get(criterion, 0) for r in chapter_ratings if criterion in r]
        if values:
            averages[f'{criterion}_avg'] = round(sum(values) / len(values), 1)
        else:
            averages[f'{criterion}_avg'] = 0

    sorted_by_overall = sorted(
        chapter_ratings,
        key=lambda r: r.get('overall', 0),
        reverse=True
    )

    best_chapter = sorted_by_overall[0] if sorted_by_overall else None
    worst_chapter = sorted_by_overall[-1] if len(sorted_by_overall) > 1 else None

    verdicts = [
        f"Chapter {r.get('chapter_number', '?')}: {r.get('one_line_verdict', 'No verdict')}"
        for r in chapter_ratings
    ]

    aggregate_prompt = f"""You are rating the book "{book.title}" based on chapter-by-chapter analysis.

AVERAGE SCORES (1-10 scale):
- Insight: {averages.get('insight_avg', 0)}
- Clarity: {averages.get('clarity_avg', 0)}
- Evidence: {averages.get('evidence_avg', 0)}
- Engagement: {averages.get('engagement_avg', 0)}
- Actionability: {averages.get('actionability_avg', 0)}
- Overall: {averages.get('overall_avg', 0)}

CHAPTER VERDICTS:
{chr(10).join(verdicts)}

Based on these scores and chapter verdicts, write a 2-3 sentence overall verdict for the book.
Focus on what makes this book valuable (or not) and who would benefit from reading it.

Output ONLY the verdict text, no additional formatting."""

    start_time = time.time()
    result = openai_service.complete_with_cost_control(
        prompt=aggregate_prompt,
        model=model,
        max_tokens=500,
    )
    processing_time_ms = int((time.time() - start_time) * 1000)

    verdict = result['content'].strip()
    if verdict.startswith('"') and verdict.endswith('"'):
        verdict = verdict[1:-1]

    aggregate_data = {
        **averages,
        'chapters_analyzed': len(chapter_ratings),
        'verdict': verdict,
        'best_chapter': {
            'number': best_chapter.get('chapter_number'),
            'title': best_chapter.get('chapter_title', ''),
            'score': best_chapter.get('overall', 0),
        } if best_chapter else None,
        'worst_chapter': {
            'number': worst_chapter.get('chapter_number'),
            'title': worst_chapter.get('chapter_title', ''),
            'score': worst_chapter.get('overall', 0),
        } if worst_chapter else None,
    }

    aggregate_prompt_obj, _ = Prompt.objects.get_or_create(
        name='aggregate_book_rating',
        defaults={
            'category': 'rating',
            'template_text': 'Book rating aggregation prompt',
            'default_model': 'gpt-4o-mini',
        }
    )

    existing_max = Summary.objects.filter(
        book=book,
        prompt=aggregate_prompt_obj
    ).aggregate(Max('version'))['version__max'] or 0
    next_version = existing_max + 1

    summary = Summary.objects.create(
        book=book,
        chapter=None,
        prompt=aggregate_prompt_obj,
        summary_type='analysis',
        content_json={
            'text': json.dumps(aggregate_data, indent=2),
            'prompt_name': 'aggregate_book_rating',
            'model': model,
            'aggregate_data': aggregate_data,
        },
        tokens_used=result['tokens_used'],
        model_used=result['model'],
        processing_time_ms=processing_time_ms,
        version=next_version,
        estimated_cost_usd=Decimal(result['actual_cost_usd']),
    )

    return summary


def _aggregate_summaries(
    book: Book,
    chapter_summaries: List[Dict],
    model: str,
    openai_service: OpenAIService
) -> Optional[Summary]:
    """Aggregate chapter summaries into book thesis using AI."""
    aggregate_prompt = Prompt.objects.filter(name='aggregate_summaries').first()
    if not aggregate_prompt:
        logger.warning("Aggregate prompt 'aggregate_summaries' not found")
        return None

    # Format chapter summaries
    sections = []
    for item in chapter_summaries:
        section = f"## Chapter {item['chapter_number']}"
        if item.get('chapter_title'):
            section += f": {item['chapter_title']}"
        section += f"\n\n{item['content']}"
        sections.append(section)
    chapters_text = "\n\n---\n\n".join(sections)

    rendered = aggregate_prompt.render_template({
        'chapter_summaries': chapters_text,
        'book_title': book.title
    })

    start_time = time.time()
    try:
        result = openai_service.complete_with_cost_control(
            prompt=rendered,
            model=model,
            max_tokens=1000,
        )
    except Exception as e:
        logger.error(f"Failed to aggregate summaries: {e}")
        return None

    processing_time_ms = int((time.time() - start_time) * 1000)

    existing_max = Summary.objects.filter(
        book=book,
        prompt=aggregate_prompt
    ).aggregate(Max('version'))['version__max'] or 0
    next_version = existing_max + 1

    summary = Summary.objects.create(
        book=book,
        chapter=None,
        prompt=aggregate_prompt,
        summary_type='analysis',
        content_json={
            'text': result['content'],
            'prompt_name': 'aggregate_summaries',
            'model': model,
            'chapters_included': len(chapter_summaries),
        },
        tokens_used=result['tokens_used'],
        model_used=result['model'],
        processing_time_ms=processing_time_ms,
        version=next_version,
        estimated_cost_usd=Decimal(result['actual_cost_usd']),
    )

    return summary


def _aggregate_extractions_concat(
    book: Book,
    extractions: Dict[str, List[Dict]]
) -> Dict[str, Summary]:
    """Aggregate extractions via concatenation (no AI)."""
    import re

    results = {}

    for extraction_type, chapter_data in extractions.items():
        if not chapter_data:
            continue

        if extraction_type == 'extract_chapter_wisdom':
            prompt_name = 'grouped_wisdom'
            content = _group_wisdom_by_type(chapter_data)
        else:
            prompt_name = 'concatenated_references'
            sections = []
            for item in chapter_data:
                section = f"## Chapter {item['chapter_number']}"
                if item.get('chapter_title'):
                    section += f": {item['chapter_title']}"
                section += f"\n\n{item['content']}"
                sections.append(section)
            content = "\n\n---\n\n".join(sections)

        prompt, _ = Prompt.objects.get_or_create(
            name=prompt_name,
            defaults={
                'category': 'aggregation',
                'template_text': f'{prompt_name} - concatenated content',
                'default_model': 'none',
            }
        )

        existing_max = Summary.objects.filter(
            book=book,
            prompt=prompt
        ).aggregate(Max('version'))['version__max'] or 0
        next_version = existing_max + 1

        summary = Summary.objects.create(
            book=book,
            chapter=None,
            prompt=prompt,
            summary_type='analysis',
            content_json={
                'text': content,
                'prompt_name': prompt_name,
                'model': 'concatenation',
                'extraction_type': extraction_type,
                'chapters_included': len(chapter_data),
                'is_concatenated': True,
            },
            tokens_used=0,
            model_used='concatenation',
            processing_time_ms=0,
            version=next_version,
            estimated_cost_usd=Decimal('0'),
        )

        results[extraction_type] = summary

    return results


def _group_wisdom_by_type(chapter_data: List[Dict]) -> str:
    """Parse chapter wisdom and group by type."""
    import re

    sections = {
        'stories': {'pattern': r'## THE STORY\s*\n(.*?)(?=\n---|\n## |$)', 'header': '## ALL STORIES', 'items': []},
        'advice': {'pattern': r'## THE ADVICE\s*\n(.*?)(?=\n---|\n## |$)', 'header': '## ALL ADVICE', 'items': []},
        'people': {'pattern': r'## PEOPLE WORTH KNOWING\s*\n(.*?)(?=\n---|\n## |$)', 'header': '## NOTABLE PEOPLE', 'items': []},
        'insights': {'pattern': r'## THE SURPRISE\s*\n(.*?)(?=\n---|\n## |$)', 'header': '## KEY INSIGHTS', 'items': []},
        'quotes': {'pattern': r'## QUOTABLE\s*\n(.*?)(?=\n---|\n## |$)', 'header': '## MEMORABLE QUOTES', 'items': []},
    }

    for item in chapter_data:
        chapter_num = item['chapter_number']
        chapter_title = item.get('chapter_title', f"Chapter {chapter_num}")
        content = item.get('content', '')

        for section_key, section_info in sections.items():
            match = re.search(section_info['pattern'], content, re.DOTALL | re.IGNORECASE)
            if match:
                extracted = match.group(1).strip()
                if extracted and 'no standout' not in extracted.lower() and 'skip' not in extracted.lower():
                    attribution = f"*From Chapter {chapter_num}: {chapter_title}*\n\n"
                    section_info['items'].append(attribution + extracted)

    output_parts = []
    for section_key, section_info in sections.items():
        if section_info['items']:
            output_parts.append(section_info['header'])
            output_parts.append('')
            for i, item in enumerate(section_info['items']):
                output_parts.append(item)
                if i < len(section_info['items']) - 1:
                    output_parts.append('')
                    output_parts.append('---')
                    output_parts.append('')
            output_parts.append('')

    return '\n'.join(output_parts)
