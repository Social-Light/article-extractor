"""AI sentiment + summary for extracted articles, via Groq.

Sibling of social-light-platform's monitor/sentiment_ai.py, same idea (an LLM
reads the headline/summary and judges sentiment FROM THE MONITORED
ORGANISATION'S PERSPECTIVE, plus a one-sentence rationale) — but single-key,
not a fallback pool: this app was given one dedicated Groq key
(GROQ_API_KEY), not a shared account pool to fall through. Also adds
summarize_article, which that module doesn't have — a brief 1-2 sentence
summary of what the article actually says, shown in "My Extracts"
(organisations/views.py:my_extracts).

Every call is best-effort: on any failure (no key, SDK missing, rate limit,
malformed response) both functions return None and the caller keeps the
plain-heuristic result — AVE/rank/every other metric already computed by
article_extractor/extractor.py are entirely unaffected either way; this only
ever upgrades sentiment specifically and adds a summary that wasn't there
before, never blocks or fails an extraction.
"""
import json
import logging
import re
import time

from django.conf import settings

logger = logging.getLogger(__name__)

MODEL = getattr(settings, 'SENTIMENT_AI_MODEL', 'openai/gpt-oss-120b')

SENTIMENT_SYSTEM_PROMPT = (
    "You are a media-monitoring analyst. Given a headline and summary, and the "
    "organisation whose media coverage is being monitored, judge the sentiment "
    "of the coverage FROM THAT ORGANISATION'S PERSPECTIVE — not sentiment in "
    "general. Coverage that is bad for a competitor, a critic, or an unrelated "
    "party is not automatically negative for the monitored organisation; read "
    "who the news actually reflects on. Respond with ONLY a single JSON object "
    "— no markdown, no prose, no code fences."
)

SUMMARY_SYSTEM_PROMPT = (
    "You are a media-monitoring analyst. Given an article's headline and "
    "extracted text, write a brief, neutral 1-2 sentence summary of what the "
    "article actually says. Respond with ONLY a single JSON object — no "
    "markdown, no prose, no code fences."
)

_VALID_SENTIMENTS = {'positive', 'negative', 'neutral'}

# A short "try again in Xms/Xs" cooldown is worth one inline retry — anything
# longer (a daily cap) won't clear by waiting, and there's no second key here
# to fall through to, so it's just a wasted request either way.
_RETRY_AFTER_RE = re.compile(r'try again in ([\d.]+)(ms|s)\b')
_MAX_SHORT_RETRY_SECONDS = 3.0


def _short_retry_after(exc):
    match = _RETRY_AFTER_RE.search(str(exc))
    if not match:
        return None
    value, unit = match.groups()
    seconds = float(value) / 1000.0 if unit == 'ms' else float(value)
    return seconds if seconds <= _MAX_SHORT_RETRY_SECONDS else None


def _parse_json(text):
    """Groq's json_object mode guarantees syntactically valid JSON but not a
    particular schema."""
    text = (text or '').strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:]
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end == -1:
        raise ValueError('No JSON object in model response')
    candidate = text[start:end + 1]
    candidate = re.sub(r',(\s*[}\]])', r'\1', candidate)  # trailing commas
    return json.loads(candidate)


def _call(system_prompt, user_prompt, max_tokens):
    """One best-effort Groq call. Returns the parsed JSON dict, or None on
    any failure — never raises."""
    api_key = (getattr(settings, 'GROQ_API_KEY', '') or '').strip()
    if not api_key:
        return None

    try:
        import groq
    except ImportError:
        logger.warning('sentiment_ai: the "groq" package is not installed.')
        return None

    retried_after_wait = False
    while True:
        try:
            client = groq.Groq(api_key=api_key, timeout=15.0, max_retries=1)
            completion = client.chat.completions.create(
                model=MODEL,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
            choice = completion.choices[0]
            if choice.finish_reason == 'length':
                return None
            return _parse_json(choice.message.content)
        except Exception as exc:
            wait = None if retried_after_wait else _short_retry_after(exc)
            if wait is not None:
                retried_after_wait = True
                time.sleep(wait)
                continue
            logger.warning('sentiment_ai: Groq call failed: %s', exc)
            return None


def analyze_sentiment(org_name, headline, summary=''):
    """Return {'sentiment': 'positive'|'negative'|'neutral', 'rationale': str}
    or None on any failure — callers should treat None as "keep the heuristic
    sentiment", never as a reason to fail the extraction."""
    headline = (headline or '').strip()
    summary = (summary or '').strip()
    if not headline and not summary:
        return None

    user_prompt = f"""Organisation being monitored: {org_name}

Headline: {headline}
Summary: {summary or '(none provided)'}

Return a JSON object with exactly these keys:
{{
  "sentiment": "positive" | "negative" | "neutral",
  "rationale": "<one sentence explaining why, from {org_name}'s perspective specifically>"
}}"""

    data = _call(SENTIMENT_SYSTEM_PROMPT, user_prompt, max_tokens=600)
    if not data:
        return None

    sentiment = (data.get('sentiment') or '').strip().lower()
    if sentiment not in _VALID_SENTIMENTS:
        return None
    rationale = (data.get('rationale') or '').strip()[:500]
    return {'sentiment': sentiment, 'rationale': rationale}


def summarize_article(headline, body_text=''):
    """Return a brief 1-2 sentence summary, or None on any failure."""
    headline = (headline or '').strip()
    body_text = (body_text or '').strip()
    if not headline and not body_text:
        return None

    user_prompt = f"""Headline: {headline}
Article text: {body_text[:3000] or '(not available — summarise from the headline alone)'}

Return a JSON object with exactly this key:
{{
  "summary": "<1-2 sentence neutral summary of what the article says>"
}}"""

    data = _call(SUMMARY_SYSTEM_PROMPT, user_prompt, max_tokens=300)
    if not data:
        return None
    summary = (data.get('summary') or '').strip()[:1000]
    return summary or None