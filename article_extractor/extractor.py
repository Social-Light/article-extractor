"""
Social Light - Newspaper Article Extractor
Fixed: 
- Processes all pages with keywords.
- Extracts title from the article block (not whole page).
- Follows "Continues to page X" markers.
- Matches publisher from database or filename.
"""

import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Set
import pdfplumber
import fitz  # PyMuPDF
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER


class ArticleExtractor:
    def __init__(self, media_root: str, organisation):
        self.media_root = Path(media_root)
        self.organisation = organisation
        self.publishers = self._load_publishers_from_db()
        self.keywords = self._load_keywords()
        self.all_keywords = self._flatten_keywords()

        self.base_output = self.media_root / 'extractions' / self._sanitize_name(organisation.name)
        self.screenshots_folder = self.base_output / 'screenshots'
        self.reports_folder = self.base_output / 'reports'
        self.screenshots_folder.mkdir(parents=True, exist_ok=True)
        self.reports_folder.mkdir(parents=True, exist_ok=True)

        self.stats = {'pages_scanned': 0, 'articles_found': 0, 'screenshots_taken': 0, 'reports_generated': 0}

    def _sanitize_name(self, name: str) -> str:
        return re.sub(r'[^\w\s-]', '', name).lower().replace(' ', '_')

    def _load_publishers_from_db(self) -> Dict:
        from organisations.models import Publisher
        return {pub.name.lower(): {'name': pub.name, 'base_ave_rate': float(pub.base_ave_rate), 'reach': pub.reach}
                for pub in Publisher.objects.all()}

    def _load_keywords(self) -> Dict:
        from organisations.models import Keyword
        keywords = {}
        for cat in self.organisation.keyword_categories.all():
            terms = [kw.term for kw in cat.keywords.filter(is_active=True)]
            if terms:
                keywords[cat.name] = terms
        return keywords

    def _flatten_keywords(self) -> List[str]:
        return [term for terms in self.keywords.values() for term in terms]

    def get_page_multiplier(self, page_num: int) -> float:
        if page_num <= 2: return 2.0
        elif page_num <= 5: return 1.5
        elif page_num <= 10: return 1.0
        else: return 0.7

    def calculate_ave(self, publisher_name: str, page_num: int, sentiment: str) -> float:
        pub_data = self.publishers.get(publisher_name.lower())
        base_rate = pub_data['base_ave_rate'] if pub_data else 250.00
        reach = pub_data['reach'] if pub_data else 5000
        page_mult = self.get_page_multiplier(page_num)
        reach_mult = reach / 1000
        sent_mult = {"Positive": 1.2, "Neutral": 1.0, "Negative": 0.8}
        return round(base_rate * page_mult * reach_mult * sent_mult.get(sentiment, 1.0), 2)

    def extract_publisher(self, text: str, filename: str = "") -> Tuple[str, int]:
        # First try to match against database publishers by name in text
        for key, data in self.publishers.items():
            if key in text.lower():
                return data['name'], data['reach']
        # Then try filename
        filename_lower = filename.lower()
        for key, data in self.publishers.items():
            if key in filename_lower:
                return data['name'], data['reach']
        # If still unknown, return default
        return "Unknown Publisher", 5000

    def extract_date_from_text(self, text: str) -> str:
        months = {'JANUARY':'Jan','FEBRUARY':'Feb','MARCH':'Mar','APRIL':'Apr','MAY':'May','JUNE':'Jun',
                  'JULY':'Jul','AUGUST':'Aug','SEPTEMBER':'Sep','OCTOBER':'Oct','NOVEMBER':'Nov','DECEMBER':'Dec'}
        for line in text.split('\n')[:30]:
            match = re.search(r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', line, re.IGNORECASE)
            if match:
                day, month_name, year = match.groups()
                month = months.get(month_name.upper(), month_name[:3])
                return f"{int(day):02d} {month} {year}"
        return datetime.now().strftime("%d %b %Y")

    def get_article_block_around_keyword(self, pdf_path: str, page_num: int, keyword_pos_y: float) -> Tuple[str, str, float, float]:
        """
        Extract the article block (text and title) that contains the keyword.
        Returns (block_text, title_text, block_y_start, block_y_end)
        """
        try:
            doc = fitz.open(pdf_path)
            page = doc[page_num - 1]
            blocks = page.get_text("dict")
            # Find the block that contains the keyword
            target_block = None
            for block in blocks.get("blocks", []):
                block_text = ""
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        block_text += span.get("text", "") + " "
                if any(kw.lower() in block_text.lower() for kw in self.all_keywords):
                    target_block = block
                    break
            if target_block:
                # Extract full text
                block_text = ""
                for line in target_block.get("lines", []):
                    for span in line.get("spans", []):
                        block_text += span.get("text", "") + " "
                # Find title: largest font within this block
                max_size = 0
                title = ""
                for line in target_block.get("lines", []):
                    for span in line.get("spans", []):
                        size = span.get("size", 0)
                        text = span.get("text", "").strip()
                        if size > max_size and len(text) > 10:
                            max_size = size
                            title = text
                # Get y range
                bbox = target_block.get("bbox", [0,0,0,0])
                y_start = bbox[1]
                y_end = bbox[3]
                doc.close()
                return block_text, title, y_start, y_end
            doc.close()
        except Exception as e:
            print(f"    Block extraction error: {e}")
        return "", "", 0, 0

    def find_article_pages(self, pdf, start_page: int, start_block_y: float, total_pages: int) -> List[int]:
        """Follow 'Continues to page X' markers to find all pages of the article."""
        pages = [start_page]
        current_page = start_page
        while current_page <= total_pages:
            # Get text of current page
            page_text = pdf.pages[current_page-1].extract_text() or ""
            # Look for continuation marker at the bottom of the page
            bottom_text = page_text[-800:] if len(page_text) > 800 else page_text
            match = re.search(r'continues?\s+to\s+page\s+(\d+)', bottom_text, re.IGNORECASE)
            if match:
                next_page = int(match.group(1))
                if next_page > current_page and next_page <= total_pages:
                    pages.append(next_page)
                    current_page = next_page
                    continue
            # Also check next page for "from page X"
            if current_page < total_pages:
                next_text = pdf.pages[current_page].extract_text() or ""
                top_text = next_text[:800]
                match_from = re.search(r'from\s+page\s+(\d+)', top_text, re.IGNORECASE)
                if match_from and int(match_from.group(1)) == current_page:
                    pages.append(current_page + 1)
                    current_page += 1
                    continue
            break
        return pages

    def capture_screenshot(self, pdf_path: str, page_num: int, title: str) -> str:
        safe_title = re.sub(r'[^\w\s-]', '', title)[:50].replace(' ', '_')
        filename = f"{safe_title}_p{page_num}.png"
        filepath = self.screenshots_folder / filename
        if filepath.exists():
            return str(filepath)
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if page_num <= len(pdf.pages):
                    pdf.pages[page_num-1].to_image(resolution=120).save(str(filepath), format="PNG")
                    self.stats['screenshots_taken'] += 1
                    return str(filepath)
        except Exception as e:
            print(f"    Screenshot failed: {e}")
        return ""

    def extract_articles(self, pdf_path: str) -> List[Dict]:
        articles = []
        processed_pages = set()
        try:
            with pdfplumber.open(pdf_path) as pdf:
                filename = Path(pdf_path).name
                total_pages = len(pdf.pages)
                print(f"  Processing: {filename} ({total_pages} pages)")

                # First pass: find all pages that contain any keyword
                keyword_pages = []
                for page_num in range(1, total_pages + 1):
                    if page_num in processed_pages:
                        continue
                    page_text = pdf.pages[page_num-1].extract_text() or ""
                    if any(kw.lower() in page_text.lower() for kw in self.all_keywords):
                        keyword_pages.append(page_num)

                for page_num in keyword_pages:
                    if page_num in processed_pages:
                        continue
                    page_text = pdf.pages[page_num-1].extract_text() or ""

                    # Find the specific article block on this page that contains the keyword
                    block_text, title, y_start, y_end = self.get_article_block_around_keyword(pdf_path, page_num, 0)
                    if not block_text:
                        # Fallback: use whole page
                        block_text = page_text
                        title = self.get_largest_font_text_on_page(pdf_path, page_num) or f"Article - Page {page_num}"

                    # Check if block contains keywords
                    matches = [term for term in self.all_keywords if term.lower() in block_text.lower()]
                    if not matches:
                        continue

                    # Find continuation pages (based on the marker at bottom of this page)
                    article_pages = self.find_article_pages(pdf, page_num, y_start, total_pages)
                    for p in article_pages:
                        processed_pages.add(p)

                    # Combine text from all pages
                    combined_text = block_text
                    for p in article_pages[1:]:
                        combined_text += " " + (pdf.pages[p-1].extract_text() or "")

                    # Extract metadata
                    author = self.extract_author(combined_text)
                    sentiment = self.analyze_sentiment(combined_text)
                    article_date = self.extract_date_from_text(combined_text)
                    section = "Business"
                    publisher_name, reach = self.extract_publisher(combined_text, filename)
                    ave = self.calculate_ave(publisher_name, article_pages[0], sentiment)

                    # Capture screenshots for each page
                    screenshot_paths = []
                    for p in article_pages:
                        scr = self.capture_screenshot(pdf_path, p, title)
                        if scr:
                            screenshot_paths.append(scr)

                    # Create report
                    report_path = self.create_article_report({
                        "article_title": title,
                        "publisher": publisher_name,
                        "section": section,
                        "date": article_date,
                        "page": article_pages[0],
                        "pages": article_pages,
                        "reach": reach,
                        "ave": ave,
                        "author": author,
                        "source_file": filename,
                    }, screenshot_paths)

                    articles.append({
                        "title": title,
                        "publisher_name": publisher_name,
                        "section": section,
                        "publication_date": article_date,
                        "page": article_pages[0],
                        "pages": article_pages,
                        "reach": reach,
                        "ave": ave,
                        "author": author,
                        "sentiment": sentiment,
                        "keywords_matched": ", ".join(matches),
                        "source_file": filename,
                        "screenshot_path": screenshot_paths[0] if screenshot_paths else "",
                        "screenshot_paths": screenshot_paths,
                        "report_path": report_path,
                    })
                    self.stats['articles_found'] += 1
                    pages_str = f"Pages: {article_pages}" if len(article_pages)>1 else f"Page: {article_pages[0]}"
                    print(f"    {pages_str}: {title[:50]}...")

        except Exception as e:
            print(f"  Error: {e}")
            raise e
        return articles

    def get_largest_font_text_on_page(self, pdf_path: str, page_num: int) -> str:
        try:
            doc = fitz.open(pdf_path)
            page = doc[page_num - 1]
            blocks = page.get_text("dict")
            max_size = 0
            largest_text = ""
            for block in blocks.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        size = span.get("size", 0)
                        text = span.get("text", "").strip()
                        if size > max_size and len(text) > 15:
                            max_size = size
                            largest_text = text
            doc.close()
            return largest_text
        except Exception:
            return ""

    def extract_author(self, text: str) -> str:
        match = re.search(r'By\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip().upper()
        return ""

    def analyze_sentiment(self, text: str) -> str:
        pos = sum(1 for w in ['expansion','milestone','success','growth','positive'] if w in text.lower())
        neg = sum(1 for w in ['setback','decline','fall','struggles','crisis'] if w in text.lower())
        return "Positive" if pos > neg else "Negative" if neg > pos else "Neutral"

    def create_article_report(self, article_data: Dict, screenshot_paths: List[str]) -> str:
        safe_filename = Path(article_data['source_file']).stem
        output_filename = f"{safe_filename}_p{article_data['page']}_report.pdf"
        output_file = self.reports_folder / output_filename
        if output_file.exists():
            return str(output_file)

        doc = SimpleDocTemplate(str(output_file), pagesize=A4,
                                topMargin=1.5*cm, bottomMargin=1.5*cm,
                                leftMargin=1.5*cm, rightMargin=1.5*cm)
        styles = getSampleStyleSheet()
        param_style = ParagraphStyle('ParamStyle', parent=styles['Normal'], fontSize=8, leading=10)
        story = []
        total_parts = len(screenshot_paths)

        story.append(Paragraph(f"Part: 1 of {total_parts}", param_style))
        story.append(Spacer(1, 4))

        pub_section = f"{article_data['publisher']} - {article_data['section']}"
        data1 = [[
            Paragraph(f"<b>Publication:</b> {pub_section}", param_style),
            Paragraph(f"<b>Page:</b> {article_data['page']}", param_style)
        ]]
        table1 = Table(data1, colWidths=[8*cm, 8*cm])
        table1.setStyle(TableStyle([('ALIGN',(0,0),(0,0),'LEFT'),('ALIGN',(1,0),(1,0),'RIGHT'),
                                    ('FONTSIZE',(0,0),(-1,-1),8),('GRID',(0,0),(-1,-1),0,colors.white)]))
        story.append(table1)

        data2 = [[
            Paragraph(f"<b>Title:</b> {article_data['article_title']}", param_style),
            Paragraph(f"<b>Reach:</b> {article_data['reach']:,}", param_style)
        ]]
        table2 = Table(data2, colWidths=[8*cm, 8*cm])
        table2.setStyle(TableStyle([('ALIGN',(0,0),(0,0),'LEFT'),('ALIGN',(1,0),(1,0),'RIGHT'),
                                    ('FONTSIZE',(0,0),(-1,-1),8),('GRID',(0,0),(-1,-1),0,colors.white)]))
        story.append(table2)

        data3 = [[
            Paragraph(f"<b>Publish date:</b> {article_data['date']}", param_style),
            Paragraph(f"<b>AVE:</b> P {article_data['ave']:,.2f}", param_style)
        ]]
        table3 = Table(data3, colWidths=[8*cm, 8*cm])
        table3.setStyle(TableStyle([('ALIGN',(0,0),(0,0),'LEFT'),('ALIGN',(1,0),(1,0),'RIGHT'),
                                    ('FONTSIZE',(0,0),(-1,-1),8),('GRID',(0,0),(-1,-1),0,colors.white)]))
        story.append(table3)

        data4 = [[
            Paragraph(f"<b>Author:</b> {article_data['author']}", param_style),
            Paragraph("", param_style)
        ]]
        table4 = Table(data4, colWidths=[8*cm, 8*cm])
        table4.setStyle(TableStyle([('ALIGN',(0,0),(0,0),'LEFT'),('FONTSIZE',(0,0),(-1,-1),8),
                                    ('GRID',(0,0),(-1,-1),0,colors.white)]))
        story.append(table4)

        story.append(Spacer(1, 8))

        for i, scr in enumerate(screenshot_paths):
            if i > 0:
                story.append(PageBreak())
                story.append(Paragraph(f"Part: {i+1} of {total_parts}", param_style))
                story.append(Spacer(1, 4))
            if scr and Path(scr).exists():
                img = Image(scr, width=16*cm, height=22*cm)
                story.append(img)

        story.append(Spacer(1, 15))
        footer_style = ParagraphStyle('FooterStyle', parent=styles['Normal'], fontSize=7,
                                       alignment=TA_CENTER, textColor=colors.grey)
        story.append(Paragraph('🍐 This article is copyright protected and licensed under agreement with DALRO. Redistribution or re-sale is not allowed.', footer_style))

        doc.build(story)
        self.stats['reports_generated'] += 1
        return str(output_file)

    def get_stats(self) -> Dict:
        return self.stats