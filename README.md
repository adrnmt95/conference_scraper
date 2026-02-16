# 📅 Conference Scraper

A Python tool that scrapes academic conference listings from [theeconomicmisfit.com](https://theeconomicmisfit.com/category/conferences/), extracts key details (submission deadlines, dates, locations, speakers), and exports everything to a formatted Excel file — sorted by closest deadline so you never miss an application window.

## What it does

The scraper collects conferences from [theeconomicmisfit.com](https://theeconomicmisfit.com/category/conferences/) with **optional topic filtering**. You can specify which topics to include or exclude via command-line arguments, or scrape all conferences without any filtering. It uses **OpenAI's GPT-4o-mini** (via the API) to parse unstructured conference announcement pages into clean, structured data.

On each run, the script:
1. Paginates through all conference listing pages
2. Skips conferences already present in the Excel file
3. Sends new conference pages to the OpenAI API for structured extraction
4. Optionally filters by topic relevance (if `--include` or `--exclude` flags are provided)
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

**Scrape all conferences (no filtering):**
```bash
python scrape_conferences.py
```

**Scrape with topic filters:**
```bash
# Include specific topics
python scrape_conferences.py --include "applied econ, political economy, development"

# Exclude specific topics
python scrape_conferences.py --exclude "macro-finance, economic theory"

# Combine both
python scrape_conferences.py --include "labor economics, migration" --exclude "economic theory, asset pricing"
```

When you provide `--include` or `--exclude` flags, the script uses OpenAI to check conference relevance before extracting full details, saving API costs on irrelevant conferences.

## Output

The generated `conferences.xlsx` contains two sheets:

| Sheet | Contents |
|---|---|
| **Conferences** | Active conferences sorted by closest submission deadline |
| **Past Conferences** | 10 most recently expired conferences |

Each entry includes: title, submission deadline, conference dates, location, keynote speakers, description, topics, and source URL.

## Customization

You can customize the scraper's behavior in several ways:

1. **Topic filtering**: Use the `--include` and `--exclude` command-line arguments to filter conferences by your research interests. No need to edit the script.

2. **OpenAI model**: Edit `config.json` to change the model used for extraction:
   ```json
   {
       "openai_model": "gpt-4o-mini"
   }
   ```
   (You can use other models like `gpt-4o` for potentially better accuracy, but at higher cost.)

## Requirements

- Python 3.8+
- OpenAI API key
- Internet connection
