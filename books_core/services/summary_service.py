"""
SummaryService - Service for managing summary version history and creation.

Handles:
- Version number calculation
- Summary creation with version linking
- Version retrieval and comparison
- Chapter has_summary flag updates
"""

import logging
from typing import Tuple, Optional
from decimal import Decimal
from django.db import transaction
from django.db.models import QuerySet, Max

from books_core.models import Chapter, Prompt, Summary

logger = logging.getLogger(__name__)


class SummaryService:
    """
    Service for managing summary version history and creation.
    """

    def get_next_version(
        self,
        chapter: Chapter,
        prompt: Prompt
    ) -> Tuple[int, Optional[Summary]]:
        """
        Calculate the next version number for a chapter+prompt combination.

        Args:
            chapter: Chapter instance
            prompt: Prompt instance

        Returns:
            Tuple of (next_version_number, most_recent_summary_or_none)
            - next_version_number: Integer version to use for new summary
            - most_recent_summary_or_none: Previous summary to link as previous_version,
              or None if this is the first version
        """
        # Query for existing summaries with this chapter+prompt
        existing_summaries = Summary.objects.filter(
            chapter=chapter,
            prompt=prompt
        ).order_by('-version')

        # Get the most recent version
        most_recent = existing_summaries.first()

        if most_recent:
            next_version = most_recent.version + 1
            logger.info(
                f"Found existing version {most_recent.version} for "
                f"chapter {chapter.id}, prompt {prompt.id}. "
                f"Next version: {next_version}"
            )
            return next_version, most_recent
        else:
            logger.info(
                f"No existing versions for chapter {chapter.id}, prompt {prompt.id}. "
                f"Starting with version 1"
            )
            return 1, None

    @transaction.atomic
    def create_summary(
        self,
        chapter: Chapter,
        prompt: Prompt,
        content: str,
        metadata: dict
    ) -> Summary:
        """
        Create a new summary with proper version management.

        This method:
        1. Calculates next version number
        2. Links to previous version if exists
        3. Creates Summary record
        4. Updates chapter.has_summary flag
        5. All within atomic transaction

        Args:
            chapter: Chapter instance
            prompt: Prompt instance
            content: AI-generated summary content
            metadata: Dictionary containing:
                - tokens_used: int
                - model_used: str
                - processing_time_ms: int
                - estimated_cost_usd: Decimal or str
                - summary_type: str (optional, defaults to 'tldr')

        Returns:
            Created Summary instance

        Raises:
            Exception: If creation fails (transaction rolled back)
        """
        # Get next version and previous summary
        next_version, previous_summary = self.get_next_version(chapter, prompt)

        # Extract metadata
        tokens_used = metadata.get('tokens_used', 0)
        model_used = metadata.get('model_used', 'gpt-4o-mini')
        processing_time_ms = metadata.get('processing_time_ms', 0)
        estimated_cost_usd = metadata.get('estimated_cost_usd', Decimal('0'))
        summary_type = metadata.get('summary_type', 'tldr')

        # Convert cost to Decimal if string
        if isinstance(estimated_cost_usd, str):
            estimated_cost_usd = Decimal(estimated_cost_usd)

        # Prepare content_json
        content_json = {
            'text': content,
            'prompt_name': prompt.name,
            'model': model_used,
        }

        # Create summary
        summary = Summary.objects.create(
            chapter=chapter,
            prompt=prompt,
            summary_type=summary_type,
            content_json=content_json,
            tokens_used=tokens_used,
            model_used=model_used,
            processing_time_ms=processing_time_ms,
            version=next_version,
            previous_version=previous_summary,
            estimated_cost_usd=estimated_cost_usd,
        )

        # Update chapter.has_summary flag if this is the first summary
        if not chapter.has_summary:
            chapter.has_summary = True
            chapter.save(update_fields=['has_summary'])

        logger.info(
            f"Created summary version {next_version} for chapter {chapter.id}, "
            f"prompt {prompt.id}. Cost: ${estimated_cost_usd:.6f}, "
            f"Tokens: {tokens_used}"
        )

        return summary

    def get_versions(self, chapter: Chapter, prompt: Prompt) -> QuerySet[Summary]:
        """
        Get all versions of summaries for a chapter+prompt combination.

        Args:
            chapter: Chapter instance
            prompt: Prompt instance

        Returns:
            QuerySet of Summary instances ordered by version DESC (newest first)
        """
        return Summary.objects.filter(
            chapter=chapter,
            prompt=prompt
        ).order_by('-version').select_related('prompt')

    def get_summary_by_version(
        self,
        chapter: Chapter,
        prompt: Prompt,
        version: int
    ) -> Optional[Summary]:
        """
        Get a specific version of a summary.

        Args:
            chapter: Chapter instance
            prompt: Prompt instance
            version: Version number

        Returns:
            Summary instance or None if not found
        """
        try:
            return Summary.objects.get(
                chapter=chapter,
                prompt=prompt,
                version=version
            )
        except Summary.DoesNotExist:
            logger.warning(
                f"Summary not found: chapter {chapter.id}, "
                f"prompt {prompt.id}, version {version}"
            )
            return None

    def get_latest_summary(
        self,
        chapter: Chapter,
        prompt: Prompt
    ) -> Optional[Summary]:
        """
        Get the most recent version of a summary for a chapter+prompt.

        Args:
            chapter: Chapter instance
            prompt: Prompt instance

        Returns:
            Summary instance or None if no summaries exist
        """
        return Summary.objects.filter(
            chapter=chapter,
            prompt=prompt
        ).order_by('-version').first()

    def get_all_summaries_for_chapter(self, chapter: Chapter) -> QuerySet[Summary]:
        """
        Get all summaries for a chapter, across all prompts.

        Args:
            chapter: Chapter instance

        Returns:
            QuerySet of Summary instances ordered by created_at DESC
        """
        return Summary.objects.filter(
            chapter=chapter
        ).order_by('-created_at').select_related('prompt')

    def compare_versions(
        self,
        summary1: Summary,
        summary2: Summary
    ) -> dict:
        """
        Compare two summary versions.

        Args:
            summary1: First Summary instance
            summary2: Second Summary instance

        Returns:
            Dictionary with comparison data:
            {
                'summary1': {...},
                'summary2': {...},
                'same_chapter': bool,
                'same_prompt': bool,
            }
        """
        return {
            'summary1': {
                'id': summary1.id,
                'version': summary1.version,
                'content': summary1.content_json.get('text', ''),
                'prompt_name': summary1.prompt.name if summary1.prompt else 'Unknown',
                'model_used': summary1.model_used,
                'tokens_used': summary1.tokens_used,
                'cost_usd': str(summary1.estimated_cost_usd),
                'created_at': summary1.created_at.isoformat(),
            },
            'summary2': {
                'id': summary2.id,
                'version': summary2.version,
                'content': summary2.content_json.get('text', ''),
                'prompt_name': summary2.prompt.name if summary2.prompt else 'Unknown',
                'model_used': summary2.model_used,
                'tokens_used': summary2.tokens_used,
                'cost_usd': str(summary2.estimated_cost_usd),
                'created_at': summary2.created_at.isoformat(),
            },
            'same_chapter': summary1.chapter_id == summary2.chapter_id,
            'same_prompt': summary1.prompt_id == summary2.prompt_id,
        }
