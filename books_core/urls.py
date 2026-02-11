"""
URL configuration for books_core app.

URL Structure:
- /fiction/          - Fiction library and book views
- /nonfiction/       - Non-fiction library and book views
- /                  - Redirects to /nonfiction/
- /upload/           - Shared upload page
- /settings/         - Shared settings
- /api/              - Shared API endpoints
"""
from django.urls import path, include
from django.views.generic import RedirectView

from .views import (
    # Base views (still used for legacy URLs)
    LibraryView,
    UploadBookView,
    BookDetailView,
    ReadingView,
    DeleteBookView,
    SettingsManagementView,
    # Section-specific views
    NonfictionLibraryView,
    NonfictionBookDetailView,
    NonfictionReadingView,
    FictionLibraryView,
    FictionBookDetailView,
    FictionReadingView,
)

# Import fiction-specific views
from .fiction_views import (
    ResumeReadingView,
    ResumeReadingCostPreview,
    GenerateResumeReading,
    ResumeReadingSummaryView,
)

# Import API views
from .summary_api_views import (
    # Task Group 3.1: Summary Generation API
    SummaryPreviewView,
    SummaryGenerateView,
    ChapterSummariesView,
    SummaryDetailView,

    # Task Group 3.2: Batch Processing API
    BatchPreviewView,
    BatchGenerateView,

    # Task Group 3.3: Prompt Management API
    PromptsListView,
    FabricSyncView,
    PromptPreviewView,

    # Task Group 3.4: Settings & Usage API
    SettingsView,
    UsageStatsView,
    UsageHistoryView,
)

# Import book analysis views
from .book_analysis_views import (
    BookSummaryView,
    BookAnalysisCostPreviewView,
    GenerateBookAnalysisView,
)

# Import chapter pipeline views
from .chapter_pipeline_views import (
    PipelineCostPreviewView,
    TriggerPipelineView,
    PipelineProgressView,
    BookReportView,
    ExportReportEpubView,
)

# Non-fiction section URLs
nonfiction_patterns = [
    path('', NonfictionLibraryView.as_view(), name='library'),
    path('books/<int:pk>/', NonfictionBookDetailView.as_view(), name='book_detail'),
    path('books/<int:book_id>/read/', NonfictionReadingView.as_view(), name='reading_view'),
    path('books/<int:book_id>/read/<int:chapter_number>/', NonfictionReadingView.as_view(), name='reading_view_chapter'),
    path('books/<int:pk>/delete/', DeleteBookView.as_view(), name='delete_book'),
    # Non-fiction specific: Analysis pipeline
    path('books/<int:book_id>/analyze/cost-preview/', PipelineCostPreviewView.as_view(), name='pipeline_cost_preview'),
    path('books/<int:book_id>/analyze/', TriggerPipelineView.as_view(), name='trigger_pipeline'),
    path('books/<int:book_id>/analyze/progress/<int:job_id>/', PipelineProgressView.as_view(), name='pipeline_progress'),
    path('books/<int:book_id>/report/', BookReportView.as_view(), name='book_report'),
    path('books/<int:book_id>/report/export/', ExportReportEpubView.as_view(), name='export_report_epub'),
    # Legacy book-level analysis
    path('books/<int:book_id>/summary/', BookSummaryView.as_view(), name='book_summary'),
    path('books/<int:book_id>/summary/cost-preview/', BookAnalysisCostPreviewView.as_view(), name='book_analysis_cost_preview'),
    path('books/<int:book_id>/summary/generate/', GenerateBookAnalysisView.as_view(), name='generate_book_analysis'),
]

# Fiction section URLs
fiction_patterns = [
    path('', FictionLibraryView.as_view(), name='library'),
    path('books/<int:pk>/', FictionBookDetailView.as_view(), name='book_detail'),
    path('books/<int:book_id>/read/', FictionReadingView.as_view(), name='reading_view'),
    path('books/<int:book_id>/read/<int:chapter_number>/', FictionReadingView.as_view(), name='reading_view_chapter'),
    path('books/<int:pk>/delete/', DeleteBookView.as_view(), name='delete_book'),
    # Fiction specific: Resume reading
    path('books/<int:book_id>/resume/', ResumeReadingView.as_view(), name='resume_reading'),
    path('books/<int:book_id>/resume/preview/', ResumeReadingCostPreview.as_view(), name='resume_reading_preview'),
    path('books/<int:book_id>/resume/generate/', GenerateResumeReading.as_view(), name='resume_reading_generate'),
    path('books/<int:book_id>/resume/<int:summary_id>/', ResumeReadingSummaryView.as_view(), name='resume_reading_summary'),
]

urlpatterns = [
    # Section URLs with namespacing
    path('nonfiction/', include((nonfiction_patterns, 'nonfiction'), namespace='nonfiction')),
    path('fiction/', include((fiction_patterns, 'fiction'), namespace='fiction')),

    # Root redirects to non-fiction
    path('', RedirectView.as_view(url='/nonfiction/', permanent=False), name='home'),

    # Shared pages
    path('upload/', UploadBookView.as_view(), name='upload_book'),
    path('settings/', SettingsManagementView.as_view(), name='settings'),

    # Legacy URLs (for backward compatibility) - redirect or keep working
    path('library/', RedirectView.as_view(url='/nonfiction/', permanent=True)),
    path('books/<int:pk>/', BookDetailView.as_view(), name='book_detail'),
    path('books/<int:book_id>/read/', ReadingView.as_view(), name='reading_view'),
    path('books/<int:book_id>/read/<int:chapter_number>/', ReadingView.as_view(), name='reading_view_chapter'),
    path('books/<int:pk>/delete/', DeleteBookView.as_view(), name='delete_book'),

    # API endpoints - shared across all book types
    path('api/chapters/<int:chapter_id>/summary-preview/', SummaryPreviewView.as_view(), name='api_summary_preview'),
    path('api/chapters/<int:chapter_id>/summary-generate/', SummaryGenerateView.as_view(), name='api_summary_generate'),
    path('api/chapters/<int:chapter_id>/summaries/', ChapterSummariesView.as_view(), name='api_chapter_summaries'),
    path('api/summaries/<int:summary_id>/', SummaryDetailView.as_view(), name='api_summary_detail'),
    path('api/summaries/batch-preview/', BatchPreviewView.as_view(), name='api_batch_preview'),
    path('api/summaries/batch-generate/', BatchGenerateView.as_view(), name='api_batch_generate'),
    path('api/prompts/', PromptsListView.as_view(), name='api_prompts_list'),
    path('api/prompts/fabric/sync/', FabricSyncView.as_view(), name='api_fabric_sync'),
    path('api/prompts/<int:prompt_id>/preview/', PromptPreviewView.as_view(), name='api_prompt_preview'),
    path('api/settings/', SettingsView.as_view(), name='api_settings'),
    path('api/settings/usage/', UsageStatsView.as_view(), name='api_usage_stats'),
    path('api/settings/usage/history/', UsageHistoryView.as_view(), name='api_usage_history'),
]
