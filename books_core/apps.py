import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class BooksCoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'books_core'

    def ready(self):
        """Auto-sync prompts from files on app startup."""
        # Skip during migrations, tests, or shell
        if self._should_skip_sync():
            return

        self._sync_prompts()

    def _should_skip_sync(self) -> bool:
        """Check if we should skip auto-sync."""
        # Skip if running migrations
        if 'migrate' in sys.argv or 'makemigrations' in sys.argv:
            return True

        # Skip if running tests
        if 'test' in sys.argv:
            return True

        # Skip if running shell or other non-server commands
        skip_commands = ['shell', 'dbshell', 'createsuperuser', 'collectstatic']
        if any(cmd in sys.argv for cmd in skip_commands):
            return True

        # Skip if RUN_MAIN is not set (avoid double-sync in runserver)
        # Django's autoreload runs the app twice - skip the first one
        if os.environ.get('RUN_MAIN') != 'true':
            # But allow sync for single-process servers (gunicorn, daphne)
            if 'runserver' in sys.argv:
                return True

        return False

    def _sync_prompts(self):
        """Perform the prompt sync."""
        try:
            from books_core.services.file_prompt_service import FilePromptService

            service = FilePromptService()
            if not service.get_prompts_directory().exists():
                logger.debug("Prompts directory not found, skipping auto-sync")
                return

            result = service.sync_all()
            if result['synced'] > 0:
                logger.info(
                    f"Auto-synced {result['synced']} prompts from files"
                )
            if result['failed']:
                logger.warning(
                    f"Failed to sync {len(result['failed'])} prompts: "
                    f"{', '.join(result['failed'])}"
                )
        except Exception as e:
            # Don't crash the app if sync fails
            logger.error(f"Prompt auto-sync failed: {e}")
