# VoxLibri

AI-enhanced book comprehension platform. Upload EPUB/PDF files, read with a 3-column interface, and generate AI-powered chapter summaries with cost controls.

## Features

- **EPUB & PDF parsing** with smart chapter extraction and cover images
- **3-column reading interface** with resizable panels
- **AI chapter summaries** using OpenAI GPT-4o-mini
- **Book-level analysis** aggregating insights across all chapters
- **Cost controls** with daily/monthly limits and preview before every AI call
- **Fabric prompt integration** for customizable summarization styles

## Quick Start

```bash
# Clone and setup
git clone https://github.com/neilgilleran/voxlibri.git
cd voxlibri
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY

# Initialize database
python manage.py migrate
python manage.py createsuperuser

# Run (two terminals)
python manage.py runserver      # Terminal 1: Web server
python manage.py qcluster       # Terminal 2: Task worker (required for AI)
```

Access at http://localhost:8000

## Configuration

Create `.env` in project root:

```bash
DJANGO_SECRET_KEY=your-secret-key-here
OPENAI_API_KEY=your-openai-api-key-here
```

Get an OpenAI API key at https://platform.openai.com/api-keys

## Usage

1. **Upload** - Click "Upload Book" and select an EPUB or PDF
2. **Read** - Open book, navigate chapters in the left panel
3. **Summarize** - Select a prompt in the right panel, click "Generate Summary"
4. **Analyze** - Use "Analyze Book" for full book-level insights

### Cost Controls

- **Monthly limit**: $5/month default (configurable in Settings)
- **Daily limit**: 100 summaries/day default
- **Preview required**: Every AI call shows cost before confirmation

## Project Structure

```
voxlibri/
├── books_core/           # Main Django app
│   ├── models.py         # Book, Chapter, Summary, Prompt
│   ├── views.py          # Web views
│   ├── services/         # Business logic (parsing, AI, cost control)
│   └── templates/        # HTML templates
├── prompts/              # AI prompt templates (YAML frontmatter + markdown)
├── media/books/          # Uploaded files and covers
└── voxlibri/             # Django project settings
```

## Commands

```bash
python manage.py runserver              # Start web server
python manage.py qcluster               # Start task worker
python manage.py test books_core        # Run tests (179+ passing)
python manage.py sync_prompts           # Sync Fabric prompts from GitHub
python manage.py sync_prompts --list    # List prompt sync status
```

## Tech Stack

- **Backend**: Django 5.2+, Django-Q2, Django Channels
- **AI**: OpenAI API (GPT-4o-mini), tiktoken
- **Parsing**: ebooklib, BeautifulSoup4, PyMuPDF
- **Frontend**: Django Templates, vanilla JS, CSS Grid

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chapters/<id>/summary-preview/` | POST | Preview cost before generating |
| `/api/chapters/<id>/summary-generate/` | POST | Generate chapter summary |
| `/api/summaries/batch-preview/` | POST | Preview batch cost |
| `/api/summaries/batch-generate/` | POST | Generate multiple summaries |
| `/books/<id>/analyze/` | POST | Start book analysis pipeline |
| `/books/<id>/report/` | GET | View analysis report |

## Troubleshooting

**"Waiting for worker..."** - Start qcluster in a separate terminal

**AI features disabled** - Check OPENAI_API_KEY in .env, verify Settings > AI Features Enabled

**Limit exceeded** - Increase limits in Settings or wait for reset (daily at midnight UTC, monthly at month start)

## License

MIT License - see [LICENSE](LICENSE)

## Acknowledgments

- [Fabric](https://github.com/danielmiessler/fabric) - Prompt library
- [OpenAI](https://openai.com) - GPT-4o-mini
- [Django](https://djangoproject.com) - Web framework
