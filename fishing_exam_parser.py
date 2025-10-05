#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "pdfplumber==0.10.0",
#     "click==8.1.7",
#     "pydantic==2.10.6",
#     "pillow==11.0.0",
# ]
# ///
"""
Bavarian Fishing Exam Parser using FSM (Finite State Machine)

This script extracts text line by line from PDF and uses a state machine
to parse questions correctly, handling alternating background colors.

Usage:
    uv run fishing_exam_parser.py --input "PDF_FILE" --output "OUTPUT_FILE"
"""

import base64
import io
import json
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import click
import pdfplumber
from PIL import Image
from pydantic import BaseModel, Field


class ColumnHeader(BaseModel):
    """Column header information."""
    name: str
    x: float
    y: float


class PageHeaders(BaseModel):
    """All column headers on a page."""
    page: int
    frage_x: Optional[float] = None
    antwort_a_x: Optional[float] = None
    antwort_b_x: Optional[float] = None
    antwort_c_x: Optional[float] = None
    richtige_antwort_x: Optional[float] = None


class QuestionAnchor(BaseModel):
    """Question number anchor with position."""
    number: str
    page: int
    x: float
    y: float
    x0: float
    y0: float
    x1: float
    y1: float


class QuestionRegion(BaseModel):
    """Defines a bounding box for extracting question text."""
    anchor: QuestionAnchor
    x_start: float = Field(description="Left edge (Frage column)")
    x_end: float = Field(description="Right edge (Antwort A column)")
    y_start: float = Field(description="Top edge (question anchor)")
    y_end: float = Field(description="Bottom edge (next question anchor or page bottom)")


class ExtractedQuestion(BaseModel):
    """Extracted question with text."""
    number: str
    page: int
    text: str
    region: Optional[QuestionRegion] = None


class AnswerRegion(BaseModel):
    """Defines a bounding box for extracting answer text."""
    anchor: QuestionAnchor
    answer_letter: str = Field(description="'A', 'B', or 'C'")
    x_start: float
    x_end: float
    y_start: float
    y_end: float


class ExtractedAnswer(BaseModel):
    """Extracted answer with text."""
    question_number: str
    page: int
    answer_letter: str
    text: str
    region: Optional[AnswerRegion] = None


class QuestionOutput(BaseModel):
    """Final output format for a question."""
    number: str
    page: int
    question: str
    answers: Dict[str, str]
    correct_answer: Optional[str] = None
    image: Optional[str] = None  # Image filename for picture questions



class FishingExamParserSimple:
    """Main parser class."""

    def __init__(self, pdf_path: str):
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    def find_column_headers(self, max_pages: Optional[int] = None) -> List[PageHeaders]:
        """Find column headers (Frage, Antwort A, B, C) X-coordinates for each page."""
        all_page_headers = []
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                total_pages = len(pdf.pages)
                pages_to_process = min(max_pages, total_pages) if max_pages else total_pages

                click.echo(f"Scanning {pages_to_process} of {total_pages} pages for column headers...")

                for page_num, page in enumerate(pdf.pages[:pages_to_process], 1):
                    if page_num % 20 == 0:
                        click.echo(f"Scanned {page_num} pages...")

                    # Extract text to find multi-word headers
                    text = page.extract_text()
                    if not text:
                        continue

                    # Extract all words with positions
                    words = page.extract_words(x_tolerance=3, y_tolerance=3)

                    # Initialize page headers
                    page_headers = PageHeaders(page=page_num)

                    # Look for exact header strings by checking consecutive words
                    words_list = list(words)
                    for i, word in enumerate(words_list):
                        # Check for "Frage"
                        if word['text'].strip() == 'Frage':
                            page_headers.frage_x = word['x0']

                        # Check for "Richtige" (first part of "Richtige Antwort")
                        # They might be on separate lines but should have similar X coordinate
                        if word['text'].strip() == 'Richtige':
                            # Look for "Antwort" with similar X position (within 10 pixels)
                            for other_word in words_list:
                                if (other_word['text'].strip() == 'Antwort' and
                                    abs(other_word['x0'] - word['x0']) < 10):
                                    page_headers.richtige_antwort_x = word['x0']
                                    break

                        # Check for "Antwort A", "Antwort B", "Antwort C"
                        if word['text'].strip() == 'Antwort' and i + 1 < len(words_list):
                            next_word = words_list[i + 1]
                            if next_word['text'].strip() == 'A':
                                page_headers.antwort_a_x = word['x0']
                            elif next_word['text'].strip() == 'B':
                                page_headers.antwort_b_x = word['x0']
                            elif next_word['text'].strip() == 'C':
                                page_headers.antwort_c_x = word['x0']

                    # Only add if we found at least one header
                    if page_headers.frage_x or page_headers.antwort_a_x:
                        all_page_headers.append(page_headers)

                click.echo(f"Found column headers on {len(all_page_headers)} pages")

        except Exception as e:
            click.echo(f"Error scanning PDF: {e}", err=True)
            raise

        return all_page_headers

    def find_question_anchors(self, max_pages: Optional[int] = None) -> List[QuestionAnchor]:
        """Find all question number anchors in the PDF with their coordinates.

        Supports two formats:
        - Regular questions: 1.001, 1.002, 2.001, etc.
        - Picture questions: B2.1, B2.2, B3.1, etc.
        """
        all_anchors = []
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                total_pages = len(pdf.pages)
                pages_to_process = min(max_pages, total_pages) if max_pages else total_pages

                click.echo(f"Scanning {pages_to_process} of {total_pages} pages for question numbers...")

                for page_num, page in enumerate(pdf.pages[:pages_to_process], 1):
                    if page_num % 20 == 0:
                        click.echo(f"Scanned {page_num} pages...")

                    # Extract all words with positions
                    words = page.extract_words(x_tolerance=3, y_tolerance=3)

                    # Pattern 1: Regular questions (e.g., "1.001", "1.002", "2.001")
                    regular_pattern = re.compile(r'^(\d+\.\d{3})$')

                    # Pattern 2: Picture questions (e.g., "B2.1", "B2.2", "B3.1")
                    picture_pattern = re.compile(r'^(B\d+\.\d+)$')

                    for word in words:
                        text = word['text'].strip()

                        # Try regular pattern first
                        match = regular_pattern.match(text)
                        if not match:
                            # Try picture pattern
                            match = picture_pattern.match(text)

                        if match:
                            all_anchors.append(QuestionAnchor(
                                number=match.group(1),
                                page=page_num,
                                x=word['x0'],
                                y=word['top'],
                                x0=word['x0'],
                                y0=word['top'],
                                x1=word['x1'],
                                y1=word['bottom']
                            ))

                # Separate regular and picture questions for logging
                regular_count = sum(1 for a in all_anchors if not a.number.startswith('B'))
                picture_count = sum(1 for a in all_anchors if a.number.startswith('B'))

                click.echo(f"Found {len(all_anchors)} question numbers total:")
                click.echo(f"  Regular questions: {regular_count}")
                click.echo(f"  Picture questions: {picture_count}")

        except Exception as e:
            click.echo(f"Error scanning PDF: {e}", err=True)
            raise

        return all_anchors

    def create_question_regions(
        self,
        anchors: List[QuestionAnchor],
        page_headers: List[PageHeaders]
    ) -> List[QuestionRegion]:
        """Combine anchors and headers to create bounding boxes for questions."""
        regions = []

        # Create a map of page -> headers
        headers_by_page = {h.page: h for h in page_headers}

        # Find the first page with Bild-Fragen headers (fallback for pages without headers)
        bild_fragen_headers = None
        for headers in page_headers:
            # Check if this page has headers and has Bild-Fragen (we can detect by checking if any anchor on this page starts with B)
            if headers.frage_x and headers.antwort_a_x:
                # This could be Bild-Fragen headers - save as fallback
                bild_fragen_headers = headers

        # Group anchors by page
        anchors_by_page = {}
        for anchor in anchors:
            if anchor.page not in anchors_by_page:
                anchors_by_page[anchor.page] = []
            anchors_by_page[anchor.page].append(anchor)

        # Sort anchors on each page by Y coordinate
        for page_num in anchors_by_page:
            anchors_by_page[page_num].sort(key=lambda a: a.y)

        # Create regions for each question
        for page_num, page_anchors in anchors_by_page.items():
            # Check if this is a Bild-Fragen page (any anchor starts with B)
            is_bild_page = any(a.number.startswith('B') for a in page_anchors)

            # Get headers for this page
            if page_num in headers_by_page:
                headers = headers_by_page[page_num]
            elif is_bild_page and bild_fragen_headers:
                # Use Bild-Fragen headers as fallback
                click.echo(f"Using Bild-Fragen headers from page {bild_fragen_headers.page} for page {page_num}")
                headers = bild_fragen_headers
            else:
                click.echo(f"Warning: No headers found for page {page_num}, skipping")
                continue

            # Check if we have required columns
            if headers.frage_x is None or headers.antwort_a_x is None:
                click.echo(f"Warning: Missing Frage or Antwort A columns on page {page_num}, skipping")
                continue

            for i, anchor in enumerate(page_anchors):
                # X coordinates: from Frage column to Antwort A column
                x_start = headers.frage_x
                x_end = headers.antwort_a_x

                # Y coordinates: from this anchor to next anchor (or page bottom)
                y_start = anchor.y
                if i + 1 < len(page_anchors):
                    y_end = page_anchors[i + 1].y
                else:
                    # Last question on page - we'll need page height
                    # For now, use a large value that will be adjusted later
                    y_end = 999999  # Will be replaced with actual page height

                regions.append(QuestionRegion(
                    anchor=anchor,
                    x_start=x_start,
                    x_end=x_end,
                    y_start=y_start,
                    y_end=y_end
                ))

        click.echo(f"Created {len(regions)} question regions")
        return regions

    def extract_questions(
        self,
        regions: List[QuestionRegion],
        max_pages: Optional[int] = None
    ) -> List[ExtractedQuestion]:
        """Extract question text from defined regions."""
        extracted_questions = []

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                total_pages = len(pdf.pages)
                pages_to_process = min(max_pages, total_pages) if max_pages else total_pages

                click.echo(f"Extracting text from {len(regions)} regions...")

                # Group regions by page for efficiency
                regions_by_page = {}
                for region in regions:
                    page_num = region.anchor.page
                    if page_num not in regions_by_page:
                        regions_by_page[page_num] = []
                    regions_by_page[page_num].append(region)

                # Process each page
                for page_num in sorted(regions_by_page.keys()):
                    if page_num > pages_to_process:
                        break

                    page = pdf.pages[page_num - 1]  # 0-indexed
                    page_height = page.height

                    for region in regions_by_page[page_num]:
                        # Adjust y_end if it was set to placeholder
                        y_end = region.y_end if region.y_end < 999999 else page_height

                        # Define bounding box (x0, top, x1, bottom)
                        bbox = (region.x_start, region.y_start, region.x_end, y_end)

                        # Extract text from region
                        cropped = page.within_bbox(bbox)
                        text = cropped.extract_text()

                        if text:
                            # Clean up text (remove extra whitespace, newlines)
                            text = ' '.join(text.split())

                        extracted_questions.append(ExtractedQuestion(
                            number=region.anchor.number,
                            page=region.anchor.page,
                            text=text or "",
                            region=region
                        ))

                click.echo(f"Extracted {len(extracted_questions)} questions")

        except Exception as e:
            click.echo(f"Error extracting questions: {e}", err=True)
            raise

        return extracted_questions

    def create_answer_regions(
        self,
        anchors: List[QuestionAnchor],
        page_headers: List[PageHeaders]
    ) -> List[AnswerRegion]:
        """Create bounding boxes for answer extraction (A, B, C)."""
        regions = []

        # Create a map of page -> headers
        headers_by_page = {h.page: h for h in page_headers}

        # Find Bild-Fragen headers as fallback
        bild_fragen_headers = None
        for headers in page_headers:
            if headers.antwort_a_x and headers.antwort_b_x and headers.antwort_c_x:
                bild_fragen_headers = headers

        # Group anchors by page
        anchors_by_page = {}
        for anchor in anchors:
            if anchor.page not in anchors_by_page:
                anchors_by_page[anchor.page] = []
            anchors_by_page[anchor.page].append(anchor)

        # Sort anchors on each page by Y coordinate
        for page_num in anchors_by_page:
            anchors_by_page[page_num].sort(key=lambda a: a.y)

        # Create regions for each answer
        for page_num, page_anchors in anchors_by_page.items():
            # Check if this is a Bild-Fragen page
            is_bild_page = any(a.number.startswith('B') for a in page_anchors)

            # Get headers for this page
            if page_num in headers_by_page:
                headers = headers_by_page[page_num]
            elif is_bild_page and bild_fragen_headers:
                headers = bild_fragen_headers
            else:
                click.echo(f"Warning: No headers found for page {page_num}, skipping answers")
                continue

            # Check if we have required columns
            if not all([headers.antwort_a_x, headers.antwort_b_x, headers.antwort_c_x]):
                click.echo(f"Warning: Missing answer columns on page {page_num}, skipping")
                continue

            for i, anchor in enumerate(page_anchors):
                # Y coordinates: from this anchor to next anchor (or page bottom)
                y_start = anchor.y
                if i + 1 < len(page_anchors):
                    y_end = page_anchors[i + 1].y
                else:
                    y_end = 999999  # Will be replaced with actual page height

                # Answer A: from antwort_a_x to antwort_b_x
                regions.append(AnswerRegion(
                    anchor=anchor,
                    answer_letter='A',
                    x_start=headers.antwort_a_x,
                    x_end=headers.antwort_b_x,
                    y_start=y_start,
                    y_end=y_end
                ))

                # Answer B: from antwort_b_x to antwort_c_x
                regions.append(AnswerRegion(
                    anchor=anchor,
                    answer_letter='B',
                    x_start=headers.antwort_b_x,
                    x_end=headers.antwort_c_x,
                    y_start=y_start,
                    y_end=y_end
                ))

                # Answer C: from antwort_c_x to end (page width)
                # We'll need to get page width when extracting
                regions.append(AnswerRegion(
                    anchor=anchor,
                    answer_letter='C',
                    x_start=headers.antwort_c_x,
                    x_end=999999,  # Will be replaced with actual page width
                    y_start=y_start,
                    y_end=y_end
                ))

        click.echo(f"Created {len(regions)} answer regions")
        return regions

    def extract_answers(
        self,
        regions: List[AnswerRegion],
        max_pages: Optional[int] = None
    ) -> List[ExtractedAnswer]:
        """Extract answer text from defined regions."""
        extracted_answers = []

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                total_pages = len(pdf.pages)
                pages_to_process = min(max_pages, total_pages) if max_pages else total_pages

                click.echo(f"Extracting text from {len(regions)} answer regions...")

                # Group regions by page for efficiency
                regions_by_page = {}
                for region in regions:
                    page_num = region.anchor.page
                    if page_num not in regions_by_page:
                        regions_by_page[page_num] = []
                    regions_by_page[page_num].append(region)

                # Process each page
                for page_num in sorted(regions_by_page.keys()):
                    if page_num > pages_to_process:
                        break

                    page = pdf.pages[page_num - 1]  # 0-indexed
                    page_height = page.height
                    page_width = page.width

                    for region in regions_by_page[page_num]:
                        # Adjust y_end if it was set to placeholder
                        y_end = region.y_end if region.y_end < 999999 else page_height

                        # Adjust x_end if it was set to placeholder (Answer C)
                        x_end = region.x_end if region.x_end < 999999 else page_width

                        # Define bounding box (x0, top, x1, bottom)
                        bbox = (region.x_start, region.y_start, x_end, y_end)

                        # Extract text from region
                        cropped = page.within_bbox(bbox)
                        text = cropped.extract_text()

                        if text:
                            # Clean up text (remove extra whitespace, newlines)
                            text = ' '.join(text.split())

                        extracted_answers.append(ExtractedAnswer(
                            question_number=region.anchor.number,
                            page=region.anchor.page,
                            answer_letter=region.answer_letter,
                            text=text or "",
                            region=region
                        ))

                click.echo(f"Extracted {len(extracted_answers)} answers")

        except Exception as e:
            click.echo(f"Error extracting answers: {e}", err=True)
            raise

        return extracted_answers

    def extract_correct_answers(
        self,
        anchors: List[QuestionAnchor],
        page_headers: List[PageHeaders],
        max_pages: Optional[int] = None
    ) -> Dict[str, str]:
        """Extract which answer (A, B, or C) is correct for each question."""
        correct_answers = {}

        # Create a map of page -> headers
        headers_by_page = {h.page: h for h in page_headers}

        # Find Bild-Fragen headers as fallback
        bild_fragen_headers = None
        for headers in page_headers:
            if headers.richtige_antwort_x:
                bild_fragen_headers = headers

        # Group anchors by page
        anchors_by_page = {}
        for anchor in anchors:
            if anchor.page not in anchors_by_page:
                anchors_by_page[anchor.page] = []
            anchors_by_page[anchor.page].append(anchor)

        # Sort anchors on each page by Y coordinate
        for page_num in anchors_by_page:
            anchors_by_page[page_num].sort(key=lambda a: a.y)

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                total_pages = len(pdf.pages)
                pages_to_process = min(max_pages, total_pages) if max_pages else total_pages

                click.echo(f"Extracting correct answers for {len(anchors)} questions...")

                # Process each page
                for page_num in sorted(anchors_by_page.keys()):
                    if page_num > pages_to_process:
                        break

                    # Check if this is a Bild-Fragen page
                    page_anchors = anchors_by_page[page_num]
                    is_bild_page = any(a.number.startswith('B') for a in page_anchors)

                    # Get headers for this page
                    if page_num in headers_by_page:
                        headers = headers_by_page[page_num]
                    elif is_bild_page and bild_fragen_headers:
                        headers = bild_fragen_headers
                    else:
                        click.echo(f"Warning: No headers for page {page_num}, skipping correct answers")
                        continue

                    if headers.richtige_antwort_x is None:
                        click.echo(f"Warning: No 'Richtige Antwort' column on page {page_num}, skipping")
                        continue

                    page = pdf.pages[page_num - 1]  # 0-indexed
                    page_height = page.height
                    page_width = page.width

                    for i, anchor in enumerate(page_anchors):
                        # Y coordinates: from this anchor to next anchor
                        y_start = anchor.y
                        if i + 1 < len(page_anchors):
                            y_end = page_anchors[i + 1].y
                        else:
                            y_end = page_height

                        # X coordinates: Richtige Antwort column (with some width)
                        x_start = headers.richtige_antwort_x
                        x_end = min(x_start + 50, page_width)  # Give some width to capture the letter

                        # Define bounding box
                        bbox = (x_start, y_start, x_end, y_end)

                        # Extract text from region
                        cropped = page.within_bbox(bbox)
                        text = cropped.extract_text()

                        if text:
                            text = text.strip()
                            # Debug output
                            if i < 5:  # Show first 5
                                click.echo(f"  Question {anchor.number}: extracted '{text}' from bbox {bbox}")

                            # Look for A, B, or C in the text
                            if 'A' in text:
                                correct_answers[anchor.number] = 'A'
                            elif 'B' in text:
                                correct_answers[anchor.number] = 'B'
                            elif 'C' in text:
                                correct_answers[anchor.number] = 'C'
                        else:
                            if i < 5:
                                click.echo(f"  Question {anchor.number}: NO TEXT extracted from bbox {bbox}")

                click.echo(f"Found {len(correct_answers)} correct answers")

        except Exception as e:
            click.echo(f"Error extracting correct answers: {e}", err=True)
            raise

        return correct_answers

    def extract_images_for_picture_questions(
        self,
        anchors: List[QuestionAnchor],
        output_dir: Path,
        max_pages: Optional[int] = None
    ) -> Dict[str, str]:
        """Extract images for picture questions (B-questions) and save them.

        Returns a dict mapping question number to image filename.
        """
        image_files = {}

        # Filter to only picture questions
        picture_questions = [a for a in anchors if a.number.startswith('B')]

        if not picture_questions:
            click.echo("No picture questions found")
            return image_files

        # Create output directory
        output_dir.mkdir(parents=True, exist_ok=True)

        # Group by page
        questions_by_page = {}
        for anchor in picture_questions:
            if anchor.page not in questions_by_page:
                questions_by_page[anchor.page] = []
            questions_by_page[anchor.page].append(anchor)

        # Sort by Y position on each page
        for page_num in questions_by_page:
            questions_by_page[page_num].sort(key=lambda a: a.y)

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                total_pages = len(pdf.pages)
                pages_to_process = min(max_pages, total_pages) if max_pages else total_pages

                click.echo(f"Extracting images for {len(picture_questions)} picture questions...")

                for page_num in sorted(questions_by_page.keys()):
                    if page_num > pages_to_process:
                        break

                    page = pdf.pages[page_num - 1]
                    page_questions = questions_by_page[page_num]

                    # Get all images on this page
                    images = page.images

                    if len(images) < len(page_questions):
                        click.echo(f"Warning: Page {page_num} has {len(images)} images but {len(page_questions)} questions")

                    # Match images to questions by Y position
                    for i, question in enumerate(page_questions):
                        if i >= len(images):
                            click.echo(f"Warning: No image found for question {question.number}")
                            continue

                        img_data = images[i]

                        # Get image format from stream
                        image_stream = img_data.get('stream')
                        if not image_stream:
                            click.echo(f"Warning: No stream for image {i} on page {page_num}")
                            continue

                        # Determine file extension
                        # Try to get format from Filter
                        img_filter = image_stream.get('Filter', '')
                        if isinstance(img_filter, list):
                            img_filter = img_filter[0] if img_filter else ''

                        # Map PDF filter to file extension
                        extension = 'png'  # default
                        if 'DCT' in str(img_filter) or 'JPEG' in str(img_filter):
                            extension = 'jpg'
                        elif 'JPX' in str(img_filter):
                            extension = 'jp2'

                        # Save image
                        filename = f"{question.number}.{extension}"
                        filepath = output_dir / filename

                        try:
                            # Extract image data
                            img_bytes = image_stream.get_data()

                            # Save to file
                            with open(filepath, 'wb') as f:
                                f.write(img_bytes)

                            image_files[question.number] = filename
                            click.echo(f"  Saved {filename}")

                        except Exception as e:
                            click.echo(f"  Error saving image for {question.number}: {e}")

                click.echo(f"Extracted {len(image_files)} images to {output_dir}")

        except Exception as e:
            click.echo(f"Error extracting images: {e}", err=True)
            raise

        return image_files



@click.command()
@click.option(
    '--input', '-i',
    'input_file',
#    required=True,
    default="Pruefungsfragen_Fischerpruefung_2025_Stand-2024-12-19.pdf",
    type=click.Path(exists=True),
    help='Path to the PDF file to parse'
)
@click.option(
    '--output', '-o',
    default='fishing_exam_fsm.json',
    help='Output JSON file path (default: fishing_exam_fsm.json)'
)
@click.option(
    '--pages', '-p',
    type=int,
    help='Limit parsing to first N pages (useful for testing)'
)
@click.option(
    '--debug',
    is_flag=True,
    help='Enable debug output with raw lines'
)
def main(input_file: str, output: str, pages: Optional[int], debug: bool) -> None:
    """Parse Bavarian fishing exam questions using FSM approach."""
    try:
        parser = FishingExamParserSimple(input_file)

        # Step 1: Find column headers
        click.echo("\n=== Step 1: Finding column headers ===")
        column_headers = parser.find_column_headers(max_pages=pages)

        if debug or True:
            print("\n=== Column Headers ===")
            for headers in column_headers[:5]:  # Show first 5 pages
                print(f"\nPage {headers.page}:")
                print(f"  Frage: {headers.frage_x}")
                print(f"  Antwort A: {headers.antwort_a_x}")
                print(f"  Antwort B: {headers.antwort_b_x}")
                print(f"  Antwort C: {headers.antwort_c_x}")
                print(f"  Richtige Antwort: {headers.richtige_antwort_x}")

        # Step 2: Find all question number anchors
        click.echo("\n=== Step 2: Finding question numbers ===")
        anchors = parser.find_question_anchors(max_pages=pages)

        if debug or True:
            print("\n=== Question Anchors ===")
            for anchor in anchors[:10]:  # Show first 10
                print(f"Question {anchor.number}: page {anchor.page}, x={anchor.x:.1f}, y={anchor.y:.1f}")

        # Step 3: Create question regions by combining headers and anchors
        click.echo("\n=== Step 3: Creating question regions ===")
        regions = parser.create_question_regions(anchors, column_headers)

        if debug or True:
            print("\n=== Question Regions ===")
            for region in regions[:5]:  # Show first 5
                print(f"Question {region.anchor.number} (page {region.anchor.page}):")
                print(f"  X: {region.x_start:.1f} -> {region.x_end:.1f}")
                print(f"  Y: {region.y_start:.1f} -> {region.y_end:.1f}")

        # Step 4: Extract question text from regions
        click.echo("\n=== Step 4: Extracting question text ===")
        questions = parser.extract_questions(regions, max_pages=pages)

        if debug or True:
            print("\n=== Extracted Questions ===")
            for question in questions[:5]:  # Show first 5
                print(f"\nQuestion {question.number} (page {question.page}):")
                print(f"  Text: {question.text[:100]}..." if len(question.text) > 100 else f"  Text: {question.text}")

        # Step 5: Create answer regions
        click.echo("\n=== Step 5: Creating answer regions ===")
        answer_regions = parser.create_answer_regions(anchors, column_headers)

        # Step 6: Extract answers
        click.echo("\n=== Step 6: Extracting answers ===")
        answers = parser.extract_answers(answer_regions, max_pages=pages)

        # Step 7: Extract correct answers
        click.echo("\n=== Step 7: Extracting correct answers ===")
        correct_answers = parser.extract_correct_answers(anchors, column_headers, max_pages=pages)

        # Step 8: Extract images for picture questions
        click.echo("\n=== Step 8: Extracting images for picture questions ===")
        images_dir = Path(output).parent / "images"
        image_files = parser.extract_images_for_picture_questions(anchors, images_dir, max_pages=pages)

        if debug or True:
            print("\n=== Extracted Questions with Answers ===")
            # Group answers by question
            for question in questions[:3]:  # Show first 3 questions
                correct = correct_answers.get(question.number, '?')
                print(f"\nQuestion {question.number} (Correct: {correct}):")
                print(f"  Q: {question.text[:80]}..." if len(question.text) > 80 else f"  Q: {question.text}")
                for ans in [a for a in answers if a.question_number == question.number]:
                    marker = " ✓" if ans.answer_letter == correct else ""
                    print(f"  {ans.answer_letter}{marker}: {ans.text[:60]}..." if len(ans.text) > 60 else f"  {ans.answer_letter}{marker}: {ans.text}")

        # Step 9: Build JSON output using Pydantic
        click.echo(f"\n=== Step 9: Building JSON output ===")
        output_data = []

        for question in questions:
            # Get answers for this question
            question_answers = [a for a in answers if a.question_number == question.number]

            # Build answer dict
            answers_dict = {}
            for ans in question_answers:
                answers_dict[ans.answer_letter] = ans.text

            # Get correct answer
            correct = correct_answers.get(question.number)

            # Get image filename if this is a picture question
            image_file = image_files.get(question.number)

            # Create Pydantic model
            question_output = QuestionOutput(
                number=question.number,
                page=question.page,
                question=question.text,
                answers=answers_dict,
                correct_answer=correct,
                image=image_file
            )
            output_data.append(question_output)

        # Write to JSON file using Pydantic
        output_path = Path(output)
        with output_path.open('w', encoding='utf-8') as f:
            # Convert list of Pydantic models to JSON
            json_data = [q.model_dump() for q in output_data]
            json.dump(json_data, f, indent=2, ensure_ascii=False)

        click.echo(f"Successfully wrote {len(output_data)} questions to {output}")
        click.echo(f"\nSummary:")
        click.echo(f"  Total questions: {len(questions)}")
        click.echo(f"  Total answers: {len(answers)}")
        click.echo(f"  Correct answers found: {len(correct_answers)}")
        click.echo(f"  Picture question images: {len(image_files)}")
        if image_files:
            click.echo(f"  Images saved to: {images_dir}")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()