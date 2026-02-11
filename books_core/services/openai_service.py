"""
OpenAIService - OpenAI API integration with cost control.

Handles communication with OpenAI API with:
- 30-second timeout enforcement
- No automatic retry (manual only per requirements)
- Cost control integration
- Clear error handling
"""

import os
import logging
from typing import Dict, Any, Optional
from decimal import Decimal

from openai import OpenAI, APIError, APIConnectionError, APITimeoutError, RateLimitError

from books_core.services.cost_control_service import CostControlService

logger = logging.getLogger(__name__)


class OpenAIService:
    """
    Service for interacting with OpenAI API with strict cost control.
    """

    # Model TPM (tokens per minute) limits per OpenAI documentation
    # These are per-request limits for the free/tier 1 plans
    MODEL_TPM_LIMITS = {
        'gpt-4o': 30000,
        'gpt-4o-mini': 200000,
        'gpt-4-turbo': 30000,
        'gpt-4': 30000,
        'gpt-3.5-turbo': 200000,
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = 'gpt-4o-mini',
        timeout: int = 30,
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ):
        """
        Initialize OpenAI service.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: Model to use for completions
            timeout: Request timeout in seconds (default: 30)
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens in response

        Raises:
            ValueError: If API key is not provided
        """
        self.api_key = api_key or os.getenv('OPENAI_API_KEY')
        if not self.api_key:
            raise ValueError(
                "OpenAI API key must be provided or set in OPENAI_API_KEY environment variable"
            )

        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Initialize OpenAI client with timeout
        self.client = OpenAI(api_key=self.api_key, timeout=self.timeout)

    def complete(
        self,
        prompt: str,
        model: str = None,
        system_message: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate a completion using OpenAI API.

        IMPORTANT: This method does NOT check cost controls or retry on failure.
        Use complete_with_cost_control() for cost-controlled operations.

        Args:
            prompt: User prompt
            model: Model to use (defaults to instance model)
            system_message: Optional system message
            **kwargs: Additional parameters to override defaults

        Returns:
            Dict containing:
                - content: Response text
                - tokens_used: Total tokens
                - model: Model used
                - finish_reason: Why generation stopped
                - prompt_tokens: Input tokens
                - completion_tokens: Output tokens

        Raises:
            APIError: If API call fails (no automatic retry)
            APITimeoutError: If request times out after 30 seconds
            APIConnectionError: If connection fails
            RateLimitError: If rate limit is hit
        """
        # Build messages
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        # Merge kwargs with defaults
        params = {
            "model": model or self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        params.update(kwargs)

        logger.info(f"Making OpenAI API call with model {params['model']}")

        try:
            # Make API call with 30-second timeout
            response = self.client.chat.completions.create(
                messages=messages,
                **params
            )

            result = {
                'content': response.choices[0].message.content,
                'model': response.model,
                'tokens_used': response.usage.total_tokens,
                'prompt_tokens': response.usage.prompt_tokens,
                'completion_tokens': response.usage.completion_tokens,
                'finish_reason': response.choices[0].finish_reason,
            }

            logger.info(
                f"API call successful: {result['tokens_used']} tokens used "
                f"({result['prompt_tokens']} input, {result['completion_tokens']} output)"
            )

            return result

        except APITimeoutError as e:
            logger.error(f"OpenAI API timeout after {self.timeout} seconds")
            # Re-raise the original exception instead of wrapping it
            raise

        except RateLimitError as e:
            logger.error(f"OpenAI rate limit exceeded: {str(e)}")
            # Re-raise the original exception instead of wrapping it
            raise

        except APIConnectionError as e:
            logger.error(f"OpenAI connection error: {str(e)}")
            # Re-raise the original exception instead of wrapping it
            raise

        except APIError as e:
            logger.error(f"OpenAI API error: {str(e)}")
            raise

    def complete_with_cost_control(
        self,
        prompt: str,
        model: str = None,
        system_message: Optional[str] = None,
        cost_service: Optional[CostControlService] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate completion with cost control checks and usage tracking.

        This method:
        1. Checks limits BEFORE making API call
        2. Makes the API call if limits allow
        3. Updates usage tracking AFTER successful call
        4. Rolls back usage if API call fails

        Args:
            prompt: User prompt
            model: Model to use (defaults to instance model)
            system_message: Optional system message
            cost_service: CostControlService instance (creates one if not provided)
            **kwargs: Additional parameters to override defaults

        Returns:
            Dict with response data plus cost information:
                - content: Response text
                - tokens_used: Total tokens
                - model: Model used
                - finish_reason: Why generation stopped
                - prompt_tokens: Input tokens
                - completion_tokens: Output tokens
                - actual_cost_usd: Actual cost of operation
                - cost_breakdown: Detailed cost information

        Raises:
            LimitExceededException: If operation would exceed limits
            EmergencyStopException: If AI features are disabled
            APIError: If API call fails (usage not tracked on failure)
        """
        model_name = model or self.model

        # Initialize cost service if not provided
        if cost_service is None:
            cost_service = CostControlService(model=model_name)

        # Build messages for token counting
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        # Count input tokens
        full_prompt = (system_message or '') + '\n' + prompt
        input_tokens = cost_service.count_tokens(full_prompt, model_name)

        # Get max output tokens from kwargs or use default
        max_output_tokens = kwargs.get('max_tokens', self.max_tokens)

        # Estimate cost BEFORE making API call
        estimate = cost_service.estimate_cost(
            input_tokens=input_tokens,
            output_tokens=max_output_tokens,
            model=model_name
        )

        logger.info(
            f"Cost estimate: {estimate['total_tokens']} tokens, "
            f"${estimate['estimated_cost_usd']:.6f}"
        )

        # Check model TPM limits BEFORE making request
        model_tpm_limit = self.MODEL_TPM_LIMITS.get(model_name, None)
        total_tokens_for_request = input_tokens + max_output_tokens

        if model_tpm_limit and total_tokens_for_request > model_tpm_limit:
            error_msg = (
                f"Request exceeds {model_name} token limit. "
                f"Limit: {model_tpm_limit:,} TPM, "
                f"Requested: {total_tokens_for_request:,} tokens "
                f"({input_tokens:,} input + {max_output_tokens:,} output). "
                f"Please use a different model (e.g., gpt-4o-mini) or reduce content size."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Check cost limits (raises exception if limits exceeded)
        limit_check = cost_service.check_limits(estimate['estimated_cost_usd'])

        # Log any warnings
        for warning in limit_check.get('warnings', []):
            logger.warning(warning)

        # Make API call (only if limits check passed)
        # Note: If this fails, usage is NOT tracked (correct behavior)
        result = self.complete(prompt, model_name, system_message, **kwargs)

        # Calculate actual cost based on actual tokens used
        actual_cost_estimate = cost_service.estimate_cost(
            input_tokens=result['prompt_tokens'],
            output_tokens=result['completion_tokens'],
            model=model_name
        )

        # Update usage tracking atomically
        cost_service.update_usage(
            tokens=result['tokens_used'],
            cost=actual_cost_estimate['estimated_cost_usd'],
            model=model_name
        )

        # Add cost information to result
        result['actual_cost_usd'] = str(actual_cost_estimate['estimated_cost_usd'])
        result['cost_breakdown'] = {
            'input_tokens': actual_cost_estimate['input_tokens'],
            'output_tokens': actual_cost_estimate['output_tokens'],
            'total_tokens': actual_cost_estimate['total_tokens'],
            'input_cost_usd': str(actual_cost_estimate['input_cost_usd']),
            'output_cost_usd': str(actual_cost_estimate['output_cost_usd']),
            'total_cost_usd': str(actual_cost_estimate['estimated_cost_usd']),
            'model': model_name,
        }

        logger.info(
            f"Cost-controlled completion successful. "
            f"Actual cost: ${actual_cost_estimate['estimated_cost_usd']:.6f}"
        )

        return result
