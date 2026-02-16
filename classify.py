"""OpenAI-based conference classification and info extraction."""

import json
import re
import os
from openai import OpenAI
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Load environment variables from .env if it exists, otherwise rely on shell env
load_dotenv(os.path.join(SCRIPT_DIR, ".env"), override=False)

# Lazy-initialized client
_client = None
_model = None


def _get_client():
    global _client, _model
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY not found in environment variables. "
                "Please set it with: export OPENAI_API_KEY='your_key'"
            )
        _client = OpenAI(api_key=api_key)
        config_path = os.path.join(SCRIPT_DIR, "config.json")
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = json.load(f)
            _model = cfg.get("openai_model", "gpt-4o-mini")
        else:
            _model = "gpt-4o-mini"
    return _client, _model


def extract_with_openai(title, page_text):
    """Use OpenAI to extract structured conference info from page text."""
    client, model = _get_client()

    prompt = f"""Extract the following fields from this conference announcement page.
Return a JSON object with exactly these keys. Use empty string "" if a field is not found.

- "submission_deadline": The submission/paper deadline as a human-readable string (e.g. "March 30, 2026"). If the deadline has passed, is "expired", "closed", "TBA", or similar non-date text, return empty string ""
- "deadline_date": The submission deadline as an ISO date YYYY-MM-DD (e.g. "2026-03-30"). If the year is missing, assume 2026. If the deadline has passed or is not a real date, return empty string ""
- "conference_dates": When the conference takes place (e.g. "September 4-5, 2026")
- "location": Where the conference is held (city, country, or institution)
- "keynote_speakers": Names of keynote/invited/plenary speakers, comma-separated
- "description": A 1-2 sentence summary of what the conference is about
- "topics": Broad research fields (max 25 words total). Use short general category names like "labor economics, development, trade" — not specific paper titles or session names

Conference title: {title}

Page text:
{page_text[:4000]}"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You extract structured data from conference announcements. Always respond with valid JSON only, no markdown fences."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        print(f"    OpenAI extraction error: {e}")
        return {}


def check_relevance(title, page_text, include_topics, exclude_topics):
    """Quick OpenAI call to decide if a conference is relevant.

    Returns (bool, str) — (relevant, reason).
    """
    client, model = _get_client()

    criteria = ""
    if include_topics:
        criteria += f"Topics to INCLUDE: {include_topics}\n"
    if exclude_topics:
        criteria += f"Topics to EXCLUDE: {exclude_topics}\n"

    prompt = f"""Decide if this academic conference is relevant for a researcher based on the topic filters below.
Return a JSON object: {{"relevant": true/false, "reason": "<1 sentence explanation>", "detected_topics": "<comma-separated topics you identified>"}}

{criteria}
Rules:
- A conference is relevant if ANY of its topics or sessions broadly falls into at least one include topic. It does NOT need to be the primary focus — even partial overlap is enough.
- A conference is NOT relevant only if its focus is clearly and specifically on an exclude topic, with no meaningful overlap with include topics.
  For example: a conference on "AI in finance" or "machine learning for asset pricing" is a FINANCE conference, not a machine-learning conference — exclude it.
- Broad conferences that accept submissions from many fields (including the include topics) ARE relevant — include them.
- When in doubt, include the conference.

Conference title: {title}

Page text (excerpt):
{page_text[:1500]}"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You classify academic conference relevance. Respond with valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=120,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        relevant = bool(data.get("relevant", True))
        reason = data.get("reason", "")
        topics = data.get("detected_topics", "")
        return relevant, reason, topics
    except Exception as e:
        print(f"    Relevance check error: {e}")
        return True, "error — defaulting to include", ""
