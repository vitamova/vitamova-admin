#!/usr/bin/env python3

import json
import re
import time
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from openai import OpenAI
from pydantic import BaseModel, Field

DB_PASSWORD = input("Enter database password: ")
OPENAI_KEY = input("Enter OpenAI API key: ")

DB_HOST = "vitamova-db.cluster-cartvcorpihi.us-east-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "vitamova"
DB_USER = "admin_app"

LANGUAGES = ("es", "pt", "ru")
MIN_RANK = 2000
MAX_RANK = 6000
BATCH_SIZE = 20
LIMIT: Optional[int] = None
GENERATOR_MODEL = "gpt-5.4-mini"
REVIEWER_MODEL = "gpt-5.4"
MAX_ATTEMPTS_PER_BATCH = 3
SLEEP_SECONDS = 0.25
SUCCESS_LOG = Path("definition_rewrite_successes.jsonl")
FAILURE_LOG = Path("definition_rewrite_failures.jsonl")

LANGUAGE_NAMES = {"es": "Spanish", "pt": "Portuguese", "ru": "Russian"}


class DefinitionItem(BaseModel):
    id: int
    definition: str


class DefinitionBatch(BaseModel):
    items: list[DefinitionItem]


class ReviewedDefinitionItem(BaseModel):
    id: int
    approved: bool
    issues: list[str] = Field(default_factory=list)
    definition: str


class ReviewedDefinitionBatch(BaseModel):
    items: list[ReviewedDefinitionItem]


def append_jsonl(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def clean_definition(value: str) -> str:
    return " ".join(str(value).strip().split())


def contains_lemma(definition: str, lemma: str) -> bool:
    normalized_lemma = clean_definition(lemma)
    if not normalized_lemma:
        return False

    pattern = rf"(?<!\w){re.escape(normalized_lemma)}(?!\w)"
    return re.search(pattern, definition, flags=re.IGNORECASE | re.UNICODE) is not None


def first_cased_character(value: str) -> Optional[str]:
    for character in value:
        if character.isalpha():
            return character
    return None


def validate_definition(definition: str, lemma: str) -> list[str]:
    errors: list[str] = []
    definition = clean_definition(definition)

    if not definition:
        return ["definition is empty"]

    first_character = first_cased_character(definition)
    if first_character and first_character.isupper():
        errors.append("definition begins with a capital letter; it should begin in lowercase")

    if definition.endswith((".", "!", "?", ";", ":")):
        errors.append("definition has terminal punctuation")

    if contains_lemma(definition, lemma):
        errors.append(f'the lemma "{lemma}" appears inside its own definition')

    if "\n" in definition:
        errors.append("definition contains a line break")

    if len(definition) > 260:
        errors.append("definition is too long")

    return errors


def connect_db():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        sslmode="require",
    )


def fetch_lemmas(conn) -> list[dict]:
    sql = """
        SELECT id, lemma, language, rank, pos, definition
        FROM lemmas
        WHERE language = ANY(%s)
          AND rank BETWEEN %s AND %s
        ORDER BY language, rank, id
    """

    params: list[object] = [list(LANGUAGES), MIN_RANK, MAX_RANK]

    if LIMIT is not None:
        sql += "\nLIMIT %s"
        params.append(LIMIT)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    return [dict(row) for row in rows]


def update_definitions(conn, updates: list[tuple[str, int]]) -> None:
    if not updates:
        return

    with conn.cursor() as cursor:
        psycopg2.extras.execute_batch(
            cursor,
            """
            UPDATE lemmas
            SET definition = %s
            WHERE id = %s
            """,
            updates,
            page_size=100,
        )

    conn.commit()


def compact_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": row["id"],
            "lemma": row["lemma"],
            "language": row["language"],
            "language_name": LANGUAGE_NAMES[row["language"]],
            "part_of_speech": row["pos"],
            "current_definition": row["definition"],
        }
        for row in rows
    ]


def build_generator_prompt(rows: list[dict], previous_feedback: Optional[dict[int, list[str]]] = None) -> str:
    feedback_section = ""
    if previous_feedback:
        feedback_section = (
            "\nPrevious attempts had the following problems. Correct all of them:\n"
            + json.dumps(previous_feedback, ensure_ascii=False, indent=2)
        )

    return f"""
Rewrite the dictionary definitions for the supplied lemmas.

Each definition must:
1. Be written in the same language as the lemma: es = Spanish, pt = Portuguese, ru = Russian.
2. Be brief, clear, natural, and suitable for an intermediate language learner.
3. Distinguish the word from nearby meanings without becoming encyclopedic.
4. Express only the principal meaning represented by the lemma entry.
5. Begin with a lowercase letter.
6. Have no period or other terminal punctuation.
7. Be a compact dictionary-style phrase rather than a full sentence.
8. Never use the lemma itself anywhere in its own definition.
9. Never use a trivial variation whose only purpose is to repeat the lemma.
10. Avoid circular wording, such as defining a verb as \"the action of [lemma]\".
11. Match the part of speech.
12. Do not include translations, examples, usage notes, labels, numbering, quotation marks, or extra commentary.
13. Return exactly one definition for every supplied ID.
14. Avoid beginning with a proper noun; rephrase so lowercase formatting remains natural.

Items:
{json.dumps(compact_rows(rows), ensure_ascii=False, indent=2)}
{feedback_section}
""".strip()


def build_reviewer_prompt(source_rows: list[dict], generated: DefinitionBatch) -> str:
    source_by_id = {int(row["id"]): row for row in source_rows}
    review_items = []

    for item in generated.items:
        source = source_by_id.get(item.id)
        if source is None:
            continue

        review_items.append(
            {
                "id": item.id,
                "lemma": source["lemma"],
                "language": source["language"],
                "language_name": LANGUAGE_NAMES[source["language"]],
                "part_of_speech": source["pos"],
                "proposed_definition": item.definition,
            }
        )

    return f"""
Act as a strict senior lexicographer reviewing definitions for a language-learning database.

For every item, verify:
1. The definition is accurate for the lemma and part of speech.
2. It is written entirely in the lemma's own language.
3. It is concise but sufficiently informative.
4. It is natural dictionary-style wording, not an example sentence.
5. It begins with a lowercase letter.
6. It has no terminal punctuation.
7. The lemma itself does not appear anywhere in the definition.
8. The wording is not circular and does not merely restate a morphological variation of the lemma.
9. It contains no translations, examples, labels, usage notes, or commentary.
10. It expresses a useful principal meaning for an intermediate learner.

If the proposed definition satisfies every requirement, set approved=true, return it unchanged, and return an empty issues list.
If it violates any requirement, set approved=false, list the problems, and provide a fully corrected replacement definition.
Return exactly one reviewed item for every supplied ID.

Items:
{json.dumps(review_items, ensure_ascii=False, indent=2)}
""".strip()


def generate_definitions(client: OpenAI, rows: list[dict], previous_feedback: Optional[dict[int, list[str]]] = None) -> DefinitionBatch:
    response = client.responses.parse(
        model=GENERATOR_MODEL,
        reasoning={"effort": "low"},
        input=[
            {
                "role": "developer",
                "content": "You are an expert multilingual lexicographer specializing in Spanish, Portuguese, and Russian learner dictionaries.",
            },
            {"role": "user", "content": build_generator_prompt(rows, previous_feedback)},
        ],
        text_format=DefinitionBatch,
    )

    if response.output_parsed is None:
        raise RuntimeError("The generator did not return a parsed structured response.")

    return response.output_parsed


def review_definitions(client: OpenAI, rows: list[dict], generated: DefinitionBatch) -> ReviewedDefinitionBatch:
    response = client.responses.parse(
        model=REVIEWER_MODEL,
        reasoning={"effort": "medium"},
        input=[
            {
                "role": "developer",
                "content": "You are a strict senior multilingual lexicographer. Correct inaccurate, circular, inconsistent, or poorly formatted definitions.",
            },
            {"role": "user", "content": build_reviewer_prompt(rows, generated)},
        ],
        text_format=ReviewedDefinitionBatch,
    )

    if response.output_parsed is None:
        raise RuntimeError("The reviewer did not return a parsed structured response.")

    return response.output_parsed


def process_batch(client: OpenAI, rows: list[dict]) -> tuple[list[tuple[str, int]], list[dict]]:
    rows_by_id = {int(row["id"]): row for row in rows}
    expected_ids = set(rows_by_id)
    feedback: Optional[dict[int, list[str]]] = None

    for attempt in range(1, MAX_ATTEMPTS_PER_BATCH + 1):
        generated = generate_definitions(client, rows, feedback)
        generated_ids = {item.id for item in generated.items}

        if generated_ids != expected_ids:
            feedback = {
                0: [
                    "response IDs did not match the requested IDs",
                    f"missing IDs: {sorted(expected_ids - generated_ids)}",
                    f"unexpected IDs: {sorted(generated_ids - expected_ids)}",
                ]
            }
            continue

        reviewed = review_definitions(client, rows, generated)
        reviewed_by_id = {item.id: item for item in reviewed.items}

        updates: list[tuple[str, int]] = []
        invalid_feedback: dict[int, list[str]] = {}

        for lemma_id, source_row in rows_by_id.items():
            reviewed_item = reviewed_by_id.get(lemma_id)
            if reviewed_item is None:
                invalid_feedback[lemma_id] = ["reviewer omitted this ID"]
                continue

            definition = clean_definition(reviewed_item.definition)
            validation_errors = validate_definition(definition, source_row["lemma"])

            if validation_errors:
                invalid_feedback[lemma_id] = reviewed_item.issues + validation_errors
                continue

            updates.append((definition, lemma_id))

        if not invalid_feedback:
            return updates, []

        feedback = invalid_feedback
        print(f"  Attempt {attempt} left {len(invalid_feedback)} invalid definitions.")

    failures = []
    for lemma_id, source_row in rows_by_id.items():
        failures.append(
            {
                "id": lemma_id,
                "lemma": source_row["lemma"],
                "language": source_row["language"],
                "rank": source_row["rank"],
                "issues": (feedback or {}).get(lemma_id, ["maximum attempts exhausted"]),
            }
        )

    return [], failures


def main() -> None:
    client = OpenAI(api_key=OPENAI_KEY)
    conn = connect_db()

    try:
        rows = fetch_lemmas(conn)
        total = len(rows)
        print(f"Found {total} lemmas for {', '.join(LANGUAGES)} in ranks {MIN_RANK}-{MAX_RANK}.")

        total_updated = 0
        total_failed = 0
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        for start in range(0, total, BATCH_SIZE):
            batch = rows[start:start + BATCH_SIZE]
            batch_number = (start // BATCH_SIZE) + 1
            print(f"\nBatch {batch_number}/{total_batches}: {len(batch)} lemmas")

            try:
                updates, failures = process_batch(client, batch)

                if updates:
                    update_definitions(conn, updates)

                    batch_by_id = {row["id"]: row for row in batch}
                    for definition, lemma_id in updates:
                        source_row = batch_by_id[lemma_id]
                        append_jsonl(
                            SUCCESS_LOG,
                            {
                                "id": lemma_id,
                                "lemma": source_row["lemma"],
                                "language": source_row["language"],
                                "rank": source_row["rank"],
                                "definition": definition,
                            },
                        )

                    total_updated += len(updates)

                for failure in failures:
                    append_jsonl(FAILURE_LOG, failure)

                total_failed += len(failures)
                print(f"  Updated: {len(updates)} | Failed: {len(failures)}")

            except KeyboardInterrupt:
                raise
            except Exception as error:
                conn.rollback()
                append_jsonl(
                    FAILURE_LOG,
                    {
                        "batch_start": start,
                        "ids": [row["id"] for row in batch],
                        "error": str(error),
                    },
                )
                total_failed += len(batch)
                print(f"  Batch failed: {error}")

            if SLEEP_SECONDS > 0:
                time.sleep(SLEEP_SECONDS)

        print("\nFinished.")
        print(f"Definitions updated: {total_updated}")
        print(f"Definitions failed: {total_failed}")
        print(f"Success log: {SUCCESS_LOG.resolve()}")
        print(f"Failure log: {FAILURE_LOG.resolve()}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
