# Conference Scraper

A Python tool that scrapes academic conference listings from multiple sources, deduplicates them, extracts key details via OpenAI, and exports everything to a formatted Excel file — sorted by closest deadline so you never miss an application window.

## Sources

| Source | URL |
|---|---|
| The Economic Misfit | [theeconomicmisfit.com/category/conferences](https://theeconomicmisfit.com/category/conferences/) |
| INOMICS | [inomics.com/top/conferences](https://inomics.com/top/conferences) |

## How it works

On each run, the scraper:

1. **Scrapes** both sources — paginates through listing pages and fetches detail pages for each conference
2. **Deduplicates** across sources and against conferences already in the Excel file (using date + location matching and fuzzy title similarity — no OpenAI calls needed)
3. **Classifies** only new, unique conferences via OpenAI to extract structured fields (deadline, dates, location, speakers, topics, description)
4. **Filters** by topic relevance if `--include` / `--exclude` flags are provided
5. **Moves** conferences with passed deadlines to a separate "Past Conferences" sheet
6. **Writes** everything to `conferences.xlsx`, sorted by submission deadline

Deduplication before classification means conferences appearing on both sites only trigger one OpenAI API call, saving costs.

## Project structure

```
conferences/
├── run.py                  # main entry point (orchestrator)
├── config.json             # settings (OpenAI model)
├── .env                    # API keys (not tracked in git)
├── .gitignore              # excludes .env, .xlsx, and .DS_Store
├── scrapers/
│   ├── __init__.py
│   ├── misfit.py           # scraper for theeconomicmisfit.com
│   └── inomics.py          # scraper for inomics.com
├── dedup.py                # cross-source deduplication logic
├── classify.py             # OpenAI extraction and relevance checking
├── excel_writer.py         # Excel read/write logic
├── test_api_key.py         # diagnostic script to test OpenAI API setup
├── conferences.xlsx        # output file (not tracked in git)
└── old/                    # legacy single-source scripts
    └── scrape_conferences.py
```

## Setup

1. **Install dependencies:**
   ```bash
   pip install requests beautifulsoup4 openai openpyxl python-dotenv
   ```

2. **Set your OpenAI API key:**
   ```bash
   export OPENAI_API_KEY="your_openai_api_key_here"
   ```
   Or create a `.env` file in the project directory:
   ```
   OPENAI_API_KEY=your_openai_api_key_here
   ```

3. **Configure the model (optional):**
   Edit `config.json` to change the OpenAI model:
   ```json
   {
       "openai_model": "gpt-4o-mini"
   }
   ```

## Usage

**Scrape all conferences from all sources (no filtering):**
```bash
python run.py
```

**Scrape with topic filters:**
```bash
# Include specific topics
python run.py --include "applied econ, political economy, development"

# Exclude specific topics
python run.py --exclude "macro-finance, economic theory"

# Combine both
python run.py --include "labor economics, migration" --exclude "economic theory, asset pricing"

# Enable debug mode to see detailed reasoning for include/exclude decisions
python run.py --include "applied econ" --exclude "finance" --debug
```

When `--include` or `--exclude` flags are provided, the script uses a quick OpenAI relevance check before full extraction, saving API costs on irrelevant conferences. Use the `--debug` flag to see detailed reasons for why conferences are included or excluded, along with detected topics.

## Output

The generated `conferences.xlsx` contains two sheets:

| Sheet | Contents |
|---|---|
| **Conferences** | Active conferences sorted by closest submission deadline |
| **Past Conferences** | 10 most recently expired conferences |

Each entry includes: title, submission deadline, conference dates, location, keynote speakers, description, topics, and source URL.

## Deduplication

Conferences are deduplicated in two stages without any OpenAI calls:

1. **Date + location matching** — conferences with the same start date and city/country are grouped together; the entry with richer detail is kept
2. **Fuzzy title matching** — catches duplicates where dates or locations are missing, using token-level similarity

This runs before OpenAI classification, so duplicate conferences across sources only cost one API call.

## Troubleshooting

If you encounter API key issues, run the diagnostic script:
```bash
python3 test_api_key.py
```

This will:
- Check if your API key is properly set
- Test the OpenAI client initialization
- Make a test API call to verify everything works
- Show detailed debugging information

## Requirements

- Python 3.8+
- OpenAI API key
- openpyxl
