# GetHired

GetHired is an automated job discovery, aggregation, filtering, deduplication, and resume-matching platform built with Python (Flask) and a single-page web dashboard. It collects job postings from direct company career pages and external job sources, standardizes and deduplicates listings, filters out invalid or irrelevant roles, and evaluates candidate fit using a tiered scoring engine combining LLMs, local vector embeddings, and rule-based keyword matching.

## What It Does

GetHired automates the job discovery workflow through a deterministic 11-step execution pipeline:

1. **Job Collection**: Ingests job postings via headless browser career site scrapers (Playwright), job board API actors (Apify), dedicated fetchers (Indeed, Naukri), and manual LinkedIn URL imports.
2. **Filtering & Validation**: Validates job posting structural integrity and status to filter out closed or malformed listings.
3. **Deduplication**: Generates canonical job IDs, normalizes URLs and tracking parameters, and merges duplicate postings across sources.
4. **Targeted Filtering**: Applies configurable filters for roles, target locations, recency windows, and title exclusion keywords (e.g., filtering out senior or management roles).
5. **Resume & Job Matching**: Computes personalized relevance scores by comparing candidate resume data against job descriptions.
6. **Scoring & Ranking**: Employs a multi-tier fallback scoring architecture to assign match percentages and confidence levels.
7. **Feed Generation & Tracking**: Sorts curated jobs by relevance, recency, or compensation, excluding already-applied positions while tracking application statuses.

## Key Features

- **Automated Career Page Scraping**: Parses direct company career pages using Playwright and HTML pattern recognition.
- **Multi-Source Job Aggregation**: Integrates third-party scraper actors via Apify alongside direct platform fetchers.
- **Canonical Job Deduplication**: Merges duplicate listings across multiple job boards using URL canonicalization, fuzzy title matching, and req ID extraction.
- **Tiered Multi-Model Scoring Engine**: Dynamically routes candidate-job evaluation across 6 scoring tiers based on availability and quota headroom:
  - **Tier 1**: Google Gemini (with multi-key round-robin rotation) & Groq API
  - **Tier 2**: Anthropic Claude API
  - **Tier 3**: OpenAI API
  - **Tier 4**: Local Ollama model (`qwen2.5:7b`)
  - **Tier 5**: Hybrid Semantic Vector Search using `SentenceTransformers` (`all-mpnet-base-v2`) and FAISS CPU vector index
  - **Tier 6**: Rule-based local keyword and role classifier fallback
- **Score Verification & Consensus**: Runs second-opinion verification for ambiguous score ranges to prevent model disagreement.
- **Application Tracking & Feed Management**: Tracks application states (`Applied`, `Bookmarked`, `Dismissed`) and excludes applied positions from main recommendations.
- **Job Market Analytics & Insights**: Aggregates company activity, role distributions, location density, and timeline metrics.
- **Web Dashboard**: Single-page web UI built with Vanilla JavaScript and Tailwind CSS tokens for interactive job search, filtering, resume upload, and analytics viewing.

## How It Works

```
Raw Job Postings (Career Pages / Apify / Fetchers / Manual Import)
                         │
                         ▼
        [ Store Integrity & Structural Validation ]
                         │
                         ▼
          [ Canonical URL & Title Deduplication ]
                         │
                         ▼
    [ Filters: Role / Location / Recency / Exclusions ]
                         │
                         ▼
            [ Tiered Scoring Architecture ]
  ┌──────────────────────────────────────────────────┐
  │ Tier 1: Gemini & Groq APIs (Key Rotation)        │
  │ Tier 2: Anthropic Claude API                     │
  │ Tier 3: OpenAI API                               │
  │ Tier 4: Ollama Local (`qwen2.5:7b`)              │
  │ Tier 5: Hybrid Semantic (SentenceTransformers+FAISS)│
  │ Tier 6: Local Keyword Rule-Based Scorer          │
  └──────────────────────────────────────────────────┘
                         │
                         ▼
            [ Score Consensus Verification ]
                         │
                         ▼
  [ Final Feed Generation & Application Tracking UI ]
```

## Tech Stack

- **Backend**: Python 3.10+, Flask, Werkzeug, Gunicorn
- **Machine Learning & NLP**: `sentence-transformers` (`all-mpnet-base-v2`), `faiss-cpu`, `scikit-learn`, `numpy`
- **LLM Integrations**: Google Gemini API, Groq API, Anthropic Claude API, OpenAI API, Ollama (Local `qwen2.5:7b`)
- **Scraping & Automation**: Playwright, BeautifulSoup4, Feedparser, Requests, Apify Client
- **Frontend**: HTML5, Vanilla JavaScript, Tailwind CSS (via CDN), Google Fonts (Plus Jakarta Sans, Hanken Grotesk)
- **Data Storage**: Local JSON store files (`jobs_store.json`, `companies.json`, `pattern_store.json`, `applications.json`)

## Project Structure

```
GetHired/
├── app.py                            # Main Flask Web Application and REST API server
├── pipeline.py                       # Authoritative 11-step deterministic execution pipeline
├── scan_coordinator.py               # Orchestrator for multi-company career site scanning
├── background_search_worker.py       # Background worker for continuous automated search
├── hybrid_scorer.py                  # Tiered multi-model scoring orchestrator (Tiers 1-6)
├── llm_router.py                     # API key provider router, rate-limit manager, and fallback selector
├── local_scorer.py                   # Rule-based local keyword and role classifier scorer
├── hybrid_semantic_fallback.py       # Vector-based semantic similarity scoring fallback
├── embedding_service.py              # SentenceTransformers vector embedding service
├── vector_store.py                   # FAISS CPU vector index manager
├── job_deduplicator.py               # Job deduplication engine
├── job_identity.py                   # Canonical ID generator and URL normalizer
├── apify_scanner.py                  # Job scraper integration using Apify actors
├── browser_scanner.py                # Direct career page scraper using Playwright & BeautifulSoup
├── fetchers/                         # Source-specific fetchers (Indeed, Naukri)
│   ├── indeed_fetcher.py
│   └── naukri_fetcher.py
├── resume_parser.py                  # Candidate resume text and skill parser
├── application_tracker.py            # Application state tracker
├── recommendation_engine.py          # Market insights and recommendation generator
├── insights_aggregator.py            # Analytics aggregator across companies, roles, and locations
├── store_integrity_checker.py        # Integrity checker for job data structures
├── test_suite.py                     # Integrated regression test suite
├── static/                           # Web dashboard static frontend
│   └── index.html                    # Single-page interface template and scripts
├── deployment/                       # Deployment guides
│   └── ollama_setup.md               # Setup instructions for Ollama on Linux VMs
├── config.json                       # Application configuration settings
├── companies.json                    # Target company directory
├── requirements.txt                  # Python dependencies
└── .env.example                      # Environment variables template file
```

## Setup

Follow these instructions to set up and run GetHired locally.

### 1. Clone Repository
```bash
git clone https://github.com/aashbirsingh25/GetHired.git
cd GetHired
```

### 2. Create Python Virtual Environment
```bash
python -m venv venv
```

Activate the environment:
- **On Linux/macOS**:
  ```bash
  source venv/bin/activate
  ```
- **On Windows**:
  ```cmd
  venv\Scripts\activate
  ```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

*(Optional for browser-based scraping)*: Install Playwright browsers:
```bash
playwright install chromium
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Edit `.env` and fill in your API keys (see [Environment Variables](#environment-variables)).

### 5. Run the Application
```bash
python app.py
```
The application will start locally on `http://127.0.0.1:5000`.

## Environment Variables

GetHired supports multiple API providers. Configure any or all of the following in `.env`:

| Variable | Description | Required / Optional |
|---|---|---|
| `GEMINI_API_KEY` | Google Gemini API key | Optional (Tier 1 scoring) |
| `GEMINI_API_KEYS` | Comma-separated Gemini API keys for round-robin rotation | Optional |
| `GROQ_API_KEY` | Groq API key | Optional (Tier 1 scoring) |
| `CLAUDE_API_KEY` | Anthropic Claude API key | Optional (Tier 2 scoring) |
| `OPENAI_API_KEY` | OpenAI API key | Optional (Tier 3 scoring) |
| `APIFY_API_TOKEN` | Apify API token for third-party job actor scraping | Optional (Apify scans) |

*Note: If no external API keys are configured, GetHired automatically falls back to local Ollama (Tier 4), hybrid semantic vector search (Tier 5), or rule-based local keyword scoring (Tier 6).*

## Running Tests

Run the integrated regression test suite to verify deduplication, pipeline filtering, scoring logic, and application tracking:

```bash
python test_suite.py
```

The test suite executes 12 regression test cases without requiring external network connectivity.

## Deployment

GetHired supports running local LLM scoring via Ollama on Linux server instances (such as Oracle Cloud Always Free ARM instances).

Refer to [deployment/ollama_setup.md](deployment/ollama_setup.md) for step-by-step instructions on setting up Ollama with the `qwen2.5:7b` model as a `systemd` service.

## Limitations

- **Storage Layer**: Currently relies on local JSON files (`jobs_store.json`, `applications.json`) for persistence, intended for single-user local deployment.
- **Scraper Maintenance**: Direct career page DOM selectors may require periodic pattern updates when company websites change layout.
- **Model Initial Load**: Initial execution of semantic vector scoring downloads the `SentenceTransformers` model (`all-mpnet-base-v2`), which requires initial bandwidth and RAM.

## Future Improvements

- Transition local JSON storage to a relational database (such as PostgreSQL or SQLite).
- Implement multi-user candidate profile support.
- Add real-time email or webhook alerts for high-matching job postings.

## License

No license has currently been specified for this project.

## Author

**Aashbir Singh**  
GitHub: [https://github.com/aashbirsingh25](https://github.com/aashbirsingh25)
