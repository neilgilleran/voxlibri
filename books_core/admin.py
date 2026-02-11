from django.contrib import admin
from .models import Book, Chapter, Prompt, Summary, UsageTracking, ProcessingJob, Settings


class ChapterInline(admin.TabularInline):
    """Inline display of chapters within the Book admin."""
    model = Chapter
    extra = 0
    fields = ['chapter_number', 'title', 'word_count', 'is_front_matter', 'is_back_matter', 'has_summary']
    readonly_fields = ['word_count', 'has_summary']


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    """Admin configuration for Book model."""
    list_display = ['title', 'author', 'status', 'word_count', 'uploaded_at']
    list_filter = ['status', 'uploaded_at']
    search_fields = ['title', 'author', 'isbn']
    readonly_fields = ['uploaded_at', 'processed_at', 'created_at', 'updated_at']
    inlines = [ChapterInline]

    fieldsets = (
        ('Book Information', {
            'fields': ('title', 'author', 'isbn', 'language')
        }),
        ('Files', {
            'fields': ('epub_file', 'cover_image', 'cover_thumbnail')
        }),
        ('Processing', {
            'fields': ('status', 'error_message', 'word_count', 'processed_at')
        }),
        ('Timestamps', {
            'fields': ('uploaded_at', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
    """Admin configuration for Chapter model."""
    list_display = ['book', 'chapter_number', 'title', 'word_count', 'has_summary', 'is_front_matter', 'is_back_matter']
    list_filter = ['has_summary', 'is_front_matter', 'is_back_matter', 'book']
    search_fields = ['title', 'book__title']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Prompt)
class PromptAdmin(admin.ModelAdmin):
    """Admin configuration for Prompt model."""
    list_display = ['name', 'category', 'is_fabric', 'is_custom', 'version', 'created_at']
    list_filter = ['category', 'is_fabric', 'is_custom']
    search_fields = ['name', 'template_text']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Prompt Information', {
            'fields': ('name', 'template_text', 'category')
        }),
        ('Configuration', {
            'fields': ('is_fabric', 'is_custom', 'variables_required', 'default_model')
        }),
        ('Versioning', {
            'fields': ('version', 'created_by')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Summary)
class SummaryAdmin(admin.ModelAdmin):
    """Admin configuration for Summary model (read-only)."""
    list_display = ['chapter', 'prompt', 'version', 'summary_type', 'tokens_used', 'estimated_cost_usd', 'created_at']
    list_filter = ['summary_type', 'model_used', 'created_at']
    search_fields = ['chapter__title', 'chapter__book__title', 'prompt__name']
    readonly_fields = [
        'chapter', 'prompt', 'summary_type', 'content_json', 'tokens_used',
        'model_used', 'processing_time_ms', 'version', 'previous_version',
        'estimated_cost_usd', 'created_at'
    ]

    fieldsets = (
        ('Summary Information', {
            'fields': ('chapter', 'prompt', 'summary_type', 'content_json')
        }),
        ('Versioning', {
            'fields': ('version', 'previous_version')
        }),
        ('AI Metadata', {
            'fields': ('model_used', 'tokens_used', 'processing_time_ms', 'estimated_cost_usd')
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )

    def has_add_permission(self, request):
        """Disable add - summaries created via API only."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Disable delete - summaries are historical records."""
        return False


@admin.register(UsageTracking)
class UsageTrackingAdmin(admin.ModelAdmin):
    """Admin configuration for UsageTracking model (read-only)."""
    list_display = [
        'date', 'month_year', 'daily_summaries_count', 'daily_cost_usd',
        'monthly_summaries_count', 'monthly_cost_usd'
    ]
    list_filter = ['month_year', 'date']
    readonly_fields = [
        'date', 'month_year', 'daily_summaries_count', 'daily_tokens_used',
        'daily_cost_usd', 'monthly_summaries_count', 'monthly_tokens_used',
        'monthly_cost_usd', 'created_at', 'updated_at'
    ]

    fieldsets = (
        ('Date Information', {
            'fields': ('date', 'month_year')
        }),
        ('Daily Usage', {
            'fields': ('daily_summaries_count', 'daily_tokens_used', 'daily_cost_usd')
        }),
        ('Monthly Usage', {
            'fields': ('monthly_summaries_count', 'monthly_tokens_used', 'monthly_cost_usd')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def has_add_permission(self, request):
        """Disable add - usage tracking is auto-managed."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Disable delete - usage tracking is historical data."""
        return False


@admin.register(ProcessingJob)
class ProcessingJobAdmin(admin.ModelAdmin):
    """Admin configuration for ProcessingJob model (read-only)."""
    list_display = ['book', 'job_type', 'status', 'progress_percent', 'started_at', 'completed_at']
    list_filter = ['status', 'job_type', 'created_at']
    search_fields = ['book__title']
    readonly_fields = [
        'book', 'job_type', 'status', 'progress_percent', 'error_message',
        'metadata', 'started_at', 'completed_at', 'created_at', 'updated_at'
    ]

    fieldsets = (
        ('Job Information', {
            'fields': ('book', 'job_type', 'status')
        }),
        ('Progress', {
            'fields': ('progress_percent', 'error_message', 'metadata')
        }),
        ('Timestamps', {
            'fields': ('started_at', 'completed_at', 'created_at', 'updated_at')
        }),
    )

    def has_add_permission(self, request):
        """Disable add - jobs created via API only."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Allow delete for cleanup of old jobs."""
        return True


@admin.register(Settings)
class SettingsAdmin(admin.ModelAdmin):
    """Admin configuration for Settings model (singleton)."""
    list_display = ['monthly_limit_usd', 'daily_summary_limit', 'ai_features_enabled', 'default_model']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Cost Limits', {
            'fields': ('monthly_limit_usd', 'daily_summary_limit')
        }),
        ('AI Configuration', {
            'fields': ('ai_features_enabled', 'default_model')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def has_add_permission(self, request):
        """Only allow one Settings instance."""
        return not Settings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of the singleton Settings instance."""
        return False
