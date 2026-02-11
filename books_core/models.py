from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal


class Book(models.Model):
    """
    Model representing a book uploaded to the system.
    Stores metadata, files, and processing status.
    """
    STATUS_CHOICES = [
        ('uploaded', 'Uploaded'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    FILE_TYPE_CHOICES = [
        ('epub', 'EPUB'),
        ('pdf', 'PDF'),
    ]

    BOOK_TYPE_CHOICES = [
        ('nonfiction', 'Non-Fiction'),
        ('fiction', 'Fiction'),
    ]

    # Core metadata
    title = models.CharField(max_length=500)
    author = models.CharField(max_length=500)
    isbn = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    language = models.CharField(max_length=10, blank=True, null=True)

    # Files
    source_file = models.FileField(upload_to='books/source/%Y/%m/%d/')
    file_type = models.CharField(
        max_length=10,
        choices=FILE_TYPE_CHOICES,
        default='epub',
        db_index=True
    )
    book_type = models.CharField(
        max_length=20,
        choices=BOOK_TYPE_CHOICES,
        default='nonfiction',
        db_index=True,
        help_text='Fiction or non-fiction classification'
    )
    cover_image = models.ImageField(upload_to='books/covers/', blank=True, null=True)
    cover_thumbnail = models.ImageField(upload_to='books/covers/thumbs/', blank=True, null=True)

    # Calculated fields
    word_count = models.IntegerField(default=0)

    # Processing status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='uploaded',
        db_index=True
    )
    error_message = models.TextField(blank=True, null=True)

    # Timestamps
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['isbn']),
            models.Index(fields=['file_type']),
            models.Index(fields=['book_type']),
        ]

    def __str__(self):
        return self.title


class Chapter(models.Model):
    """
    Model representing a chapter within a book.
    Stores chapter content in markdown format.
    """
    # Relationships
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='chapters')

    # Chapter metadata
    chapter_number = models.IntegerField()
    title = models.CharField(max_length=500, blank=True, null=True)

    # Content
    content = models.TextField()  # Markdown format
    word_count = models.IntegerField(default=0)

    # Classification
    is_front_matter = models.BooleanField(default=False)
    is_back_matter = models.BooleanField(default=False)

    # AI summary tracking
    has_summary = models.BooleanField(default=False)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['book', 'chapter_number']
        unique_together = [['book', 'chapter_number']]
        indexes = [
            models.Index(fields=['book', 'chapter_number']),
        ]

    def __str__(self):
        if self.title:
            return f"Chapter {self.chapter_number}: {self.title}"
        return f"Chapter {self.chapter_number}: Untitled"

    @property
    def summary_count(self):
        """Count of summaries for this chapter."""
        return self.summaries.count()


class Prompt(models.Model):
    """
    Represents a prompt template for AI-powered content analysis.
    Supports both Fabric prompts from GitHub and custom user prompts.
    """
    CATEGORY_CHOICES = [
        ('summarization', 'Summarization'),
        ('extraction', 'Extraction'),
        ('analysis', 'Analysis'),
        ('rating', 'Rating'),
        ('custom', 'Custom'),
    ]

    name = models.CharField(max_length=200, unique=True)
    template_text = models.TextField(
        help_text='Template with variables like {content}, {title}, {author}'
    )
    category = models.CharField(
        max_length=50,
        choices=CATEGORY_CHOICES,
        default='custom'
    )
    is_custom = models.BooleanField(
        default=False,
        help_text='True if created by user, False if built-in'
    )
    is_fabric = models.BooleanField(
        default=False,
        help_text='True if this is a Fabric prompt from GitHub'
    )

    # Configuration
    variables_required = models.JSONField(
        default=list,
        help_text='List of required variables, e.g., ["content", "title"]'
    )
    default_model = models.CharField(
        max_length=100,
        default='gpt-4o-mini',
        help_text='Default OpenAI model to use'
    )

    # Versioning
    version = models.CharField(
        max_length=20,
        default='1.0',
        help_text='Prompt version for tracking changes'
    )
    created_by = models.CharField(
        max_length=100,
        blank=True,
        help_text='User or system that created this prompt'
    )

    # File sync fields
    file_path = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        help_text='Path to prompt file relative to prompts/ directory'
    )
    file_checksum = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text='MD5 checksum of file content for change detection'
    )
    last_synced_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text='When this prompt was last synced from file'
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'name']
        indexes = [
            models.Index(fields=['category']),
            models.Index(fields=['is_fabric']),
            models.Index(fields=['is_custom']),
            models.Index(fields=['file_path']),
        ]

    def __str__(self):
        return f"{self.name} (v{self.version})"

    def render_template(self, variables):
        """
        Render the prompt template with provided variables.

        Args:
            variables: Dictionary of variables to substitute in template

        Returns:
            Rendered prompt string
        """
        template = self.template_text

        # Check if content is in variables but not used as placeholder
        content_value = None
        if 'content' in variables and '{content}' not in template:
            # Store content to append later
            content_value = variables.get('content')

        # Replace all placeholders
        for key, value in variables.items():
            placeholder = f"{{{key}}}"
            template = template.replace(placeholder, str(value))

        # If content wasn't used as a placeholder, append it at the end
        if content_value is not None:
            template = template + '\n\n' + str(content_value)

        return template


class Summary(models.Model):
    """
    Stores AI-generated summaries for chapters or entire books.
    Supports version history for re-running prompts with unlimited versions.

    For book-level summaries: book FK is set, chapter is null
    For chapter-level summaries: chapter FK is set, book is null
    """
    SUMMARY_TYPE_CHOICES = [
        ('tldr', 'TLDR'),
        ('key_points', 'Key Points'),
        ('analysis', 'Analysis'),
        ('custom', 'Custom'),
    ]

    # Relationships - Either chapter OR book must be set, not both
    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.CASCADE,
        related_name='summaries',
        null=True,
        blank=True
    )
    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name='summaries',
        null=True,
        blank=True,
        db_index=True
    )
    prompt = models.ForeignKey(
        Prompt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='summaries'
    )

    summary_type = models.CharField(
        max_length=50,
        choices=SUMMARY_TYPE_CHOICES,
        default='tldr'
    )
    content_json = models.JSONField(
        default=dict,
        help_text='Summary content and metadata in JSON format'
    )

    # AI metadata
    tokens_used = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)]
    )
    model_used = models.CharField(
        max_length=100,
        help_text='OpenAI model used for generation'
    )
    processing_time_ms = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text='Processing time in milliseconds'
    )

    # Versioning support for unlimited version history
    version = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text='Version number for tracking re-runs of same prompt'
    )
    previous_version = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='next_versions',
        help_text='Link to previous version of this summary'
    )

    # Cost tracking
    estimated_cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal('0.000000'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text='Estimated cost in USD for generating this summary'
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'summaries'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['chapter']),
            models.Index(fields=['book']),
            models.Index(fields=['prompt']),
            models.Index(fields=['summary_type']),
            models.Index(fields=['-created_at']),
            models.Index(fields=['chapter', 'prompt', 'version']),
            models.Index(fields=['book', 'prompt', 'version']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['chapter', 'prompt', 'version'],
                name='unique_chapter_prompt_version'
            ),
            models.UniqueConstraint(
                fields=['book', 'prompt', 'version'],
                name='unique_book_prompt_version'
            ),
            models.CheckConstraint(
                check=(
                    models.Q(chapter__isnull=False, book__isnull=True) |
                    models.Q(chapter__isnull=True, book__isnull=False)
                ),
                name='summary_chapter_or_book_not_both'
            ),
        ]

    def __str__(self):
        if self.chapter:
            return f"{self.get_summary_type_display()} for {self.chapter} (v{self.version})"
        elif self.book:
            return f"{self.get_summary_type_display()} for {self.book} (v{self.version})"
        return f"{self.get_summary_type_display()} (v{self.version})"


class UsageTracking(models.Model):
    """
    Tracks daily and monthly API usage for cost control and limit enforcement.
    Enables strict spending limits to prevent accidental overspending.
    """
    # Date tracking
    date = models.DateField(
        unique=True,
        help_text='Date for daily tracking'
    )
    month_year = models.CharField(
        max_length=7,
        db_index=True,
        help_text='Month in YYYY-MM format for monthly aggregation'
    )

    # Daily counters
    daily_summaries_count = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text='Number of summaries generated today'
    )
    daily_tokens_used = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text='Total tokens used today'
    )
    daily_cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal('0.000000'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text='Total cost in USD for today'
    )

    # Monthly counters
    monthly_summaries_count = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text='Number of summaries generated this month'
    )
    monthly_tokens_used = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text='Total tokens used this month'
    )
    monthly_cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal('0.000000'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text='Total cost in USD for this month'
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['month_year']),
            models.Index(fields=['-date']),
        ]

    def __str__(self):
        return f"Usage for {self.date} (Month: {self.month_year})"


class ProcessingJob(models.Model):
    """
    Tracks batch processing jobs for AI summarization operations.
    Supports real-time progress tracking via WebSocket updates.
    """
    JOB_TYPE_CHOICES = [
        ('batch_summarization', 'Batch Summarization'),
        ('book_analysis', 'Book Analysis'),
        ('chapter_analysis_pipeline', 'Chapter Analysis Pipeline'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name='processing_jobs'
    )
    job_type = models.CharField(
        max_length=50,
        choices=JOB_TYPE_CHOICES
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )

    # Progress tracking
    progress_percent = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text='Progress percentage (0-100)'
    )
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        help_text='Job-specific data including chapter_ids, prompt_id, per-chapter results'
    )

    # Timestamps
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['book', 'status']),
            models.Index(fields=['status']),
            models.Index(fields=['job_type']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f"{self.get_job_type_display()} - {self.get_status_display()}"


class Settings(models.Model):
    """
    Singleton model for application-wide AI settings and cost limits.
    Only one Settings record should exist in the database.
    """
    monthly_limit_usd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('5.00'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text='Monthly spending limit in USD (default: $5.00)'
    )
    daily_summary_limit = models.IntegerField(
        default=100,
        validators=[MinValueValidator(0)],
        help_text='Daily summary generation limit (default: 100)'
    )
    ai_features_enabled = models.BooleanField(
        default=True,
        help_text='Emergency stop: disable all AI features when False'
    )
    default_model = models.CharField(
        max_length=100,
        default='gpt-4o-mini',
        help_text='Default OpenAI model for summary generation'
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'settings'

    def __str__(self):
        return "Application Settings"

    def save(self, *args, **kwargs):
        """Enforce singleton pattern: only one Settings record allowed."""
        if not self.pk and Settings.objects.exists():
            # If trying to create a new instance when one already exists, raise error
            raise ValueError('Only one Settings instance is allowed. Use Settings.objects.first() to retrieve it.')
        return super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        """Get or create the singleton Settings instance."""
        settings, created = cls.objects.get_or_create(pk=1)
        return settings
