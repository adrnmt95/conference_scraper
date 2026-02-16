# Conference Scraper

A Python script that scrapes economics and political science conferences from [theeconomicmisfit.com](https://theeconomicmisfit.com/category/conferences/) and exports them to a formatted Excel file.

## Features

- Automatically scrapes conference listings from multiple pages
- Uses OpenAI API to extract structured information (deadlines, dates, location, speakers, etc.)
- Filters conferences by relevance (excludes macro/finance/pure theory)
- Tracks processed conferences to avoid duplicates
- Exports to Excel with active and past conferences in separate sheets
- Sorted by closest submission deadline

## Setup

1. **Install dependencies:**
   ```bash
   pip install requests beautifulsoup4 openai openpyxl python-dotenv
   ```

2. **Set your OpenAI API key:**
   ```bash
   export OPENAI_API_KEY="your_openai_api_key_here"
   ```

   To make it permanent, add the above line to your `~/.zshrc` or `~/.bash_profile`.

3. **Configure the model (optional):**
   Edit `config.json` to change the OpenAI model:
   ```json
   {
       "openai_model": "gpt-4o-mini"
   }
   ```

## Usage

Run the scraper:
```bash
python scrape_conferences.py
```

The script will:
1. Fetch all conference pages
2. Process new conferences (not already in the Excel file)
3. Extract structured information using OpenAI
4. Filter by relevance
5. Export to `conferences.xlsx`

## Output

The Excel file contains two sheets:
- **Conferences**: Active conferences sorted by submission deadline
- **Past Conferences**: The 10 most recent past conferences

## Configuration

- **OpenAI Model**: Edit `config.json` to change the model (default: `gpt-4o-mini`)
- **Filtering**: Modify `EXCLUDE_KEYWORDS` and `INCLUDE_KEYWORDS` in the script to customize conference filtering

## Requirements

- Python 3.8+
- OpenAI API key
- Internet connection
