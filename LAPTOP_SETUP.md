# GetHired — Setup Instructions for the Other Laptop (for Antigravity)

**Goal:** get the existing `GetHired` folder on this laptop up to date with GitHub and running, installing anything the machine is missing. Then it will be deployed from here.

Read every step. Do not skip the manual `.env` step — it cannot be automated because the secrets are not in GitHub.

---

## 0. Prerequisites to check first

Run these and install whatever is missing:

- **Python 3.12** (the project is pinned to 3.12; other versions may break `faiss` / `sentence-transformers`)
  - Check: `python3.12 --version`
  - macOS install: `brew install python@3.12`
  - Windows: install from python.org, ensure `py -3.12` works
- **Git** — check `git --version`
- **Ollama** (local LLM scoring tier — this was installed on the original machine and must be installed here too)
  - Check: `ollama --version`
  - Install: https://ollama.com/download (or macOS `brew install ollama`)

## 1. Update the code from GitHub

The folder already exists. From inside the `GetHired` folder:

```bash
git fetch origin
git checkout master
git pull origin master
```

- Remote: `https://github.com/aashbirsingh25/GetHired.git`
- Latest commit should be `707b569 docs: Rewrite README to match the real system` (or newer).
- **If `git pull` reports local changes to `.json` files** (companies.json, config.json, scan_order.json, etc.): these are the app's live state/data files. On the machine that has been running, they are the source of truth. If this laptop's copies are stale, run `git stash` then `git pull` then decide with the user whether to keep this laptop's data or the pulled version. **Do not blindly discard `.json` data files** — they hold the ~7,600 collected jobs and company lists.

## 2. Create the main Python environment

From inside the `GetHired` folder:

```bash
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

**`requirements.txt` is incomplete** — the code also imports these; install them too:

```bash
.venv/bin/pip install playwright scrapling pytz lxml
.venv/bin/playwright install chromium
```

(`playwright` drives the career-page scrapers; `scrapling` does TLS-impersonation fetching; `pytz` is used by the quota rollover; `lxml` by the HTML parsers.)

## 3. The `.env` file — MANUAL, cannot be pulled from GitHub

`.env` is gitignored, so it did **not** come with `git pull`. The app will not score jobs without it.

**Ask the user to copy their `.env` file from the original Mac to the `GetHired` folder on this laptop** (AirDrop, USB, or a private paste — never commit it, never send it over a public channel).

It must contain these keys (names shown, values are the user's secrets):

```
GEMINI_API_KEYS=      (comma-separated, ~10 keys)
GROQ_API_KEY=
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
JOOBLE_API_KEY=
APIFY_API_TOKEN=
```

After copying, lock its permissions (macOS/Linux): `chmod 600 .env`

A template exists at `.env.example` if you need to see the shape.

## 4. Local LLM model (Ollama tier)

```bash
ollama serve &            # start the Ollama background server
ollama pull qwen2.5:7b    # ~4.7 GB download — the model GetHired uses
```

Note: on the original Mac, `brew services` for Ollama did not work, so it was run as a plain background process (`ollama serve &`). Do the same here unless the user sets up a proper service.

## 5. (Optional) Indeed fetcher — isolated environment

The JobSpy Indeed fetcher has dependencies that **conflict** with the main environment, so it lives in its own venv and is called via subprocess. Only set this up if Indeed results are wanted:

```bash
python3.12 -m venv .venv-jobspy
.venv-jobspy/bin/pip install python-jobspy
```

## 6. Run it

macOS/Linux:
```bash
OMP_NUM_THREADS=1 .venv/bin/python app.py
```
Windows (PowerShell):
```powershell
$env:OMP_NUM_THREADS=1; .\.venv\Scripts\python app.py
```

- **`OMP_NUM_THREADS=1` is mandatory on macOS** — without it the embedding/FAISS stack segfaults on startup. `app.py` sets it internally too, but exporting it first is the safe belt-and-suspenders.
- Dashboard: **http://localhost:5050** (takes ~15 s to start — it loads the embedding model).

## 7. Verify it works

```bash
OMP_NUM_THREADS=1 .venv/bin/python test_ui_e2e.py     # expect "ALL PASS" at the end
```

Then open http://localhost:5050 and confirm the feed loads with jobs.

---

## Summary of what must be installed on this laptop
| Thing | Why | How |
|---|---|---|
| Python 3.12 | runtime, pinned version | brew / python.org |
| Git | pull the code | pre-installed usually |
| pip packages (requirements.txt **+ playwright, scrapling, pytz, lxml**) | app dependencies | `pip install` |
| Playwright Chromium | career-page scraping | `playwright install chromium` |
| **`.env` file (manual copy)** | API keys — NOT in git | user copies from Mac |
| Ollama + `qwen2.5:7b` | local scoring tier | ollama.com + `ollama pull` |
| `.venv-jobspy` (optional) | Indeed fetcher, isolated | separate venv |

## Things that will NOT transfer via git pull (must be handled manually)
- **`.env`** — the API keys (step 3) — the one truly required manual item
- **`.venv` / `.venv-jobspy`** — Python environments are per-machine, rebuild them (steps 2 & 5)
- **Ollama model** — re-download on this machine (step 4)
- The `.json` data files (companies, jobs, config) DO transfer via git if they were committed on the original machine — but confirm they're current (step 1 note).
