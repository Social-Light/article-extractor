"""
Social Light - Enhanced Newspaper Article Extractor
Extracts articles from newspaper PDFs and images based on organization keywords
Features:
- Article continuation detection across pages
- Font-size based title extraction around keywords
- OCR support for image files
- Improved date detection
"""

import re
import os
import difflib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from textblob import TextBlob
import pytesseract
from PIL import Image as PILImage
import cv2
import numpy as np


class EnhancedArticleExtractor:
    """
    Enhanced article extractor with advanced features:
    - Article continuation detection
    - Font-size based title extraction
    - OCR support for images
    - Improved date detection
    """

    def __init__(self, media_root: str, organisation, extraction_type: str = 'PR', extra_keywords=None):
        """
        Initialize enhanced extractor for a specific organization

        Args:
            media_root: Base media directory path
            organisation: Organisation model instance
            extraction_type: 'PR' or 'Ad'
            extra_keywords: optional list of freeform terms typed in for just
                this one run (organisations/views.py:run_extraction's
                "upload & extract now" path, ExtractionJob.extra_keywords) —
                matched alongside whatever the organisation already has
                saved, under a synthetic 'Custom' category. Never persisted
                back to the organisation's own KeywordCategory/Keyword rows.
        """
        self.media_root = Path(media_root)
        self.organisation = organisation
        extraction_type_normalized = (extraction_type or 'PR').strip()
        self.extraction_type = 'Ad' if extraction_type_normalized.lower() == 'ad' else 'PR'
        self.extra_keywords = [t for t in (extra_keywords or []) if t]

        # Load publishers from database
        self.publishers = self._load_publishers_from_db()

        # Load organization keywords (+ this run's ad-hoc ones, if any)
        self.keywords = self._load_keywords()
        self.all_keywords = self._flatten_keywords()

        # Load learned parameters from user feedback
        self.learned_params = self._load_learned_params()

        # Setup folders
        self.base_output = self.media_root / 'extractions' / self._sanitize_name(organisation.name)
        self.screenshots_folder = self.base_output / 'screenshots'
        self.reports_folder = self.base_output / 'reports'

        self.screenshots_folder.mkdir(parents=True, exist_ok=True)
        self.reports_folder.mkdir(parents=True, exist_ok=True)

        # Stats
        self.stats = {
            'files_processed': 0,
            'pages_scanned': 0,
            'articles_found': 0,
            'articles_grouped': 0,
            'screenshots_taken': 0,
            'reports_generated': 0
        }
    
    def _sanitize_name(self, name: str) -> str:
        """Convert name to safe folder name"""
        return re.sub(r'[^\w\s-]', '', name).lower().replace(' ', '_')
    
    def _load_publishers_from_db(self) -> Dict[str, Dict]:
        """Load all publishers from database"""
        from organisations.models import Publisher
        
        publishers_dict = {}
        for pub in Publisher.objects.all():
            publishers_dict[pub.name.lower()] = {
                'name': pub.name,
                'base_ave_rate': float(pub.base_ave_rate),
                'reach': pub.reach
            }
        return publishers_dict
    
    def _load_keywords(self) -> Dict[str, List[str]]:
        """Load keywords from database for this organization"""
        from organisations.models import Keyword
        
        keywords_dict = {}
        categories = self.organisation.keyword_categories.all()
        
        for category in categories:
            terms = [kw.term for kw in category.keywords.filter(is_active=True)]
            if terms:
                keywords_dict[category.name] = terms

        if self.extra_keywords:
            # Merges into an existing 'Custom' category name rather than
            # overwriting it, on the off chance the organisation already has
            # one of its own saved categories called exactly that.
            keywords_dict['Custom'] = keywords_dict.get('Custom', []) + self.extra_keywords

        return keywords_dict
    
    def _flatten_keywords(self) -> List[str]:
        """Flatten all keywords into a single list"""
        all_terms = []
        for terms in self.keywords.values():
            all_terms.extend(terms)
        return all_terms
    
    def _load_learned_params(self) -> Dict:
        """Load learned parameters accumulated from user feedback for this organisation."""
        try:
            from organisations.learning import LearningEngine
            return LearningEngine().get_learned_params(self.organisation)
        except Exception:
            return {'positive_words': [], 'negative_words': [], 'section_corrections': {}}

    def get_page_multiplier(self, page_num: int) -> float:
        """Get page position multiplier based on page number"""
        if page_num <= 2:
            return 2.0      # Front page
        elif page_num <= 5:
            return 1.5      # Early pages
        elif page_num <= 10:
            return 1.0      # Middle pages
        else:
            return 0.7      # Back pages

    def is_ad_page(self, text: str) -> bool:
        """Detect whether a page looks like an advertisement.

        2026-09-08: was a plain substring scan requiring just one hit — that
        flagged real PR articles as ads constantly. Two concrete false
        positives found on one 12-page real newspaper: 'advert' matched
        inside the masthead's own "Advertising: +267..." contact line
        (present on every page), and a page's genuine tax-law article got
        flagged too. Word-boundary matching stops the first (an 'advert'
        substring inside 'advertising' no longer counts), and requiring two
        distinct markers instead of one stops the second kind — a single
        incidental word like 'sale' inside otherwise-editorial prose is
        common; a real ad page reliably carries several of these together.
        """
        text_lower = text.lower()
        ad_markers = [
            'advertisement', 'advert', 'sponsored', 'paid advert', 'paid advertisement',
            'special offer', 'limited offer', 'buy now', 'order now', 'call now',
            'classified', 'subscription', 'offer ends', 'sale', 'discount', 'promoted'
        ]

        indicator_count = sum(
            1 for marker in ad_markers
            if re.search(r'\b' + re.escape(marker) + r'\b', text_lower)
        )
        return indicator_count >= 2

    def calculate_ave(self, publisher_name: str, page_num: int, sentiment: str) -> float:
        """
        Calculate Advertising Value Equivalency (AVE)
        Uses publisher data from database
        Formula: Base Rate × Page Multiplier × (Reach / 1000) × Sentiment Multiplier
        """
        publisher_lower = publisher_name.lower()
        
        # Get publisher from database
        publisher_data = self.publishers.get(publisher_lower)
        
        if publisher_data:
            base_rate = publisher_data['base_ave_rate']
            reach = publisher_data['reach']
        else:
            # Fallback for unknown publishers
            base_rate = 250.00
            reach = 5000
        
        page_multiplier = self.get_page_multiplier(page_num)
        reach_multiplier = reach / 1000
        sentiment_multiplier = {"Positive": 1.2, "Neutral": 1.0, "Negative": 0.8}
        
        ave = base_rate * page_multiplier * reach_multiplier * sentiment_multiplier.get(sentiment, 1.0)
        
        return round(ave, 2)
    
    def extract_publisher(self, text: str, filename: str = "") -> Tuple[str, int]:
        """Extract publisher name from text, matching against database publishers"""
        text_lower = text.lower()
        
        # Try to match against database publishers
        for pub_key, pub_data in self.publishers.items():
            if pub_key in text_lower:
                return pub_data['name'], pub_data['reach']
        
        # Check filename as fallback
        filename_lower = filename.lower()
        for pub_key, pub_data in self.publishers.items():
            if pub_key in filename_lower:
                return pub_data['name'], pub_data['reach']
        
        return "Unknown Publisher", 5000
    
    def extract_date_from_page(self, page_text: str) -> str:
        """Enhanced date extraction with better pattern matching"""
        lines = page_text.strip().split('\n')

        # Extended month mappings
        months = {
            'JANUARY': 'Jan', 'FEBRUARY': 'Feb', 'MARCH': 'Mar', 'APRIL': 'Apr',
            'MAY': 'May', 'JUNE': 'Jun', 'JULY': 'Jul', 'AUGUST': 'Aug',
            'SEPTEMBER': 'Sep', 'OCTOBER': 'Oct', 'NOVEMBER': 'Nov', 'DECEMBER': 'Dec',
            'JAN': 'Jan', 'FEB': 'Feb', 'MAR': 'Mar', 'APR': 'Apr', 'JUN': 'Jun',
            'JUL': 'Jul', 'AUG': 'Aug', 'SEP': 'Sep', 'OCT': 'Oct', 'NOV': 'Nov', 'DEC': 'Dec'
        }

        # Check first 25 lines for date patterns
        for line in lines[:25]:
            line = line.strip()
            if not line:
                continue

            # Pattern 1: "17 FEBRUARY 2026" or "18 Feb 2026"
            match1 = re.search(r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', line, re.IGNORECASE)
            if match1:
                day = int(match1.group(1))
                month_name = match1.group(2).upper()
                year = match1.group(3)
                if month_name in months and 1 <= day <= 31:
                    return f"{day:02d} {months[month_name]} {year}"

            # Pattern 2: "February 17, 2026" or "Feb 18 2026"
            match2 = re.search(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', line, re.IGNORECASE)
            if match2:
                month_name = match2.group(1).upper()
                day = int(match2.group(2))
                year = match2.group(3)
                if month_name in months and 1 <= day <= 31:
                    return f"{day:02d} {months[month_name]} {year}"

            # Pattern 3: "17/02/2026" or "17-02-2026"
            match3 = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', line)
            if match3:
                day = int(match3.group(1))
                month_num = int(match3.group(2))
                year = match3.group(3)
                if 1 <= month_num <= 12 and 1 <= day <= 31 and year.startswith('20'):
                    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                    return f"{day:02d} {month_names[month_num-1]} {year}"

        # Fallback: use current date
        return datetime.now().strftime("%d %b %Y")
    
    def extract_article_title_by_font_size(self, file_path: str, page_num: int, keyword_positions: List[Tuple]) -> str:
        """
        Extract article title by analyzing font sizes around keyword positions
        Looks for the largest font text near where keywords were found
        """
        try:
            # Handle both PDF and image files
            if str(file_path).lower().endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp')):
                return self.extract_title_from_image(file_path, keyword_positions)

            # PDF processing
            with pdfplumber.open(file_path) as pdf:
                if page_num > len(pdf.pages):
                    return f"Article - Page {page_num}"

                page = pdf.pages[page_num - 1]
                chars = page.chars

                if not chars:
                    return self.extract_article_title_fallback("", page_num)

                # Find text near keyword positions
                keyword_areas = []
                for start_pos, end_pos in keyword_positions:
                    # Look for text within 100 units of keyword positions
                    nearby_chars = []
                    for char in chars:
                        char_center = (char.get('x0', 0) + char.get('x1', 0)) / 2
                        if abs(char_center - start_pos) < 100 or abs(char_center - end_pos) < 100:
                            nearby_chars.append(char)

                    if nearby_chars:
                        keyword_areas.extend(nearby_chars)

                # If no text near keywords, fall back to largest font analysis
                if not keyword_areas:
                    keyword_areas = chars

                # Group by font size and position
                font_sizes = {}
                for char in keyword_areas:
                    size = round(char.get('size', 0), 1)
                    if size > 8:  # Ignore very small text
                        font_sizes[size] = font_sizes.get(size, 0) + 1

                if not font_sizes:
                    return self.extract_article_title_fallback("", page_num)

                # Find largest font size
                largest_font_size = max(font_sizes.keys())

                # Get all text with largest font size
                title_chars = [char for char in keyword_areas if round(char.get('size', 0), 1) == largest_font_size]

                # Group by vertical position (same line)
                lines = {}
                for char in title_chars:
                    y_pos = round(char.get('y0', 0), 1)
                    if y_pos not in lines:
                        lines[y_pos] = []
                    lines[y_pos].append(char)

                # Sort each line by x position and combine text
                title_candidates = []
                for y_pos in sorted(lines.keys(), reverse=True):  # Start from top
                    line_chars = sorted(lines[y_pos], key=lambda c: c.get('x0', 0))
                    line_text = ''.join([c.get('text', '') for c in line_chars]).strip()

                    # Filter out short lines and metadata
                    if len(line_text) > 10 and len(line_text) < 200:
                        skip_words = ['page', 'www', '.com', 'http', 'tel:', 'email:',
                                     'advertisement', 'classified', 'subscription']
                        if not any(skip in line_text.lower() for skip in skip_words):
                            title_candidates.append(line_text)

                if title_candidates:
                    # Return the first valid title candidate
                    for candidate in title_candidates:
                        if len(candidate.split()) >= 3:  # At least 3 words
                            return candidate[:150]

        except Exception as e:
            print(f"Font size title extraction failed: {e}")

        return self.extract_article_title_fallback("", page_num)

    def extract_title_from_image(self, image_path: str, keyword_positions: List[Tuple]) -> str:
        """Extract title from image using OCR"""
        try:
            # Use OCR to extract text from image
            image = PILImage.open(image_path)
            text = pytesseract.image_to_string(image)

            # Look for title patterns in OCR text
            return self.extract_article_title_fallback(text, 1)
        except Exception as e:
            print(f"Image title extraction failed: {e}")
            return "Article from Image"

    def extract_article_title_fallback(self, text: str, page_num: int) -> str:
        """Fallback title extraction from text"""
        if not text:
            return f"Article - Page {page_num}"

        lines = text.strip().split('\n')

        skip_words = ['page', 'vol', 'issue', 'www.', '.com', 'published',
                     'gazette', 'standard', 'monitor', 'advertisement']

        for line in lines[:25]:
            line = line.strip()
            if len(line) < 15 or len(line) > 150:
                continue

            if any(skip in line.lower() for skip in skip_words):
                continue

            # Check if it looks like a headline
            is_all_caps = line.isupper() and len(line.split()) >= 3
            has_title_case = line[0].isupper() and any(c.isupper() for c in line[1:20]) and len(line.split()) >= 3
            has_keywords = any(kw.lower() in line.lower() for kw in self.all_keywords[:10])

            if is_all_caps or has_title_case or has_keywords:
                title = re.sub(r'^#+\s*', '', line)
                title = re.sub(r'\s+', ' ', title)
                title = title.strip()
                if len(title) > 15:
                    return title[:200]

        return f"Article - Page {page_num}"
    
    def extract_author(self, text: str) -> str:
        """Extract author name from text"""
        known_authors = [
            "GAZETTE REPORTER", "Staff Reporter", "Bongani Malunga", 
            "The Telegraph Reporter", "KAGO JOROMEA", "GRILLREPORTER"
        ]
        
        for author in known_authors:
            if author.lower() in text.lower():
                return author.upper()
        
        pattern = r'By\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip().upper()
        
        return "STAFF REPORTER"
    
    def extract_section(self, text: str) -> str:
        """Extract section from newspaper content or return default"""
        text_lower = text.lower()
        
        # Common newspaper section names to look for
        known_sections = [
            ('business', 'Business'),
            ('sports', 'Sports'),
            ('entertainment', 'Entertainment'),
            ('politics', 'Politics'),
            ('opinion', 'Opinion'),
            ('classified', 'Classifieds'),
            ('advertisement', 'Advertisements'),
            ('advert', 'Advertisements'),
            ('news', 'News'),
            ('world', 'World'),
            ('local', 'Local'),
            ('features', 'Features'),
            ('health', 'Health'),
            ('technology', 'Technology'),
            ('finance', 'Finance'),
        ]
        
        corrections = self.learned_params.get('section_corrections', {})

        # Search for section names in the text
        for keyword, section_name in known_sections:
            if keyword in text_lower:
                return corrections.get(section_name, section_name)

        # Fallback: use default based on extraction type
        default = "Advertisements" if self.extraction_type == 'Ad' else "Business"
        return corrections.get(default, default)
    
    def analyze_sentiment(self, text: str) -> str:
        """Analyze sentiment of the article"""
        positive_words = [
            'expansion', 'milestone', 'breakthrough', 'success', 'growth',
            'positive', 'progress', 'strong', 'excellent', 'achievement'
        ] + self.learned_params.get('positive_words', [])
        negative_words = [
            'setback', 'decline', 'fall', 'drop', 'struggles', 'crisis',
            'warning', 'concern', 'delay', 'problem', 'loss', 'risk'
        ] + self.learned_params.get('negative_words', [])
        
        pos_count = sum(1 for w in positive_words if w in text.lower())
        neg_count = sum(1 for w in negative_words if w in text.lower())
        
        if pos_count > neg_count:
            return "Positive"
        elif neg_count > pos_count:
            return "Negative"
        return "Neutral"
    
    def capture_screenshot(self, pdf_path: str, page_num: int, title: str) -> str:
        """Capture screenshot of a page"""
        try:
            safe_title = re.sub(r'[^\w\s-]', '', title)[:50]
            safe_title = re.sub(r'[-\s]+', '_', safe_title)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{safe_title}_p{page_num}_{timestamp}.png"
            filepath = self.screenshots_folder / filename
            
            with pdfplumber.open(pdf_path) as pdf:
                if page_num <= len(pdf.pages):
                    page = pdf.pages[page_num - 1]
                    page.to_image(resolution=120).save(str(filepath), format="PNG")
                    self.stats['screenshots_taken'] += 1
                    return str(filepath)
        except Exception as e:
            print(f"  Screenshot failed: {e}")
        return ""
    
    def find_keyword_positions(self, text: str) -> List[Tuple[int, int]]:
        """Find positions of keywords in text for title extraction"""
        positions = []
        text_lower = text.lower()

        for keyword in self.all_keywords:
            keyword_lower = keyword.lower()
            start = 0
            while True:
                pos = text_lower.find(keyword_lower, start)
                if pos == -1:
                    break
                end_pos = pos + len(keyword)
                positions.append((pos, end_pos))
                start = end_pos

        return positions

    # Small function-word list for _keyword_found's multi-word fallback —
    # "Minister of Energy" should match on "minister"/"energy" both being
    # present, not on "of" coincidentally appearing (it always does).
    _STOPWORDS = {'of', 'the', 'and', 'a', 'an', 'in', 'for', 'to', 'on', 'at', 'is', 'are'}

    def _word_present(self, word: str, text_words: set, words_by_length: dict) -> bool:
        """Whether one significant word from a keyword counts as present on
        the page — exact token match, or (5+ letters only) a close
        spelling/inflection variant via stdlib difflib. The same check
        covers plurals ("ministers"~"minister") and genuine typos
        ("Minster"~"Minister") for free — both are just a one-character
        edit apart, which is exactly what a distance cutoff catches,
        without needing plural handling as a separate case.

        The 5-letter floor (raised from an initial 4 after testing against
        a real document) is load-bearing, not arbitrary: at 4 letters,
        "meat" was fuzzy-matching "meant" — same edit distance and ratio as
        the "LGBT"~"LGBTQ" case this was built for, just with a
        catastrophically different outcome, since "meant" is an ordinary
        word likely to appear in unrelated text. There is no ratio/cutoff
        that separates "a plausible near-miss spelling" from "a genuinely
        different short word" in general — one more letter of length is
        what actually helps, since a single-character edit is a smaller
        fraction of a longer word, so genuine unrelated collisions get
        rarer. The trade-off: a 4-letter keyword (an acronym, "LGBT" itself)
        only ever matches exactly now — accepted deliberately, since a
        false "match" that isn't really about the searched term is worse
        for a monitoring tool than a missed near-miss.
        """
        if word in text_words:
            return True
        if len(word) < 5:
            return False
        # A genuine near-miss is always close in length too (a typo/plural
        # is a one-or-two-character edit) — narrowing difflib's candidate
        # pool to nearby lengths instead of every unique word on the page
        # is what keeps this affordable on a long, keyword-heavy document
        # (difflib's own cost scales with candidate-set size).
        candidates = set()
        for length in range(len(word) - 2, len(word) + 3):
            candidates |= words_by_length.get(length, set())
        if not candidates:
            return False
        return bool(difflib.get_close_matches(word, candidates, n=1, cutoff=0.82))

    def _keyword_found(self, keyword: str, text_lower: str, text_words: set, words_by_length: dict) -> bool:
        """Whether `keyword` counts as matched on this page.

        2026-09-08: was a single `keyword.lower() in text_lower` check —
        exact phrase only. Real searches kept coming back empty for
        reasons that weren't really "not mentioned": "Minister of Energy"
        against a page that says "Ministers Bogolo Kenewendo... Energy
        Security" (same topic, plural + different phrasing — the exact
        phrase genuinely isn't there), or "LGBTQ" against a page that only
        ever writes "LGBT". Exact phrase match is still tried first and is
        what decides precedence when several forms would all match — the
        fallback below only runs when that fails: split into significant
        words (stopwords like "of"/"the" dropped) and require every one of
        them present *somewhere* on the page (not necessarily adjacent or
        in order), each via _word_present's exact-or-close-variant check.
        A single-word keyword is just the one-word case of the same rule.
        Short words/acronyms (under 5 letters, e.g. "BMC", "meat") never fuzzy-match
        — that class of miss (an abbreviation for a full name) needs real
        abbreviation knowledge, not spelling tolerance, and is out of scope
        here; a short word still has to appear exactly.
        """
        keyword_lower = keyword.lower()
        if keyword_lower in text_lower:
            return True

        words = [w for w in re.findall(r"[a-z0-9']+", keyword_lower) if w not in self._STOPWORDS]
        if not words:
            return False

        return all(self._word_present(w, text_words, words_by_length) for w in words)

    def check_page_for_organization(self, text: str) -> Tuple[bool, List[str], Dict[str, List[str]]]:
        """Check if page mentions the organization and whether it matches extraction type."""
        text_lower = text.lower()
        # Computed once per page (not per keyword) — _keyword_found's
        # fallbacks both need these, and rebuilding per keyword would
        # multiply the cost by however many keywords exist. words_by_length
        # buckets the same set by word length, for _word_present's fuzzy
        # candidate narrowing.
        text_words = set(re.findall(r"[a-z0-9']+", text_lower))
        words_by_length = {}
        for w in text_words:
            words_by_length.setdefault(len(w), set()).add(w)
        matches = []
        categorized = {cat: [] for cat in self.keywords.keys()}

        for category, terms in self.keywords.items():
            for term in terms:
                if self._keyword_found(term, text_lower, text_words, words_by_length):
                    categorized[category].append(term)
                    matches.append(term)

        has_keyword_match = len(matches) > 0
        is_ad_page = self.is_ad_page(text)

        if self.extraction_type == 'PR':
            has_match = has_keyword_match and not is_ad_page
        else:
            has_match = has_keyword_match and is_ad_page

        return has_match, list(set(matches)), categorized

    def group_continuous_articles(self, articles: List[Dict]) -> List[List[Dict]]:
        """Group articles that continue across multiple pages"""
        if not articles:
            return []

        # Sort by file and page
        articles.sort(key=lambda x: (x['source_file'], x['page']))

        grouped = []
        current_group = [articles[0]]

        for i in range(1, len(articles)):
            prev = articles[i-1]
            curr = articles[i]

            # Check if articles can be grouped
            same_file = curr['source_file'] == prev['source_file']
            consecutive_pages = curr['page'] == prev['page'] + 1
            similar_titles = self.titles_are_similar(prev['title'], curr['title'])

            if same_file and (consecutive_pages or similar_titles):
                current_group.append(curr)
            else:
                grouped.append(current_group)
                current_group = [curr]

        grouped.append(current_group)
        return grouped

    def titles_are_similar(self, title1: str, title2: str) -> bool:
        """Check if two titles are similar (continuation of same article)"""
        if not title1 or not title2:
            return False

        # Remove common words and compare
        common_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}

        words1 = set(title1.lower().split()) - common_words
        words2 = set(title2.lower().split()) - common_words

        # If significant overlap in words, consider them similar
        overlap = len(words1 & words2)
        total_unique = len(words1 | words2)

        return overlap > 0 and (overlap / total_unique) > 0.3

    def extract_text_from_image(self, image_path: str) -> str:
        """Extract text from image using OCR"""
        try:
            # Preprocessing for better OCR
            image = cv2.imread(str(image_path))
            if image is None:
                pil_image = PILImage.open(image_path)
                image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)

            # Convert to grayscale
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            # Apply thresholding to get better contrast
            _, threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # OCR the image
            text = pytesseract.image_to_string(threshold)

            return text
        except Exception as e:
            print(f"OCR failed for {image_path}: {e}")
            return ""

    def _ocr_pdf_page(self, page) -> str:
        """OCR one PDF page that pdfplumber found no text layer on — a
        scanned/photographed page saved as a PDF (e.g. a phone photo of a
        newspaper, or a WhatsApp-forwarded scan), as opposed to a real text
        PDF. Same preprocessing as extract_text_from_image, just fed a
        rendered page image (already in memory, via pdfplumber's own
        `page.to_image()` — also how capture_screenshot renders a page)
        instead of a file already on disk.

        Meaningfully slower than native text extraction — real OCR per
        page, not a string read — so extract_articles_from_pdf only calls
        this when the page's own text layer came back empty/near-empty,
        never on every page.
        """
        try:
            pil_image = page.to_image(resolution=200).original.convert('RGB')
            image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            _, threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            return pytesseract.image_to_string(threshold)
        except Exception as e:
            print(f"  PDF page OCR failed (page {page.page_number}): {e}")
            return ""

    def process_file(self, file_path: str) -> List[Dict]:
        """Process a single file (PDF or image) for articles"""
        file_path = Path(file_path)
        articles = []

        try:
            if file_path.suffix.lower() in ['.png', '.jpg', '.jpeg', '.tiff', '.bmp']:
                # Process image file
                text = self.extract_text_from_image(file_path)
                if text:
                    article = self.process_image_text(text, file_path)
                    if article:
                        articles.append(article)

            elif file_path.suffix.lower() == '.pdf':
                # Process PDF file
                articles = self.extract_articles_from_pdf(file_path)

            self.stats['files_processed'] += 1

        except Exception as e:
            print(f"Error processing file {file_path}: {e}")

        return articles

    def process_image_text(self, text: str, image_path: Path) -> Optional[Dict]:
        """Process text extracted from image"""
        has_match, matches, categorized = self.check_page_for_organization(text)

        if not has_match:
            return None

        # Find keyword positions for title extraction
        keyword_positions = self.find_keyword_positions(text)

        # Extract article data
        title = self.extract_article_title_by_font_size(str(image_path), 1, keyword_positions)
        author = self.extract_author(text)
        sentiment = self.analyze_sentiment(text)
        article_date = self.extract_date_from_page(text)
        section = self.extract_section(text)

        # Get publisher info
        publisher_name, reach = self.extract_publisher(text, image_path.name)

        # Calculate AVE
        ave = self.calculate_ave(publisher_name, 1, sentiment)

        # Create screenshot (copy of original image)
        screenshot_path = self.capture_image_screenshot(str(image_path), title)

        # Create PDF report
        report_path = self.create_article_report({
            "article_title": title,
            "publisher": publisher_name,
            "section": section,
            "date": article_date,
            "page": 1,
            "reach": reach,
            "ave": ave,
            "author": author,
            "source_file": image_path.name,
        }, screenshot_path)

        article = {
            "title": title,
            "publisher_name": publisher_name,
            "section": section,
            "publication_date": article_date,
            "page": 1,
            "pages": [1],
            "reach": reach,
            "ave": ave,
            "author": author,
            "sentiment": sentiment,
            "extraction_type": self.extraction_type,
            "keywords_matched": ", ".join(matches),
            "keyword_categories": categorized,
            "source_file": image_path.name,
            "screenshot_path": screenshot_path,
            "report_path": report_path,
        }

        return article

    def capture_image_screenshot(self, image_path: str, title: str) -> str:
        """Capture screenshot for image files (copy the image)"""
        try:
            safe_title = re.sub(r'[^\w\s-]', '', title)[:50]
            safe_title = re.sub(r'[-\s]+', '_', safe_title)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{safe_title}_img_{timestamp}.png"
            output_path = self.screenshots_folder / filename

            # Copy the image
            import shutil
            shutil.copy2(image_path, output_path)

            self.stats['screenshots_taken'] += 1
            return str(output_path)
        except Exception as e:
            print(f"Image screenshot failed: {e}")
        return ""
    
    def create_article_report(self, article_data: Dict, screenshot_paths) -> str:
        """Create PDF report for an article; screenshot_paths may be a list or single string."""
        if isinstance(screenshot_paths, str):
            screenshot_paths = [screenshot_paths] if screenshot_paths else []

        safe_filename = Path(article_data['source_file']).stem
        pages_info = article_data.get('pages', [article_data['page']])
        total_parts = max(len(pages_info), len(screenshot_paths)) if screenshot_paths else len(pages_info)

        output_filename = f"{safe_filename}_p{pages_info[0]}_report.pdf"
        output_file = self.reports_folder / output_filename

        doc = SimpleDocTemplate(str(output_file), pagesize=A4,
                                topMargin=1.5*cm, bottomMargin=1.5*cm,
                                leftMargin=1.5*cm, rightMargin=1.5*cm)

        styles = getSampleStyleSheet()
        param_style = ParagraphStyle('ParamStyle', parent=styles['Normal'], fontSize=8, leading=10)
        footer_style = ParagraphStyle('FooterStyle', parent=styles['Normal'], fontSize=7,
                                       alignment=TA_CENTER, textColor=colors.grey)

        story = []

        # Part indicator (always show, even for single page)
        story.append(Paragraph(f"Part: 1 of {total_parts}", param_style))
        story.append(Spacer(1, 4))

        pub_section = f"{article_data['publisher']} - {article_data['section']}"

        # Two-column parameters
        data1 = [[
            Paragraph(f"<b>Publication:</b> {pub_section}", param_style),
            Paragraph(f"<b>Page:</b> {', '.join(map(str, pages_info))}", param_style)
        ]]
        table1 = Table(data1, colWidths=[8*cm, 8*cm])
        table1.setStyle(TableStyle([
            ('ALIGN', (0,0), (0,0), 'LEFT'),
            ('ALIGN', (1,0), (1,0), 'RIGHT'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('GRID', (0,0), (-1,-1), 0, colors.white),
        ]))
        story.append(table1)

        data2 = [[
            Paragraph(f"<b>Title:</b> {article_data['article_title']}", param_style),
            Paragraph(f"<b>Reach:</b> {article_data['reach']:,}", param_style)
        ]]
        table2 = Table(data2, colWidths=[8*cm, 8*cm])
        table2.setStyle(TableStyle([
            ('ALIGN', (0,0), (0,0), 'LEFT'),
            ('ALIGN', (1,0), (1,0), 'RIGHT'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('GRID', (0,0), (-1,-1), 0, colors.white),
        ]))
        story.append(table2)

        data3 = [[
            Paragraph(f"<b>Publish date:</b> {article_data['date']}", param_style),
            Paragraph(f"<b>AVE:</b> P {article_data['ave']:,.2f}", param_style)
        ]]
        table3 = Table(data3, colWidths=[8*cm, 8*cm])
        table3.setStyle(TableStyle([
            ('ALIGN', (0,0), (0,0), 'LEFT'),
            ('ALIGN', (1,0), (1,0), 'RIGHT'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('GRID', (0,0), (-1,-1), 0, colors.white),
        ]))
        story.append(table3)

        data4 = [[
            Paragraph(f"<b>Author:</b> {article_data['author']}", param_style),
            Paragraph("", param_style)
        ]]
        table4 = Table(data4, colWidths=[8*cm, 8*cm])
        table4.setStyle(TableStyle([
            ('ALIGN', (0,0), (0,0), 'LEFT'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('GRID', (0,0), (-1,-1), 0, colors.white),
        ]))
        story.append(table4)

        story.append(Spacer(1, 8))

        # Add screenshots — one per page, with a page break between each
        for i, sspath in enumerate(screenshot_paths):
            if i > 0:
                story.append(PageBreak())
                story.append(Paragraph(f"Part: {i + 1} of {total_parts}", param_style))
                story.append(Spacer(1, 8))
            if sspath and Path(sspath).exists():
                try:
                    story.append(Image(sspath, width=16*cm, height=22*cm))
                except Exception as e:
                    story.append(Paragraph(f"Screenshot not available: {e}", param_style))
            else:
                page_label = pages_info[i] if i < len(pages_info) else i + 1
                story.append(Paragraph(f"Screenshot not available for page {page_label}.", param_style))

        # DALRO Footer
        story.append(Spacer(1, 15))
        story.append(Paragraph('Redistribution or re-sale is not allowed.', footer_style))

        doc.build(story)
        self.stats['reports_generated'] += 1

        return str(output_file)
    
    def extract_articles_from_pdf(self, pdf_path: str) -> List[Dict]:
        """Extract all articles from a PDF with continuation detection"""
        articles = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                filename = Path(pdf_path).name

                # First pass: find all pages with matches
                candidate_pages = []
                for page_num, page in enumerate(pdf.pages, 1):
                    page_text = page.extract_text()
                    if not page_text or len(page_text.strip()) < 20:
                        # No text layer (or next to nothing — a stray page
                        # number) — this is a scanned/photographed page,
                        # not a real text PDF. Try OCR before giving up on
                        # it entirely; a page that's genuinely blank still
                        # comes back empty from this and gets skipped below
                        # exactly as before.
                        ocr_text = self._ocr_pdf_page(page)
                        if ocr_text and len(ocr_text.strip()) >= len(page_text or ''):
                            page_text = ocr_text
                    if not page_text or not page_text.strip():
                        continue

                    has_match, matches, categorized = self.check_page_for_organization(page_text)

                    if has_match:
                        self.stats['pages_scanned'] += 1

                        # Find keyword positions for title extraction
                        keyword_positions = self.find_keyword_positions(page_text)

                        # Extract basic info
                        author = self.extract_author(page_text)
                        sentiment = self.analyze_sentiment(page_text)
                        article_date = self.extract_date_from_page(page_text)
                        section = self.extract_section(page_text)

                        # Get publisher info
                        publisher_name, reach = self.extract_publisher(page_text, filename)

                        candidate_pages.append({
                            'page_num': page_num,
                            'text': page_text,
                            'matches': matches,
                            'categorized': categorized,
                            'keyword_positions': keyword_positions,
                            'author': author,
                            'sentiment': sentiment,
                            'date': article_date,
                            'publisher_name': publisher_name,
                            'reach': reach,
                            'section': section
                        })

                # Group continuous articles
                if candidate_pages:
                    article_groups = self.group_continuous_pages_from_candidates(candidate_pages)

                    for group in article_groups:
                        article = self.process_article_group(group, pdf_path)
                        if article:
                            articles.append(article)
                            self.stats['articles_grouped'] += 1

        except Exception as e:
            print(f"Error processing PDF {pdf_path}: {e}")
            raise e

        return articles

    def group_continuous_pages_from_candidates(self, candidates: List[Dict]) -> List[List[Dict]]:
        """Group candidate pages into continuous articles"""
        if not candidates:
            return []

        # Sort by page number
        candidates.sort(key=lambda x: x['page_num'])

        grouped = []
        current_group = [candidates[0]]

        for i in range(1, len(candidates)):
            prev = candidates[i-1]
            curr = candidates[i]

            # Check if consecutive pages
            consecutive = curr['page_num'] == prev['page_num'] + 1

            # Check if same article (similar keywords/authors)
            same_article = (
                set(prev['matches']) & set(curr['matches']) or  # Shared keywords
                prev['author'] == curr['author']  # Same author
            )

            if consecutive and same_article:
                current_group.append(curr)
            else:
                grouped.append(current_group)
                current_group = [curr]

        grouped.append(current_group)
        return grouped

    def process_article_group(self, group: List[Dict], pdf_path: str) -> Optional[Dict]:
        """Process a group of continuous pages as one article"""
        if not group:
            return None

        first_page = group[0]
        all_pages = [p['page_num'] for p in group]
        all_text = ' '.join([p['text'] for p in group])
        all_matches = list(set([m for p in group for m in p['matches']]))

        # Use first page for most metadata, but combine text for title extraction
        combined_keyword_positions = []
        for page in group:
            combined_keyword_positions.extend(page['keyword_positions'])

        # Extract title using font size analysis from first page
        title = self.extract_article_title_by_font_size(pdf_path, first_page['page_num'], combined_keyword_positions)

        # Use best metadata from the group
        best_author = first_page['author']
        best_sentiment = self.analyze_sentiment(all_text)  # Analyze combined text
        best_date = first_page['date']

        # Calculate AVE based on first page
        ave = self.calculate_ave(first_page['publisher_name'], first_page['page_num'], best_sentiment)

        # Capture screenshots of every page the article spans
        screenshot_paths = []
        for page_data in group:
            path = self.capture_screenshot(str(pdf_path), page_data['page_num'], title)
            if path:
                screenshot_paths.append(path)

        # Create PDF report containing all page screenshots
        report_path = self.create_article_report({
            "article_title": title,
            "publisher": first_page['publisher_name'],
            "section": first_page['section'],
            "date": best_date,
            "page": first_page['page_num'],
            "pages": all_pages,
            "reach": first_page['reach'],
            "ave": ave,
            "author": best_author,
            "source_file": Path(pdf_path).name,
        }, screenshot_paths)

        article = {
            "title": title,
            "publisher_name": first_page['publisher_name'],
            "section": first_page['section'],
            "publication_date": best_date,
            "page": first_page['page_num'],
            "pages": all_pages,
            "reach": first_page['reach'],
            "ave": ave,
            "author": best_author,
            "sentiment": best_sentiment,
            "extraction_type": self.extraction_type,
            "keywords_matched": ", ".join(all_matches),
            "keyword_categories": first_page['categorized'],
            "source_file": Path(pdf_path).name,
            "screenshot_path": screenshot_paths[0] if screenshot_paths else "",
            "report_path": report_path,
        }

        self.stats['articles_found'] += 1
        print(f"    Article: {title[:50]}... (Pages: {', '.join(map(str, all_pages))})")

        return article
    
    def process_multiple_files(self, file_paths: List[str]) -> List[Dict]:
        """Process multiple files (PDFs and images)"""
        all_articles = []

        print(f"\n🔍 Processing {len(file_paths)} files for {self.organisation.name}...")

        for file_path in file_paths:
            try:
                articles = self.process_file(file_path)
                all_articles.extend(articles)
            except Exception as e:
                print(f"❌ Failed to process {file_path}: {e}")

        # Group articles that may span multiple files (for continuation detection)
        if all_articles:
            all_articles = self.group_continuous_articles(all_articles)

        print(f"\n✅ Found {len(all_articles)} articles total")
        return all_articles

    def get_stats(self) -> Dict:
        """Get processing statistics"""
        return self.stats.copy()


# Backward compatibility - keep the old class name as an alias
ArticleExtractor = EnhancedArticleExtractor