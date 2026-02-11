"""
Django management command to clear all analysis data for a fresh start.

Usage:
    python manage.py clear_analysis_data           # Preview what will be deleted
    python manage.py clear_analysis_data --confirm # Actually delete the data
"""

from django.core.management.base import BaseCommand

from books_core.models import Summary, ProcessingJob, UsageTracking, Chapter


class Command(BaseCommand):
    help = 'Clear all Summary, ProcessingJob, and UsageTracking data while preserving Books, Chapters, and Prompts'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Actually delete the data (without this flag, only shows preview)',
        )

    def handle(self, *args, **options):
        # Count existing data
        summary_count = Summary.objects.count()
        job_count = ProcessingJob.objects.count()
        usage_count = UsageTracking.objects.count()
        chapters_with_summary = Chapter.objects.filter(has_summary=True).count()

        self.stdout.write(self.style.MIGRATE_HEADING('\nAnalysis Data Summary:'))
        self.stdout.write('-' * 40)
        self.stdout.write(f"  Summaries: {summary_count}")
        self.stdout.write(f"  Processing Jobs: {job_count}")
        self.stdout.write(f"  Usage Tracking Records: {usage_count}")
        self.stdout.write(f"  Chapters with has_summary=True: {chapters_with_summary}")
        self.stdout.write('')

        if not options['confirm']:
            self.stdout.write(
                self.style.WARNING('This is a preview. Add --confirm to actually delete.')
            )
            return

        # Delete in order (no FK dependencies between these tables)
        self.stdout.write('Deleting data...\n')

        deleted_summaries, _ = Summary.objects.all().delete()
        self.stdout.write(f"  Deleted {deleted_summaries} summaries")

        deleted_jobs, _ = ProcessingJob.objects.all().delete()
        self.stdout.write(f"  Deleted {deleted_jobs} processing jobs")

        deleted_usage, _ = UsageTracking.objects.all().delete()
        self.stdout.write(f"  Deleted {deleted_usage} usage tracking records")

        # Reset chapter flags
        updated_chapters = Chapter.objects.filter(has_summary=True).update(has_summary=False)
        self.stdout.write(f"  Reset has_summary flag on {updated_chapters} chapters")

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Data cleanup complete. Ready for fresh analysis.'))
