"""
Django management command to sync prompts from local files to database.

Usage:
    python manage.py sync_prompts           # Sync all prompts
    python manage.py sync_prompts --list    # List status without syncing
    python manage.py sync_prompts --force   # Force resync even if unchanged
    python manage.py sync_prompts --orphans # Show orphaned DB prompts
"""

from django.core.management.base import BaseCommand

from books_core.services.file_prompt_service import FilePromptService


class Command(BaseCommand):
    help = 'Sync prompt files from prompts/ directory to database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--list',
            action='store_true',
            help='List prompt files and their sync status without syncing',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force resync even if file checksum unchanged',
        )
        parser.add_argument(
            '--orphans',
            action='store_true',
            help='Show prompts in DB that have no corresponding file',
        )

    def handle(self, *args, **options):
        service = FilePromptService()

        # Ensure prompts directory exists
        if not service.ensure_prompts_directory():
            self.stderr.write(
                self.style.ERROR('Failed to create prompts directory')
            )
            return

        # List mode
        if options['list']:
            self.list_prompts(service)
            return

        # Orphans mode
        if options['orphans']:
            self.show_orphans(service)
            return

        # Sync mode (default)
        self.sync_prompts(service, force=options['force'])

    def list_prompts(self, service: FilePromptService):
        """List all prompt files with sync status."""
        prompts = service.list_prompts()

        if not prompts:
            self.stdout.write(
                self.style.WARNING('No prompt files found in prompts/ directory')
            )
            return

        self.stdout.write(self.style.MIGRATE_HEADING('\nPrompt Files Status:'))
        self.stdout.write('-' * 60)

        for p in prompts:
            status = p['status']
            if status == 'synced':
                style = self.style.SUCCESS
                icon = '[OK]'
            elif status == 'changed':
                style = self.style.WARNING
                icon = '[CHANGED]'
            elif status == 'new':
                style = self.style.NOTICE
                icon = '[NEW]'
            else:
                style = self.style.ERROR
                icon = '[ERROR]'

            name = p['name'] or p['file']
            self.stdout.write(style(f"  {icon} {name} ({p['file']})"))

        self.stdout.write('')

    def show_orphans(self, service: FilePromptService):
        """Show prompts in DB without corresponding files."""
        orphans = service.detect_orphans()

        if not orphans:
            self.stdout.write(
                self.style.SUCCESS('No orphaned prompts found')
            )
            return

        self.stdout.write(self.style.WARNING('\nOrphaned prompts (in DB but no file):'))
        for name in orphans:
            self.stdout.write(self.style.WARNING(f"  - {name}"))

        self.stdout.write('')

    def sync_prompts(self, service: FilePromptService, force: bool = False):
        """Sync all prompts from files to database."""
        self.stdout.write('Syncing prompts from prompts/ directory...\n')

        result = service.sync_all(force=force)

        # Report results
        if result['synced'] > 0:
            self.stdout.write(
                self.style.SUCCESS(f"  Synced: {result['synced']}")
            )

        if result['unchanged'] > 0:
            self.stdout.write(f"  Unchanged: {result['unchanged']}")

        if result['failed']:
            self.stdout.write(
                self.style.ERROR(f"  Failed: {len(result['failed'])}")
            )
            for name in result['failed']:
                error = result['errors'].get(name, 'Unknown error')
                self.stdout.write(self.style.ERROR(f"    - {name}: {error}"))

        # Summary
        total = result['synced'] + result['unchanged'] + len(result['failed'])
        self.stdout.write(f"\nTotal: {total} prompt files processed")
