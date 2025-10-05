#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "genanki==0.13.1",
#     "click==8.1.7",
#     "pydantic==2.10.6",
# ]
# ///
"""
Generate Anki deck from fishing exam questions.

This script takes the parsed JSON output and creates an Anki deck
with all questions, answers, and images.

Usage:
    uv run generate_anki_deck.py --input fishing_exam_fsm.json --output fishing_exam.apkg
"""

import json
import random
import sys
from pathlib import Path
from typing import List, Optional

import click
import genanki
from pydantic import BaseModel


class Question(BaseModel):
    """Question model matching the parser output."""
    number: str
    page: int
    question: str
    answers: dict[str, str]
    correct_answer: Optional[str] = None
    image: Optional[str] = None


class AnkiDeckGenerator:
    """Generate Anki deck from fishing exam questions."""

    # Custom model ID (random but stable)
    MODEL_ID = 1607392319

    # Deck ID (random but stable)
    DECK_ID = 2059400110

    def __init__(self, deck_name: str = "Bayerische Fischerprüfung 2025"):
        self.deck_name = deck_name

        # Define the note model (card template)
        self.model = genanki.Model(
            self.MODEL_ID,
            'Fishing Exam Question',
            fields=[
                {'name': 'QuestionNumber'},
                {'name': 'Question'},
                {'name': 'Image'},
                {'name': 'AnswerA'},
                {'name': 'AnswerB'},
                {'name': 'AnswerC'},
                {'name': 'CorrectAnswer'},
                {'name': 'IsACorrect'},
                {'name': 'IsBCorrect'},
                {'name': 'IsCCorrect'},
                {'name': 'Explanation'},
            ],
            templates=[
                {
                    'name': 'Card 1',
                    'qfmt': '''
                        <div class="question-number">Frage {{QuestionNumber}}</div>
                        <div class="question">{{Question}}</div>
                        {{#Image}}
                        <div class="image">
                            <img src="{{Image}}" style="max-width: 400px; max-height: 300px;">
                        </div>
                        {{/Image}}
                        <hr>
                        <div class="answers">
                            <div class="answer">A) {{AnswerA}}</div>
                            <div class="answer">B) {{AnswerB}}</div>
                            <div class="answer">C) {{AnswerC}}</div>
                        </div>
                    ''',
                    'afmt': '''
                        <div class="question-number">Frage {{QuestionNumber}}</div>
                        <div class="question">{{Question}}</div>
                        {{#Image}}
                        <div class="image">
                            <img src="{{Image}}" style="max-width: 400px; max-height: 300px;">
                        </div>
                        {{/Image}}
                        <hr>
                        <div class="answers">
                            <div class="answer {{#IsACorrect}}correct{{/IsACorrect}}">
                                A) {{AnswerA}}
                                {{#IsACorrect}}<span class="checkmark">✓</span>{{/IsACorrect}}
                            </div>
                            <div class="answer {{#IsBCorrect}}correct{{/IsBCorrect}}">
                                B) {{AnswerB}}
                                {{#IsBCorrect}}<span class="checkmark">✓</span>{{/IsBCorrect}}
                            </div>
                            <div class="answer {{#IsCCorrect}}correct{{/IsCCorrect}}">
                                C) {{AnswerC}}
                                {{#IsCCorrect}}<span class="checkmark">✓</span>{{/IsCCorrect}}
                            </div>
                        </div>
                        {{#Explanation}}
                        <hr>
                        <div class="explanation">{{Explanation}}</div>
                        {{/Explanation}}
                    ''',
                },
            ],
            css='''
                .card {
                    font-family: arial;
                    font-size: 20px;
                    text-align: left;
                    color: black;
                    background-color: white;
                }

                .question-number {
                    font-size: 14px;
                    color: #888;
                    margin-bottom: 10px;
                }

                .question {
                    font-size: 24px;
                    font-weight: bold;
                    margin-bottom: 20px;
                }

                .image {
                    text-align: center;
                    margin: 20px 0;
                }

                .answers {
                    margin-top: 20px;
                }

                .answer {
                    padding: 10px;
                    margin: 5px 0;
                    border-radius: 5px;
                    background-color: #f5f5f5;
                }

                .answer.correct {
                    background-color: #d4edda;
                    border: 2px solid #28a745;
                }

                .checkmark {
                    color: #28a745;
                    font-weight: bold;
                    float: right;
                }

                .explanation {
                    background-color: #fff3cd;
                    padding: 10px;
                    border-radius: 5px;
                    margin-top: 10px;
                }
            '''
        )

        # Create deck
        self.deck = genanki.Deck(self.DECK_ID, self.deck_name)

    def add_question(
        self,
        question_data: Question,
        image_path: Optional[Path] = None
    ) -> None:
        """Add a question to the deck."""

        # Prepare image field
        image_field = ""
        if question_data.image and image_path and image_path.exists():
            image_field = question_data.image

        # Get answers
        answer_a = question_data.answers.get('A', '')
        answer_b = question_data.answers.get('B', '')
        answer_c = question_data.answers.get('C', '')

        # Determine which answer is correct
        correct = question_data.correct_answer
        is_a_correct = '1' if correct == 'A' else ''
        is_b_correct = '1' if correct == 'B' else ''
        is_c_correct = '1' if correct == 'C' else ''

        # Create note
        note = genanki.Note(
            model=self.model,
            fields=[
                question_data.number,
                question_data.question,
                image_field,
                answer_a,
                answer_b,
                answer_c,
                question_data.correct_answer or '',
                is_a_correct,
                is_b_correct,
                is_c_correct,
                '',  # Explanation (empty for now)
            ]
        )

        self.deck.add_note(note)

    def save(self, output_path: Path, media_files: List[Path]) -> None:
        """Save the deck as an .apkg file."""
        package = genanki.Package(self.deck)

        # Add media files (images)
        if media_files:
            package.media_files = [str(f) for f in media_files if f.exists()]

        package.write_to_file(str(output_path))


@click.command()
@click.option(
    '--input', '-i',
    'input_file',
    default='fishing_exam_fsm.json',
    type=click.Path(exists=True),
    help='Input JSON file from parser'
)
@click.option(
    '--images-dir',
    default='images',
    type=click.Path(exists=True),
    help='Directory containing question images'
)
@click.option(
    '--output', '-o',
    default='fishing_exam.apkg',
    help='Output Anki deck file (.apkg)'
)
@click.option(
    '--deck-name',
    default='Bayerische Fischerprüfung 2025',
    help='Name of the Anki deck'
)
def main(input_file: str, images_dir: str, output: str, deck_name: str) -> None:
    """Generate Anki deck from fishing exam questions."""
    try:
        input_path = Path(input_file)
        images_path = Path(images_dir)
        output_path = Path(output)

        click.echo(f"Loading questions from {input_path}...")

        # Load questions
        with input_path.open('r', encoding='utf-8') as f:
            questions_data = json.load(f)

        click.echo(f"Found {len(questions_data)} questions")

        # Create deck generator
        generator = AnkiDeckGenerator(deck_name)

        # Collect media files
        media_files = []

        # Add questions to deck
        for q_data in questions_data:
            question = Question(**q_data)

            # Find image file if it exists
            image_path = None
            if question.image:
                image_path = images_path / question.image
                if image_path.exists():
                    media_files.append(image_path)

            generator.add_question(question, image_path)

        click.echo(f"Added {len(questions_data)} questions to deck")
        click.echo(f"Including {len(media_files)} images")

        # Save deck
        click.echo(f"Saving deck to {output_path}...")
        generator.save(output_path, media_files)

        click.echo(f"\n✓ Successfully created Anki deck: {output_path}")
        click.echo(f"\nImport this file into Anki to start studying!")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
