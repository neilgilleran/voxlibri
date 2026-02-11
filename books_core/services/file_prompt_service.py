"""
FilePromptService - Service for syncing prompts from local markdown files.

This service reads prompt templates from the prompts/ directory in the repo
and syncs them to the database, enabling version control and easy editing.

Key features:
- Parse markdown files with YAML frontmatter
- MD5 checksum-based change detection
- Sync on app startup and via management command
- Preserve Summary FK relationships
"""

import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

import yaml
from django.conf import settings
from django.utils import timezone

from books_core.models import Prompt

logger = logging.getLogger(__name__)


class FilePromptService:
    """
    Service for syncing prompts from local markdown files to the database.

    Prompt files are stored in the prompts/ directory with YAML frontmatter
    for metadata and markdown content for the prompt template.
    """

    def __init__(self):
        self.prompts_dir = Path(settings.BASE_DIR) / 'prompts'

    # Category mapping based on prompt name patterns
    CATEGORY_MAP = {
        'extract': 'extraction',
        'rate': 'rating',
        'summarize': 'summarization',
        'create': 'summarization',
        'analyze': 'analysis',
    }

    def get_prompts_directory(self) -> Path:
        """Get the prompts directory path."""
        return self.prompts_dir

    def ensure_prompts_directory(self) -> bool:
        """
        Ensure the prompts directory exists.

        Returns:
            True if directory exists or was created, False on error
        """
        try:
            self.prompts_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            logger.error(f"Failed to create prompts directory: {e}")
            return False

    def calculate_checksum(self, content: str) -> str:
        """
        Calculate MD5 checksum of content for change detection.

        Args:
            content: File content as string

        Returns:
            MD5 hex digest
        """
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def parse_frontmatter(self, content: str) -> tuple[dict, str]:
        """
        Parse YAML frontmatter and body from markdown content.

        Expected format:
        ---
        name: prompt_name
        category: extraction
        default_model: gpt-4o-mini
        variables: [content]
        ---

        # Prompt content here...

        Args:
            content: Full file content

        Returns:
            Tuple of (frontmatter_dict, body_content)
        """
        # Match YAML frontmatter between --- markers
        frontmatter_pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
        match = re.match(frontmatter_pattern, content, re.DOTALL)

        if match:
            try:
                frontmatter = yaml.safe_load(match.group(1))
                body = match.group(2).strip()
                return frontmatter or {}, body
            except yaml.YAMLError as e:
                logger.warning(f"Failed to parse YAML frontmatter: {e}")
                return {}, content

        # No frontmatter found, return empty dict and full content
        return {}, content

    def determine_category(self, prompt_name: str) -> str:
        """
        Determine prompt category based on name prefix.

        Args:
            prompt_name: Name of the prompt

        Returns:
            Category string matching Prompt.CATEGORY_CHOICES
        """
        for keyword, category in self.CATEGORY_MAP.items():
            if prompt_name.startswith(keyword):
                return category
        return 'custom'

    def parse_prompt_file(self, filepath: Path) -> Optional[dict]:
        """
        Parse a prompt file and extract metadata and content.

        Args:
            filepath: Path to the .md file

        Returns:
            Dictionary with prompt data, or None on error
        """
        try:
            content = filepath.read_text(encoding='utf-8')
            frontmatter, body = self.parse_frontmatter(content)

            # Use filename (without extension) as default name
            default_name = filepath.stem

            # Extract metadata from frontmatter with defaults
            name = frontmatter.get('name', default_name)
            category = frontmatter.get('category', self.determine_category(name))
            default_model = frontmatter.get('default_model', 'gpt-4o-mini')
            variables = frontmatter.get('variables', ['content'])

            return {
                'name': name,
                'template_text': body,
                'category': category,
                'default_model': default_model,
                'variables_required': variables,
                'file_path': filepath.name,
                'file_checksum': self.calculate_checksum(content),
            }

        except Exception as e:
            logger.error(f"Failed to parse prompt file {filepath}: {e}")
            return None

    def sync_single(self, filepath: Path) -> Optional[Prompt]:
        """
        Sync a single prompt file to the database.

        Args:
            filepath: Path to the .md file

        Returns:
            Prompt instance if successful, None on error
        """
        prompt_data = self.parse_prompt_file(filepath)
        if not prompt_data:
            return None

        name = prompt_data['name']
        checksum = prompt_data['file_checksum']

        try:
            # Check if prompt exists and if it changed
            existing = Prompt.objects.filter(name=name).first()

            if existing and existing.file_checksum == checksum:
                logger.debug(f"Prompt '{name}' unchanged, skipping")
                return existing

            # Create or update prompt
            prompt, created = Prompt.objects.update_or_create(
                name=name,
                defaults={
                    'template_text': prompt_data['template_text'],
                    'category': prompt_data['category'],
                    'default_model': prompt_data['default_model'],
                    'variables_required': prompt_data['variables_required'],
                    'file_path': prompt_data['file_path'],
                    'file_checksum': checksum,
                    'last_synced_at': timezone.now(),
                    'is_fabric': False,
                    'is_custom': False,
                    'created_by': 'file_sync',
                }
            )

            action = "Created" if created else "Updated"
            logger.info(f"{action} prompt '{name}' from {filepath.name}")
            return prompt

        except Exception as e:
            logger.error(f"Database error syncing '{name}': {e}")
            return None

    def sync_all(self, force: bool = False) -> dict:
        """
        Sync all prompt files from the prompts directory to the database.

        Args:
            force: If True, resync even if checksum unchanged

        Returns:
            Dictionary with sync results:
            {
                'synced': int,
                'unchanged': int,
                'failed': List[str],
                'errors': Dict[str, str]
            }
        """
        if not self.prompts_dir.exists():
            logger.warning(f"Prompts directory not found: {self.prompts_dir}")
            return {
                'synced': 0,
                'unchanged': 0,
                'failed': [],
                'errors': {'_directory': 'Prompts directory not found'}
            }

        synced = 0
        unchanged = 0
        failed = []
        errors = {}

        prompt_files = list(self.prompts_dir.glob('*.md'))
        logger.info(f"Found {len(prompt_files)} prompt files to sync")

        for filepath in prompt_files:
            try:
                prompt_data = self.parse_prompt_file(filepath)
                if not prompt_data:
                    failed.append(filepath.name)
                    errors[filepath.name] = "Failed to parse file"
                    continue

                name = prompt_data['name']
                checksum = prompt_data['file_checksum']

                # Check if unchanged (unless force)
                if not force:
                    existing = Prompt.objects.filter(name=name).first()
                    if existing and existing.file_checksum == checksum:
                        unchanged += 1
                        continue

                # Sync the prompt
                prompt = self.sync_single(filepath)
                if prompt:
                    synced += 1
                else:
                    failed.append(filepath.name)
                    errors[filepath.name] = "Database sync failed"

            except Exception as e:
                failed.append(filepath.name)
                errors[filepath.name] = str(e)

        logger.info(
            f"Sync complete: {synced} synced, {unchanged} unchanged, "
            f"{len(failed)} failed"
        )

        return {
            'synced': synced,
            'unchanged': unchanged,
            'failed': failed,
            'errors': errors
        }

    def list_prompts(self) -> List[dict]:
        """
        List all prompt files with their sync status.

        Returns:
            List of dicts with file info and sync status
        """
        if not self.prompts_dir.exists():
            return []

        results = []
        for filepath in self.prompts_dir.glob('*.md'):
            prompt_data = self.parse_prompt_file(filepath)
            if not prompt_data:
                results.append({
                    'file': filepath.name,
                    'name': None,
                    'status': 'error',
                    'error': 'Failed to parse'
                })
                continue

            name = prompt_data['name']
            checksum = prompt_data['file_checksum']

            # Check DB status
            existing = Prompt.objects.filter(name=name).first()
            if not existing:
                status = 'new'
            elif existing.file_checksum == checksum:
                status = 'synced'
            else:
                status = 'changed'

            results.append({
                'file': filepath.name,
                'name': name,
                'category': prompt_data['category'],
                'status': status,
                'last_synced': existing.last_synced_at if existing else None
            })

        return results

    def detect_orphans(self) -> List[str]:
        """
        Find prompts in DB that don't have corresponding files.

        Returns:
            List of prompt names that are orphaned
        """
        if not self.prompts_dir.exists():
            return []

        # Get all file-based prompt names
        file_names = set()
        for filepath in self.prompts_dir.glob('*.md'):
            prompt_data = self.parse_prompt_file(filepath)
            if prompt_data:
                file_names.add(prompt_data['name'])

        # Find DB prompts with file_path set but no matching file
        orphans = []
        for prompt in Prompt.objects.exclude(file_path__isnull=True).exclude(file_path=''):
            if prompt.name not in file_names:
                orphans.append(prompt.name)

        return orphans
