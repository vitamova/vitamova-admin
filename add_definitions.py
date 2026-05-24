#!/usr/bin/env python3

import argparse
import getpass
import json
import sys
import time
from typing import Any

import psycopg2
import psycopg2.extras
from openai import OpenAI


NATIVE_LANGUAGE_FOR_TRANSLATIONS = "en"


def get_args():
    parser = argparse.ArgumentParser(
        description="Fill missing lemma definitions and English translations using OpenAI."
    )

    parser.add_argument("--db-host", default="vitamova-db.cluster-cartvcorpihi.us-east-1.rds.amazonaws.com")
    parser.add_argument("--db-port", default=5432, type=int)
    parser.add_argument("--db-name", default="vitamova", required=True)
    parser.add_argument("--db-user", default="admin_app")
    parser.add_argument("--batch-size", default=25, type=int)
    parser.add_argument("--limit", default=None, type=int)
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--sleep-seconds", default=0.5, type=float)
    parser.add_argument("--dry-run", action="store_true")

    return parser.parse_args()


def connect_db(args, db_password):
    return psycopg2.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=db_password,
    )


def fetch_lemmas_to_process(conn, batch_size):
    """
    Fetch lemmas where:
    - definition is NULL, OR
    - no English translation exists in lemma_translations.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT
                l.id,
                l.lemma,
                l.language,
                l.definition,
                CASE
                    WHEN l.language = 'en' THEN false
                    WHEN lt.id IS NULL THEN true
                    ELSE false
                END AS needs_translation,
                CASE
                    WHEN l.definition IS NULL THEN true
                    ELSE false
                END AS needs_definition
            FROM lemmas l
            LEFT JOIN lemma_translations lt
                ON lt.lemma_id = l.id
               AND lt.native_language = %s
            WHERE l.definition IS NULL
               OR lt.id IS NULL
            ORDER BY l.id
            LIMIT %s
            """,
            [NATIVE_LANGUAGE_FOR_TRANSLATIONS, batch_size],
        )

        return cursor.fetchall()


def build_prompt(rows):
    compact_rows = []

    for row in rows:
        compact_rows.append(
            {
                "id": row["id"],
                "lemma": row["lemma"],
                "language": row["language"],
                "needs_definition": row["needs_definition"],
                "needs_translation": row["needs_translation"],
            }
        )

    return f"""
You are helping populate a language-learning database.

For each lemma below:
1. If needs_definition is true, write a brief but complete definition in the lemma's own language.
   - The language code is in the "language" field.
   - The definition should be suitable for a learner.
   - Keep it concise, but complete enough to distinguish the word.
2. If needs_translation is true, write an English translation.
   - Prefer one English word where possible.
   - Use multiple English words separated by comma and a space only when needed to capture important meanings or make the meaning clearer.
   - Do not include long explanations in the translation field.

Return only valid JSON matching the provided schema.

Lemmas:
{json.dumps(compact_rows, ensure_ascii=False, indent=2)}
""".strip()


def call_openai(client, model, rows):
    prompt = build_prompt(rows)

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "You produce concise dictionary-style data for a language-learning app. "
                    "Return only the requested structured data."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "lemma_updates",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "id": {"type": "integer"},
                                    "definition": {
                                        "anyOf": [
                                            {"type": "string"},
                                            {"type": "null"},
                                        ]
                                    },
                                    "translation": {
                                        "anyOf": [
                                            {"type": "string"},
                                            {"type": "null"},
                                        ]
                                    },
                                },
                                "required": ["id", "definition", "translation"],
                            },
                        }
                    },
                    "required": ["items"],
                },
            }
        },
    )

    return json.loads(response.output_text)


def clean_text(value: Any):
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    return value


def apply_updates(conn, rows, openai_data, dry_run=False):
    rows_by_id = {int(row["id"]): row for row in rows}

    items = openai_data.get("items", [])

    if not isinstance(items, list):
        raise ValueError("OpenAI response did not include an items list.")

    definition_updates = []
    translation_inserts = []

    for item in items:
        lemma_id = int(item["id"])

        if lemma_id not in rows_by_id:
            print(f"Skipping unexpected lemma id from OpenAI: {lemma_id}")
            continue

        source_row = rows_by_id[lemma_id]

        definition = clean_text(item.get("definition"))
        translation = clean_text(item.get("translation"))

        if source_row["needs_definition"] and definition:
            definition_updates.append((definition, lemma_id))

        if (
            source_row["needs_translation"]
            and source_row["language"] != "en"
            and translation
        ):
            translation_inserts.append(
                (
                    lemma_id,
                    NATIVE_LANGUAGE_FOR_TRANSLATIONS,
                    translation,
                )
            )

    if dry_run:
        print("\nDRY RUN: would update definitions:")
        for definition, lemma_id in definition_updates:
            print(f"  lemma_id={lemma_id}: {definition}")

        print("\nDRY RUN: would insert translations:")
        for lemma_id, native_language, translation in translation_inserts:
            print(f"  lemma_id={lemma_id}, native_language={native_language}: {translation}")

        return len(definition_updates), len(translation_inserts)

    with conn.cursor() as cursor:
        if definition_updates:
            psycopg2.extras.execute_batch(
                cursor,
                """
                UPDATE lemmas
                SET definition = %s
                WHERE id = %s
                  AND definition IS NULL
                """,
                definition_updates,
                page_size=100,
            )

        if translation_inserts:
            psycopg2.extras.execute_batch(
                cursor,
                """
                INSERT INTO lemma_translations (
                    lemma_id,
                    native_language,
                    translation
                )
                VALUES (
                    %s,
                    %s,
                    %s
                )
                ON CONFLICT (lemma_id, native_language)
                DO NOTHING
                """,
                translation_inserts,
                page_size=100,
            )

    conn.commit()

    return len(definition_updates), len(translation_inserts)


def main():
    args = get_args()

    openai_key = getpass.getpass("OpenAI API key: ")
    db_password = getpass.getpass(f"Database password for {args.db_user}: ")

    client = OpenAI(api_key=openai_key)

    processed_lemmas = 0
    total_definition_updates = 0
    total_translation_inserts = 0

    try:
        conn = connect_db(args, db_password)
    except Exception as error:
        print(f"Could not connect to database: {error}", file=sys.stderr)
        sys.exit(1)

    try:
        while True:
            if args.limit is not None:
                remaining = args.limit - processed_lemmas

                if remaining <= 0:
                    break

                batch_size = min(args.batch_size, remaining)
            else:
                batch_size = args.batch_size

            rows = fetch_lemmas_to_process(conn, batch_size)

            if not rows:
                print("No more missing definitions or English translations found.")
                break

            print(f"\nProcessing batch of {len(rows)} lemmas...")
            print("Lemma IDs:", ", ".join(str(row["id"]) for row in rows))

            try:
                openai_data = call_openai(client, args.model, rows)
                definition_count, translation_count = apply_updates(
                    conn,
                    rows,
                    openai_data,
                    dry_run=args.dry_run,
                )

                processed_lemmas += len(rows)
                total_definition_updates += definition_count
                total_translation_inserts += translation_count

                print(
                    f"Batch complete. Definitions updated: {definition_count}. "
                    f"Translations inserted: {translation_count}."
                )

            except Exception as error:
                conn.rollback()
                print(f"Error processing batch: {error}", file=sys.stderr)
                print("Stopping so you can inspect the issue safely.", file=sys.stderr)
                sys.exit(1)

            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

        print("\nDone.")
        print(f"Lemmas processed: {processed_lemmas}")
        print(f"Definitions updated: {total_definition_updates}")
        print(f"English translations inserted: {total_translation_inserts}")

        if args.dry_run:
            print("\nDRY RUN was enabled. No database changes were made.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()