#!/usr/bin/env python3

import json
import re
import time
from pathlib import Path
from typing import Optional

import psycopg2
from openai import OpenAI
from pydantic import BaseModel, Field


DB_PASSWORD = input("Enter database password: ")
OPENAI_KEY = input("Enter OpenAI API key: ")

DB_HOST = "vitamova-db.cluster-cartvcorpihi.us-east-1.rds.amazonaws.com"
DB_NAME = "vitamova"
DB_USER = "admin_app"
DB_PORT = 5432

LANGUAGES = ("ru", "es", "pt")
MIN_RANK = 3000
MAX_RANK = 6000

GENERATOR_MODEL = "gpt-5.4-mini"
REVIEWER_MODEL = "gpt-5.4"

MAX_ATTEMPTS_PER_LEMMA = 3
RETRY_DELAY_SECONDS = 2
LIMIT: Optional[int] = 10

SUCCESS_LOG = Path("sentence_generation_successes.jsonl")
FAILURE_LOG = Path("sentence_generation_failures.jsonl")


class SentenceCandidate(BaseModel):
    sentence: str = Field(description="One natural example sentence in the target language.")
    word: str = Field(
        description=(
            "The exact surface form of the target lemma used in the sentence. "
            "This must appear verbatim in the sentence."
        )
    )


class ReviewResult(BaseModel):
    approved: bool = Field(description="True only when the candidate fully satisfies all requirements.")
    issues: list[str] = Field(
        default_factory=list,
        description="Specific defects found in the candidate. Empty when approved."
    )
    sentence: str = Field(
        description=(
            "The approved sentence. If the original needed correction, provide a "
            "fully corrected replacement sentence."
        )
    )
    word: str = Field(description="The exact surface form used in the approved/revised sentence.")


LANGUAGE_NAMES = {
    "es": "Spanish",
    "pt": "Portuguese",
    "ru": "Russian",
}

ARTICLE_GUIDANCE = {
    "es": (
        "If the lemma is a noun, use a natural Spanish article immediately before "
        "the exact noun form, such as el, la, los, las, un, una, unos, or unas."
    ),
    "pt": (
        "If the lemma is a noun, use a natural Portuguese article immediately before "
        "the exact noun form, such as o, a, os, as, um, uma, uns, or umas."
    ),
    "ru": (
        "Russian has no articles. Do not invent or require an article for a Russian noun."
    ),
}

SPANISH_ARTICLES = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "al", "del"
}
PORTUGUESE_ARTICLES = {
    "o", "a", "os", "as", "um", "uma", "uns", "umas",
    "ao", "aos", "à", "às", "do", "da", "dos", "das",
    "no", "na", "nos", "nas"
}


def append_jsonl(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def is_noun(pos: Optional[str]) -> bool:
    if not pos:
        return False

    normalized = pos.casefold().strip()
    noun_markers = ("noun", "sustantivo", "substantivo", "существ", "nombre")
    return any(marker in normalized for marker in noun_markers)


def contains_exact_word(sentence: str, word: str) -> bool:
    if not sentence or not word:
        return False

    pattern = rf"(?<!\w){re.escape(word)}(?!\w)"
    return re.search(pattern, sentence, flags=re.IGNORECASE | re.UNICODE) is not None


def noun_has_article(sentence: str, word: str, language: str) -> bool:
    if language == "ru":
        return True

    articles = SPANISH_ARTICLES if language == "es" else PORTUGUESE_ARTICLES
    article_pattern = "|".join(
        sorted((re.escape(article) for article in articles), key=len, reverse=True)
    )

    pattern = rf"(?<!\w)(?:{article_pattern})\s+{re.escape(word)}(?!\w)"
    return re.search(pattern, sentence, flags=re.IGNORECASE | re.UNICODE) is not None


def validate_candidate(
    candidate: SentenceCandidate | ReviewResult,
    lemma: str,
    pos: Optional[str],
    language: str,
) -> list[str]:
    errors: list[str] = []

    sentence = candidate.sentence.strip()
    word = candidate.word.strip()

    if not sentence:
        errors.append("The sentence is empty.")
    if not word:
        errors.append("The exact surface word is empty.")

    if sentence and word and not contains_exact_word(sentence, word):
        errors.append(
            f'The exact word "{word}" does not appear as a standalone form in the sentence.'
        )

    if is_noun(pos) and language in {"es", "pt"}:
        if not noun_has_article(sentence, word, language):
            errors.append(
                f'The noun "{word}" is not immediately preceded by an appropriate article.'
            )

    if word.casefold() == lemma.casefold() and pos:
        normalized_pos = pos.casefold()
        if any(marker in normalized_pos for marker in ("verb", "verbo", "глаг")):
            errors.append(
                "A verb was used in its lemma/infinitive form rather than a natural conjugated form."
            )

    return errors


def generate_candidate(
    client: OpenAI,
    lemma_row: dict,
    previous_feedback: Optional[list[str]] = None,
) -> SentenceCandidate:
    language = lemma_row["language"]
    language_name = LANGUAGE_NAMES[language]

    feedback_text = ""
    if previous_feedback:
        feedback_text = (
            "\nA previous attempt was rejected for these reasons:\n- "
            + "\n- ".join(previous_feedback)
            + "\nCorrect every issue in the new attempt."
        )

    prompt = f"""
Create one high-quality example sentence for a language-learning application.

TARGET LANGUAGE: {language_name}
LEMMA: {lemma_row["lemma"]}
PART OF SPEECH: {lemma_row.get("pos") or "unknown"}
DICTIONARY DEFINITION: {lemma_row.get("definition") or "not provided"}
FREQUENCY RANK: {lemma_row["rank"]}

Requirements:

1. Write exactly one natural, grammatical sentence in {language_name}.
2. The sentence must make the target word highly meaningful in context. A learner
   should be able to infer or strongly narrow down its meaning from the situation.
3. Do NOT write a disguised dictionary definition. Do not use patterns like
   '"X means..."', '"X is a..."', or a sentence whose only purpose is to define X.
4. The target word should be central to what happens or is communicated in the sentence.
5. Use a natural inflected or derived surface form instead of the lemma whenever possible.
   This is HIGHLY ENCOURAGED and should happen in the VAST MAJORITY of examples.
6. For verbs, use a natural conjugated form rather than the infinitive/lemma except in
   the rare case where an infinitive is genuinely the most natural contextual use.
7. For nouns, plural, case, or other natural forms are encouraged when appropriate.
8. For adjectives or other inflecting words, use a natural non-dictionary form when
   the language and context allow it.
9. {ARTICLE_GUIDANCE[language]}
10. Return the exact surface word appearing in the sentence in the `word` field.
11. Keep the sentence understandable and useful to an intermediate learner.
12. Do not use quotation marks merely to mention the word.
13. Do not include translations, explanations, or extra sentences.
{feedback_text}
""".strip()

    response = client.responses.parse(
        model=GENERATOR_MODEL,
        reasoning={"effort": "low"},
        input=[
            {
                "role": "developer",
                "content": (
                    "You are an expert lexicographer and language-learning content "
                    "writer for Spanish, Portuguese, and Russian."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        text_format=SentenceCandidate,
    )

    if response.output_parsed is None:
        raise RuntimeError("Generator did not return a parsed candidate.")

    return response.output_parsed


def review_candidate(
    client: OpenAI,
    lemma_row: dict,
    candidate: SentenceCandidate,
) -> ReviewResult:
    language = lemma_row["language"]
    language_name = LANGUAGE_NAMES[language]

    prompt = f"""
Review the proposed example sentence rigorously.

TARGET LANGUAGE: {language_name}
LEMMA: {lemma_row["lemma"]}
PART OF SPEECH: {lemma_row.get("pos") or "unknown"}
DICTIONARY DEFINITION: {lemma_row.get("definition") or "not provided"}

PROPOSED SENTENCE: {candidate.sentence}
PROPOSED EXACT WORD: {candidate.word}

Approval requirements:

1. The sentence must be fully natural and grammatical in {language_name}.
2. The exact value returned as `word` must occur in the sentence.
3. The word must be used with the intended lemma meaning.
4. The context must make the word's meaning inferable or strongly understandable.
5. The sentence must NOT simply state or paraphrase the dictionary definition.
6. The target word must be important to the sentence rather than incidental.
7. A non-lemma surface form is HIGHLY preferred and should be used in the vast
   majority of examples.
8. If the lemma is a verb, reject an unnecessary infinitive/lemma form and revise
   it into a natural conjugated form.
9. {ARTICLE_GUIDANCE[language]}
10. The output must contain one sentence only and no translation or explanation.

If every requirement is met, set approved=true and return the candidate unchanged.
If anything can be repaired, set approved=false, list the issues, and return a fully
corrected sentence and exact word in the remaining fields.
Do not merely criticize the candidate; always supply the best corrected version.
""".strip()

    response = client.responses.parse(
        model=REVIEWER_MODEL,
        reasoning={"effort": "medium"},
        input=[
            {
                "role": "developer",
                "content": (
                    "You are a strict senior reviewer of multilingual language-learning "
                    "content. Accuracy and natural usage matter more than preserving the draft."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        text_format=ReviewResult,
    )

    if response.output_parsed is None:
        raise RuntimeError("Reviewer did not return a parsed result.")

    return response.output_parsed


def load_target_lemmas(conn) -> list[dict]:
    sql = """
        SELECT DISTINCT
            l.id,
            l.lemma,
            l.language,
            l.rank,
            l.pos,
            l.definition
        FROM vocab_test_bank vtb
        JOIN lemmas l
            ON l.id = vtb.lemma_id
        WHERE l.rank BETWEEN %s AND %s
          AND l.language = ANY(%s)
          AND NOT EXISTS (
              SELECT 1
              FROM sentences s
              JOIN words w
                  ON w.id = s.word_id
              WHERE w.lemma_id = l.id
                AND s.language = l.language
          )
        ORDER BY l.language, l.rank, l.id
    """

    params: list[object] = [MIN_RANK, MAX_RANK, list(LANGUAGES)]

    if LIMIT is not None:
        sql += "\nLIMIT %s"
        params.append(LIMIT)

    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    return [
        {
            "id": row[0],
            "lemma": row[1],
            "language": row[2].strip(),
            "rank": row[3],
            "pos": row[4],
            "definition": row[5],
        }
        for row in rows
    ]


def get_or_create_word(
    cursor,
    lemma_id: int,
    language: str,
    word: str,
) -> int:
    cursor.execute(
        """
        SELECT id
        FROM words
        WHERE lemma_id = %s
        AND language = %s
        AND word = %s
        AND form IS NULL
        AND gender IS NULL
        ORDER BY id
        LIMIT 1
        """,
        [lemma_id, language, word],
    )

    row = cursor.fetchone()
    if row:
        return row[0]

    cursor.execute(
        """
        INSERT INTO words (
            word,
            gender,
            form,
            language,
            lemma_id
        )
        VALUES (%s, NULL, NULL, %s, %s)
        RETURNING id
        """,
        [word, language, lemma_id],
    )
    return cursor.fetchone()[0]


def insert_sentence_and_word(
    conn,
    lemma_row: dict,
    reviewed: ReviewResult,
) -> tuple[int, int]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT s.id
            FROM sentences s
            JOIN words w
                ON w.id = s.word_id
            WHERE w.lemma_id = %s
              AND s.language = %s
            ORDER BY s.id
            LIMIT 1
            """,
            [lemma_row["id"], lemma_row["language"]],
        )

        existing = cursor.fetchone()
        if existing:
            return existing[0], 0

        word_id = get_or_create_word(
            cursor=cursor,
            lemma_id=lemma_row["id"],
            language=lemma_row["language"],
            word=reviewed.word.strip(),
        )

        cursor.execute(
            """
            INSERT INTO sentences (
                sentence,
                word_id,
                language
            )
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            [reviewed.sentence.strip(), word_id, lemma_row["language"]],
        )

        sentence_id = cursor.fetchone()[0]

    conn.commit()
    return sentence_id, word_id


def main() -> None:
    client = OpenAI(api_key=OPENAI_KEY)

    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        sslmode="require",
    )

    try:
        lemmas = load_target_lemmas(conn)
        total = len(lemmas)

        print(
            f"Found {total} eligible lemmas in ranks "
            f"{MIN_RANK}-{MAX_RANK} for {', '.join(LANGUAGES)}."
        )

        inserted = 0
        skipped = 0
        failed = 0

        for index, lemma_row in enumerate(lemmas, start=1):
            print(
                f'[{index}/{total}] {lemma_row["language"]} '
                f'rank={lemma_row["rank"]} '
                f'id={lemma_row["id"]} lemma={lemma_row["lemma"]!r}'
            )

            previous_feedback: Optional[list[str]] = None
            completed = False

            for attempt in range(1, MAX_ATTEMPTS_PER_LEMMA + 1):
                try:
                    candidate = generate_candidate(
                        client=client,
                        lemma_row=lemma_row,
                        previous_feedback=previous_feedback,
                    )

                    local_generator_errors = validate_candidate(
                        candidate=candidate,
                        lemma=lemma_row["lemma"],
                        pos=lemma_row["pos"],
                        language=lemma_row["language"],
                    )

                    if local_generator_errors:
                        previous_feedback = local_generator_errors
                        print(
                            f"  Attempt {attempt}: generator failed local validation: "
                            + "; ".join(local_generator_errors)
                        )
                        continue

                    review = review_candidate(
                        client=client,
                        lemma_row=lemma_row,
                        candidate=candidate,
                    )

                    local_review_errors = validate_candidate(
                        candidate=review,
                        lemma=lemma_row["lemma"],
                        pos=lemma_row["pos"],
                        language=lemma_row["language"],
                    )

                    if local_review_errors:
                        previous_feedback = review.issues + local_review_errors
                        print(
                            f"  Attempt {attempt}: reviewed output failed validation: "
                            + "; ".join(local_review_errors)
                        )
                        continue

                    if not review.approved:
                        print(
                            f"  Attempt {attempt}: reviewer repaired the draft: "
                            + "; ".join(review.issues or ["unspecified improvements"])
                        )

                    sentence_id, word_id = insert_sentence_and_word(
                        conn=conn,
                        lemma_row=lemma_row,
                        reviewed=review,
                    )

                    if word_id == 0:
                        skipped += 1
                        print(
                            f"  Skipped: a sentence already exists "
                            f"(sentence_id={sentence_id})."
                        )
                    else:
                        inserted += 1
                        print(
                            f"  Inserted sentence_id={sentence_id}, word_id={word_id}: "
                            f"{review.sentence}"
                        )

                        append_jsonl(
                            SUCCESS_LOG,
                            {
                                **lemma_row,
                                "sentence_id": sentence_id,
                                "word_id": word_id,
                                "sentence": review.sentence,
                                "word": review.word,
                                "reviewer_approved_original": review.approved,
                                "reviewer_issues": review.issues,
                            },
                        )

                    completed = True
                    break

                except KeyboardInterrupt:
                    raise
                except Exception as error:
                    conn.rollback()
                    previous_feedback = [f"Technical or model-processing failure: {error}"]
                    print(f"  Attempt {attempt} failed: {error}")

                    if attempt < MAX_ATTEMPTS_PER_LEMMA:
                        time.sleep(RETRY_DELAY_SECONDS)

            if not completed:
                failed += 1
                append_jsonl(
                    FAILURE_LOG,
                    {
                        **lemma_row,
                        "error": "Maximum attempts exhausted.",
                        "last_feedback": previous_feedback,
                    },
                )
                print("  FAILED after maximum attempts; logged for later review.")

        print()
        print("Finished.")
        print(f"Inserted: {inserted}")
        print(f"Skipped because already present: {skipped}")
        print(f"Failed: {failed}")
        print(f"Success log: {SUCCESS_LOG.resolve()}")
        print(f"Failure log: {FAILURE_LOG.resolve()}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
