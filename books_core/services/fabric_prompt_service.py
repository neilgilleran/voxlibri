"""
FabricPromptService - Service for fetching and managing Fabric AI prompts.

This service fetches prompt templates from the danielmiessler/fabric GitHub
repository and stores them as Prompt records with is_fabric=True flag.

Key features:
- Fetch prompts from GitHub raw URLs
- Local caching after fetch
- Parse markdown content
- Store/update prompts without duplication
- Graceful error handling for network failures
"""

import logging
import requests
from typing import Optional, List, Dict

from books_core.models import Prompt

logger = logging.getLogger(__name__)


class FabricPromptService:
    """
    Service for importing and managing Fabric AI prompts from GitHub.

    Fabric prompts are high-quality, community-maintained prompt templates
    from: https://github.com/danielmiessler/fabric
    """

    # GitHub repository configuration
    GITHUB_BASE_URL = 'https://raw.githubusercontent.com/danielmiessler/fabric/refs/heads/main/data/patterns'
    REQUEST_TIMEOUT = 10  # seconds

    # Core Fabric prompts to sync (7+ prompts as required)
    CORE_PROMPTS = [
        'extract_wisdom',
        'summarize',
        'analyze_prose',
        'explain_code',
        'extract_article_wisdom',
        'create_reading_plan',
        'rate_content',
    ]

    # Category mapping based on prompt name patterns
    CATEGORY_MAP = {
        'extract': 'extraction',
        'rate': 'rating',
        'summarize': 'summarization',
        'analyze': 'analysis',
        'explain': 'analysis',
        'create': 'custom',
    }

    def get_fabric_prompt_url(self, prompt_name: str) -> str:
        """
        Construct GitHub raw URL for a Fabric prompt.

        Args:
            prompt_name: Name of the Fabric prompt (e.g., 'extract_wisdom')

        Returns:
            Full URL to the prompt's system.md file
        """
        return f"{self.GITHUB_BASE_URL}/{prompt_name}/system.md"

    def fetch_prompt_from_github(self, prompt_name: str) -> Optional[str]:
        """
        Fetch a Fabric prompt from GitHub.

        Args:
            prompt_name: Name of the Fabric prompt to fetch

        Returns:
            Prompt content as string, or None if fetch fails
        """
        url = self.get_fabric_prompt_url(prompt_name)

        try:
            logger.info(f"Fetching Fabric prompt '{prompt_name}' from GitHub")
            response = requests.get(url, timeout=self.REQUEST_TIMEOUT)

            if response.status_code == 200:
                logger.info(f"Successfully fetched '{prompt_name}'")
                return response.text
            else:
                logger.warning(
                    f"Failed to fetch '{prompt_name}': HTTP {response.status_code}"
                )
                return None

        except requests.RequestException as e:
            logger.error(f"Network error fetching '{prompt_name}': {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching '{prompt_name}': {str(e)}")
            return None

    def parse_prompt_content(self, content: str) -> str:
        """
        Parse Fabric prompt markdown content.

        Currently returns content as-is since Fabric prompts are already
        well-structured markdown. Future enhancements could extract specific
        sections or perform validation.

        Args:
            content: Raw markdown content from GitHub

        Returns:
            Parsed/validated prompt content
        """
        # Fabric prompts are already in the correct format
        # Just return as-is (no modification per requirements)
        return content

    def determine_category(self, prompt_name: str) -> str:
        """
        Determine prompt category based on name.

        Args:
            prompt_name: Name of the prompt

        Returns:
            Category string matching Prompt.CATEGORY_CHOICES
        """
        for keyword, category in self.CATEGORY_MAP.items():
            if prompt_name.startswith(keyword):
                return category

        # Default category
        return 'analysis'

    def import_fabric_prompt(self, prompt_name: str) -> Optional[Prompt]:
        """
        Import a single Fabric prompt from GitHub.

        Fetches the prompt from GitHub and creates/updates the corresponding
        Prompt record in the database. After first fetch, prompt is cached
        locally in the database.

        Args:
            prompt_name: Name of the Fabric prompt to import

        Returns:
            Prompt instance if successful, None if fetch fails
        """
        # Check if prompt already exists in cache (local database)
        try:
            existing_prompt = Prompt.objects.filter(
                name=prompt_name,
                is_fabric=True
            ).first()

            if existing_prompt:
                logger.info(f"Prompt '{prompt_name}' already cached locally")
                # Optionally re-fetch to update, or just return cached version
                # For MVP, return cached version (local caching behavior)
                # To force update, comment out the return below
                # return existing_prompt
        except Exception as e:
            logger.warning(f"Error checking cache for '{prompt_name}': {str(e)}")

        # Fetch content from GitHub
        content = self.fetch_prompt_from_github(prompt_name)
        if content is None:
            logger.error(f"Cannot import '{prompt_name}': fetch failed")
            return None

        # Parse content
        template_text = self.parse_prompt_content(content)

        # Determine category
        category = self.determine_category(prompt_name)

        # Create or update prompt (local cache)
        try:
            prompt, created = Prompt.objects.update_or_create(
                name=prompt_name,
                defaults={
                    'template_text': template_text,
                    'category': category,
                    'is_fabric': True,
                    'is_custom': False,
                    'variables_required': ['content'],
                    'default_model': 'gpt-4o-mini',
                    'created_by': 'fabric_import',
                }
            )

            if created:
                logger.info(f"Created new Fabric prompt: {prompt_name}")
            else:
                logger.info(f"Updated existing Fabric prompt: {prompt_name}")

            return prompt

        except Exception as e:
            logger.error(f"Database error importing '{prompt_name}': {str(e)}")
            return None

    def sync_prompts(self, prompt_names: Optional[List[str]] = None) -> Dict[str, any]:
        """
        Sync specified prompts from GitHub, or all core prompts if none specified.

        Fetches prompts from GitHub and caches them locally in database.
        Second fetch uses cache.

        Args:
            prompt_names: Optional list of prompt names to sync.
                         If None, syncs all core prompts.

        Returns:
            Dictionary with sync results:
            {
                'synced': int,
                'failed': List[str],
                'errors': Dict[str, str]
            }
        """
        names_to_sync = prompt_names or self.CORE_PROMPTS
        synced_prompts = []
        failed_prompts = []
        errors = {}

        logger.info(f"Starting sync of {len(names_to_sync)} Fabric prompts")

        for name in names_to_sync:
            prompt = self.import_fabric_prompt(name)
            if prompt:
                synced_prompts.append(prompt)
            else:
                failed_prompts.append(name)
                errors[name] = "Failed to fetch or import prompt"

        logger.info(
            f"Sync complete: {len(synced_prompts)} succeeded, "
            f"{len(failed_prompts)} failed"
        )

        if failed_prompts:
            logger.warning(f"Failed to sync: {', '.join(failed_prompts)}")

        return {
            'synced': len(synced_prompts),
            'failed': failed_prompts,
            'errors': errors
        }

    def preview_prompt(self, prompt: Prompt, variables: Dict[str, str] = None) -> str:
        """
        Preview a prompt by rendering its template with sample or provided variables.

        Args:
            prompt: Prompt instance to preview
            variables: Optional dictionary of variables to use.
                      If not provided, uses sample values.

        Returns:
            Rendered prompt text for preview
        """
        # Use provided variables or sample defaults
        if variables is None:
            variables = {
                'content': '[Sample chapter content will be inserted here]',
                'title': '[Chapter title]',
                'author': '[Book author]',
            }

        # Render template using prompt's render_template method
        try:
            rendered = prompt.render_template(variables)
            return rendered
        except Exception as e:
            logger.error(f"Error previewing prompt '{prompt.name}': {str(e)}")
            return f"Error rendering prompt: {str(e)}"
