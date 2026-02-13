# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VoxLibri is an AI-enhanced book comprehension platform. It processes EPUB and PDF files to enable AI-powered chapter summaries and book-level analysis. Local-first Django application with vanilla JavaScript frontend.

**Status:** Phases 1-4 complete (upload, parsing, AI summarization, book-level analysis). Not yet built: multi-model AI support, speed reading, user authentication.

## Development Commands

```bash
# Setup (first time)
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate

# Run development server
source .venv/bin/activate
python manage.py runserver
# Access at http://127.0.0.1:8000/

# Run task worker (REQUIRED for AI analysis - separate terminal)
python manage.py qcluster
# Without qcluster, "Analyze Book" shows "Waiting for worker..." and never progresses

# Testing
python manage.py test                              # All tests
python manage.py test books_core                   # App tests
python manage.py test books_core.tests.test_models # Single file

# Database
python manage.py makemigrations  # After model changes
python manage.py migrate
python manage.py shell

# Prompt sync
python manage.py sync_prompts         # Manual sync
python manage.py sync_prompts --list  # List status
python manage.py sync_prompts --force # Force resync
```

## Architecture

### Data Model: Book → Chapter → Summary

- **Book**: Metadata, file paths, processing status, `readability_metrics` (JSONField). Supports EPUB & PDF.
- **Chapter**: FK to Book, stores markdown content, `readability_metrics` (JSONField). Unique: (book, chapter_number)
- **Summary**: FK to chapter OR book (mutually exclusive). Versioned with cost tracking.
- **Prompt**: AI templates synced from `prompts/` directory. Auto-syncs on startup.
- **UsageTracking**: Daily/monthly API cost aggregation.
- **ProcessingJob**: Background job status tracking.
- **Settings**: Singleton config via `Settings.get_settings()`. Limits: $5/month, 100 summaries/day.

### Services Layer (`books_core/services/`)

Business logic lives here, not in views:
- `epub_parser.py` / `pdf_parser.py` - File parsing
- `openai_service.py` - AI API integration
- `cost_control_service.py` - Token counting & limits
- `chapter_analysis_pipeline_service.py` - Full analysis orchestration
- `readability_service.py` - Local textstat readability metrics (zero API cost)
- `report_epub_service.py` - EPUB export generation

### View Organization

- `views.py` - Main views (Upload, Library, Detail, Reading, Settings)
- `summary_api_views.py` - Summary generation APIs
- `book_analysis_views.py` - Book analysis APIs
- `chapter_pipeline_views.py` - Analysis pipeline & report export

## AI Analysis Pipeline

Orchestrated by `ChapterAnalysisPipelineService`:

0. **Readability** (local, free): `ReadabilityService.compute_all_for_book()` runs textstat metrics on all chapters before AI processing. Computes Flesch Reading Ease, grade level, Gunning Fog, SMOG, Coleman-Liau, difficulty tier (accessible/moderate/technical/dense), and estimated reading time per chapter and book.
1. **Per-Chapter** (4 prompts each): `summarize_chapter`, `rate_chapter`, `extract_chapter_wisdom`, `extract_references`
2. **Book-Level Aggregation** (4 prompts): `aggregate_summaries`, `aggregate_book_rating`, `aggregate_wisdom`, `aggregate_references`

**Endpoints:**
- `GET /books/<id>/analyze/cost-preview/` - Cost estimate
- `POST /books/<id>/analyze/` - Start pipeline
- `GET /books/<id>/analyze/progress/<job_id>/` - Poll progress
- `GET /books/<id>/report/` - View analysis report
- `GET /books/<id>/report/export/` - Download EPUB report

## Prompts

Stored as markdown in `prompts/` with YAML frontmatter:

```markdown
---
name: summarize_chapter
category: summarization
default_model: gpt-4o-mini
variables: [content]
---
# IDENTITY
...
```

Auto-synced on `runserver` startup.

## Key Configuration

- **Database**: SQLite at `db.sqlite3`
- **Media**: `media/` (uploads), max 50MB
- **Logging**: Errors to `logs/errors.log`
- **Django-Q2**: 4 workers for parallel chapter processing (reduce if hitting rate limits)

## Book Processing Flow

1. Upload → Book created with `status='uploaded'`
2. Parse → Chapters created with markdown content
   - EPUB: Anchor-based TOC splitting, fallback to headings
   - PDF: Bookmark-based splitting, fallback to every 30 pages
3. Cover extracted, word counts calculated
4. `status='completed'` → Ready for AI analysis
