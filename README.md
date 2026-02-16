# 📅 Conference Scraper

A Python tool that scrapes academic conference listings from [theeconomicmisfit.com](https://theeconomicmisfit.com/category/conferences/), extracts key details (submission deadlines, dates, locations, speakers), and exports everything to a formatted Excel file — sorted by closest deadline so you never miss an application window.

## What it does

The scraper targets conferences in **applied economics, econometrics, political economy, and political science**, automatically filtering out macro, finance, and pure economic/econometric theory. It uses **OpenAI's GPT-4o-mini** (via the API) to parse unstructured conference announcement pages into clean, structured data.

On each run, the script:
1. Paginates through all conference listing pages
2. Skips conferences already present in the Excel file
3. Sends new conference pages to the OpenAI API for structured extraction
4. Filters by topic relevance using keyword-based heuristics
5. Moves conferences with passed deadlines to a separate "Past Conferences" sheet
6. Exports everything to `conferences.xlsx`, sorted by submission deadline

## Setup

1. **Install dependencies:**
   ```bash
   pip install requests beautifulsoup4 openai openpyxl python-dotenv
   ```

2. **Set your OpenAI API key as an environment variable:**
   ```bash
   export OPENAI_API_KEY="your_openai_api_key_here"
   ```
   To make it permanent, add the line to your `~/.zshrc` or `~/.bash_profile`.

3. **Configure the model (optional):**
   Edit `config.json` to change the OpenAI model:
   ```json
   {
       "openai_model": "gpt-4o-mini"
   }
   ```

## Usage

```bash
python scrape_conferences.py
```

## Output

The generated `conferences.xlsx` contains two sheets:

| Sheet | Contents |
|---|---|
| **Conferences** | Active conferences sorted by closest submission deadline |
| **Past Conferences** | 10 most recently expired conferences |

Each entry includes: title, submission deadline, conference dates, location, keynote speakers, description, topics, and source URL.

## Customization

The topic filtering is controlled by keyword lists in the script:
- `INCLUDE_KEYWORDS` — topics to keep (e.g. labor, development, conflict, migration, political economy)
- `EXCLUDE_KEYWORDS` — topics to drop (e.g. asset pricing, monetary economics, corporate finance)
- `EXPLICIT_EXCLUDE_TITLES` — specific conference titles to always skip

Edit these lists to adapt the scraper to your own research interests.

## Requirements

- Python 3.8+
- OpenAI API key
- Internet connection
