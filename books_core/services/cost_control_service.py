"""
CostControlService - Token counting, cost estimation, and limit enforcement.

Provides strict cost control for AI operations with:
- Token counting using tiktoken
- Cost estimation for OpenAI models
- Daily and monthly limit enforcement
- Atomic usage tracking with SELECT FOR UPDATE
"""

import logging
from decimal import Decimal
from datetime import date
from typing import Dict, Any
from django.db import transaction
import tiktoken

from books_core.models import Settings, UsageTracking
from books_core.exceptions import (
    LimitExceededException,
    CostEstimationException,
    EmergencyStopException
)

logger = logging.getLogger(__name__)


class CostControlService:
    """
    Service for managing cost control and usage tracking for AI operations.
    """

    # Pricing for gpt-4o-mini (per 1M tokens)
    MODEL_PRICING = {
        'gpt-4o-mini': {
            'input': Decimal('0.150'),   # $0.150 per 1M input tokens
            'output': Decimal('0.600'),  # $0.600 per 1M output tokens
        },
        'gpt-4o': {
            'input': Decimal('2.50'),
            'output': Decimal('10.00'),
        },
    }

    def __init__(self, model: str = 'gpt-4o-mini'):
        """
        Initialize cost control service.

        Args:
            model: OpenAI model name for pricing calculations
        """
        self.model = model

        # Initialize tokenizer
        try:
            self.encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            # Fallback to cl100k_base encoding for newer models
            logger.warning(f"Model {model} not found in tiktoken, using cl100k_base encoding")
            self.encoding = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str, model: str = None) -> int:
        """
        Count the number of tokens in a text string.

        Args:
            text: Text to count tokens for
            model: Optional model override

        Returns:
            Number of tokens

        Raises:
            CostEstimationException: If token counting fails
        """
        if not text:
            return 0

        try:
            # Use provided model or instance default
            if model and model != self.model:
                encoding = tiktoken.encoding_for_model(model)
            else:
                encoding = self.encoding

            return len(encoding.encode(text))
        except Exception as e:
            logger.error(f"Token counting failed: {str(e)}")
            raise CostEstimationException(f"Failed to count tokens: {str(e)}")

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str = None
    ) -> Dict[str, Any]:
        """
        Estimate cost for an OpenAI API call.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Estimated number of output tokens
            model: Model name (defaults to instance model)

        Returns:
            Dictionary with cost breakdown:
            {
                'total_tokens': int,
                'estimated_cost_usd': Decimal,
                'input_tokens': int,
                'output_tokens': int,
                'input_cost_usd': Decimal,
                'output_cost_usd': Decimal,
                'model': str
            }

        Raises:
            CostEstimationException: If model pricing not available
        """
        model_name = model or self.model

        if model_name not in self.MODEL_PRICING:
            raise CostEstimationException(
                f"Pricing not available for model: {model_name}. "
                f"Supported models: {', '.join(self.MODEL_PRICING.keys())}"
            )

        pricing = self.MODEL_PRICING[model_name]

        # Calculate costs (pricing is per 1M tokens)
        input_cost = (Decimal(input_tokens) / Decimal('1000000')) * pricing['input']
        output_cost = (Decimal(output_tokens) / Decimal('1000000')) * pricing['output']
        total_cost = input_cost + output_cost

        return {
            'total_tokens': input_tokens + output_tokens,
            'estimated_cost_usd': total_cost,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'input_cost_usd': input_cost,
            'output_cost_usd': output_cost,
            'model': model_name
        }

    def check_limits(self, estimated_cost: Decimal) -> Dict[str, Any]:
        """
        Check if operation would exceed daily or monthly limits.

        Args:
            estimated_cost: Estimated cost in USD for the operation

        Returns:
            Dictionary with current usage and warnings:
            {
                'daily_usage': {...},
                'monthly_usage': {...},
                'warnings': [...]
            }

        Raises:
            EmergencyStopException: If AI features are disabled
            LimitExceededException: If limits would be exceeded
        """
        # Get settings
        settings = Settings.get_settings()

        # Check emergency stop
        if not settings.ai_features_enabled:
            raise EmergencyStopException()

        # Get current usage
        today = date.today()
        month_year = today.strftime('%Y-%m')

        usage, _ = UsageTracking.objects.get_or_create(
            date=today,
            defaults={
                'month_year': month_year,
                'daily_summaries_count': 0,
                'daily_tokens_used': 0,
                'daily_cost_usd': Decimal('0'),
                'monthly_summaries_count': 0,
                'monthly_tokens_used': 0,
                'monthly_cost_usd': Decimal('0'),
            }
        )

        warnings = []

        # Check daily summary limit
        if usage.daily_summaries_count >= settings.daily_summary_limit:
            raise LimitExceededException(
                limit_type='daily',
                current=Decimal(usage.daily_summaries_count),
                limit=Decimal(settings.daily_summary_limit),
                message=(
                    f"Daily summary limit reached: {usage.daily_summaries_count} of "
                    f"{settings.daily_summary_limit} summaries used today. "
                    f"Please visit Settings to increase your daily limit."
                )
            )

        # Check monthly cost limit
        projected_monthly_cost = usage.monthly_cost_usd + estimated_cost
        if projected_monthly_cost > settings.monthly_limit_usd:
            raise LimitExceededException(
                limit_type='monthly',
                current=projected_monthly_cost,
                limit=settings.monthly_limit_usd,
                message=(
                    f"Monthly spending limit would be exceeded: "
                    f"${projected_monthly_cost:.6f} would exceed limit of "
                    f"${settings.monthly_limit_usd:.2f}. "
                    f"Please visit Settings to increase your monthly limit."
                )
            )

        # Add warnings at 80% and 90% thresholds
        daily_percent = (usage.daily_summaries_count / settings.daily_summary_limit) * 100
        if daily_percent >= 90:
            warnings.append(f"Warning: 90% of daily summary limit used ({usage.daily_summaries_count}/{settings.daily_summary_limit})")
        elif daily_percent >= 80:
            warnings.append(f"Warning: 80% of daily summary limit used ({usage.daily_summaries_count}/{settings.daily_summary_limit})")

        monthly_percent = (float(usage.monthly_cost_usd) / float(settings.monthly_limit_usd)) * 100
        if monthly_percent >= 90:
            warnings.append(f"Warning: 90% of monthly budget used (${usage.monthly_cost_usd:.6f}/${settings.monthly_limit_usd:.2f})")
        elif monthly_percent >= 80:
            warnings.append(f"Warning: 80% of monthly budget used (${usage.monthly_cost_usd:.6f}/${settings.monthly_limit_usd:.2f})")

        return {
            'daily_usage': {
                'summaries_count': usage.daily_summaries_count,
                'current_count': usage.daily_summaries_count,  # For JS compatibility
                'limit': settings.daily_summary_limit,
                'limit_count': settings.daily_summary_limit,  # For JS compatibility
                'remaining': settings.daily_summary_limit - usage.daily_summaries_count,
                'percent_used': daily_percent,
            },
            'monthly_usage': {
                'cost_usd': str(usage.monthly_cost_usd),
                'current': float(usage.monthly_cost_usd),  # For JS compatibility
                'limit_usd': str(settings.monthly_limit_usd),
                'limit': float(settings.monthly_limit_usd),  # For JS compatibility
                'remaining_usd': str(settings.monthly_limit_usd - usage.monthly_cost_usd),
                'percent_used': monthly_percent,
            },
            'warnings': warnings,
        }

    @transaction.atomic
    def update_usage(self, tokens: int, cost: Decimal, model: str = None) -> UsageTracking:
        """
        Atomically update usage tracking after successful API call.

        Uses SELECT FOR UPDATE to prevent race conditions when multiple
        requests update usage simultaneously.

        Args:
            tokens: Actual tokens used
            cost: Actual cost in USD
            model: Model used (for logging)

        Returns:
            Updated UsageTracking instance

        Raises:
            Exception: If atomic update fails
        """
        today = date.today()
        month_year = today.strftime('%Y-%m')

        # Lock the row for update to prevent race conditions
        usage, created = UsageTracking.objects.select_for_update().get_or_create(
            date=today,
            defaults={
                'month_year': month_year,
                'daily_summaries_count': 0,
                'daily_tokens_used': 0,
                'daily_cost_usd': Decimal('0'),
                'monthly_summaries_count': 0,
                'monthly_tokens_used': 0,
                'monthly_cost_usd': Decimal('0'),
            }
        )

        # Update daily counters
        usage.daily_summaries_count += 1
        usage.daily_tokens_used += tokens
        usage.daily_cost_usd += cost

        # Update monthly counters
        usage.monthly_summaries_count += 1
        usage.monthly_tokens_used += tokens
        usage.monthly_cost_usd += cost

        usage.save()

        logger.info(
            f"Usage updated: {tokens} tokens, ${cost:.6f} cost. "
            f"Daily: {usage.daily_summaries_count} summaries, ${usage.daily_cost_usd:.6f}. "
            f"Monthly: {usage.monthly_summaries_count} summaries, ${usage.monthly_cost_usd:.6f}"
        )

        return usage

    def get_current_usage(self) -> Dict[str, Any]:
        """
        Get current usage statistics with percentage of limits.

        Returns:
            Dictionary with daily and monthly usage stats:
            {
                'daily': {...},
                'monthly': {...},
                'settings': {...}
            }
        """
        settings = Settings.get_settings()
        today = date.today()
        month_year = today.strftime('%Y-%m')

        usage, _ = UsageTracking.objects.get_or_create(
            date=today,
            defaults={
                'month_year': month_year,
                'daily_summaries_count': 0,
                'daily_tokens_used': 0,
                'daily_cost_usd': Decimal('0'),
                'monthly_summaries_count': 0,
                'monthly_tokens_used': 0,
                'monthly_cost_usd': Decimal('0'),
            }
        )

        daily_percent = (usage.daily_summaries_count / settings.daily_summary_limit) * 100 if settings.daily_summary_limit > 0 else 0
        monthly_percent = (float(usage.monthly_cost_usd) / float(settings.monthly_limit_usd)) * 100 if settings.monthly_limit_usd > 0 else 0

        return {
            'daily': {
                'summaries_count': usage.daily_summaries_count,
                'tokens_used': usage.daily_tokens_used,
                'cost_usd': str(usage.daily_cost_usd),
                'limit': settings.daily_summary_limit,
                'remaining': settings.daily_summary_limit - usage.daily_summaries_count,
                'percent_used': round(daily_percent, 2),
            },
            'monthly': {
                'summaries_count': usage.monthly_summaries_count,
                'tokens_used': usage.monthly_tokens_used,
                'cost_usd': str(usage.monthly_cost_usd),
                'limit_usd': str(settings.monthly_limit_usd),
                'remaining_usd': str(settings.monthly_limit_usd - usage.monthly_cost_usd),
                'percent_used': round(monthly_percent, 2),
            },
            'settings': {
                'monthly_limit_usd': str(settings.monthly_limit_usd),
                'daily_summary_limit': settings.daily_summary_limit,
                'ai_features_enabled': settings.ai_features_enabled,
                'default_model': settings.default_model,
            }
        }
