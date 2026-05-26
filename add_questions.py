import json
import random
import time
import traceback
import psycopg2

from openai import OpenAI

# Prompt user for keys
DB_PASSWORD = input("Enter database password: ")
OPENAI_KEY = input("Enter OpenAI API key: ")

DB_HOST = "vitamova-db.cluster-cartvcorpihi.us-east-1.rds.amazonaws.com"
DB_NAME = "vitamova"
DB_USER = "admin_app"

# Use a stronger model for offline question-bank generation.
# This is not live user traffic, so quality matters more than ultra-low cost.
MODEL = "gpt-5.1"

TOTAL_QUESTIONS_TARGET = 1500
TARGET_LANGUAGE = "pt"
TARGET_LANGUAGE_NAME = "Portuguese"
TRANSLATION_LANGUAGE = "en"

OPENAI_BATCH_SIZE = 5
MAX_RETRIES = 4
SLEEP_BETWEEN_OPENAI_CALLS = 0.5

LEVEL_RANGES = {
    1: (1, 1500),
    2: (1501, 3000),
    3: (3001, 6000),
    4: (6001, 10000),
    5: (10001, 15000),
    6: (15001, None),
}


def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def get_openai_client():
    return OpenAI(api_key=OPENAI_KEY)


def get_level_targets(total_questions):
    base = total_questions // 6
    remainder = total_questions % 6

    targets = {}

    for level in range(1, 7):
        targets[level] = base

    for level in range(1, remainder + 1):
        targets[level] += 1

    return targets


def get_existing_question_count_for_level(cursor, level):
    min_rank, max_rank = LEVEL_RANGES[level]

    if max_rank is None:
        cursor.execute(
            """
            SELECT COUNT(DISTINCT l.id)
            FROM vocab_test_bank q
            JOIN lemmas l
                ON l.id = q.lemma_id
            WHERE l.language = %s
              AND l.rank >= %s
            """,
            [TARGET_LANGUAGE, min_rank]
        )
    else:
        cursor.execute(
            """
            SELECT COUNT(DISTINCT l.id)
            FROM vocab_test_bank q
            JOIN lemmas l
                ON l.id = q.lemma_id
            WHERE l.language = %s
              AND l.rank BETWEEN %s AND %s
            """,
            [TARGET_LANGUAGE, min_rank, max_rank]
        )

    return cursor.fetchone()[0]


def fetch_lemmas_for_level(cursor, level, count):
    min_rank, max_rank = LEVEL_RANGES[level]

    if max_rank is None:
        cursor.execute(
            """
            SELECT
                l.id,
                l.rank,
                l.lemma,
                l.pos,
                lt.translation,
                l.definition
            FROM lemmas l
            JOIN lemma_translations lt
                ON lt.lemma_id = l.id
                AND lt.native_language = %s
            WHERE l.language = %s
              AND l.rank >= %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM vocab_test_bank q
                  WHERE q.lemma_id = l.id
              )
              AND lt.translation IS NOT NULL
              AND l.definition IS NOT NULL
            ORDER BY RANDOM()
            LIMIT %s
            """,
            [TRANSLATION_LANGUAGE, TARGET_LANGUAGE, min_rank, count]
        )
    else:
        cursor.execute(
            """
            SELECT
                l.id,
                l.rank,
                l.lemma,
                l.pos,
                lt.translation,
                l.definition
            FROM lemmas l
            JOIN lemma_translations lt
                ON lt.lemma_id = l.id
                AND lt.native_language = %s
            WHERE l.language = %s
              AND l.rank BETWEEN %s AND %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM vocab_test_bank q
                  WHERE q.lemma_id = l.id
              )
              AND lt.translation IS NOT NULL
              AND l.definition IS NOT NULL
            ORDER BY RANDOM()
            LIMIT %s
            """,
            [TRANSLATION_LANGUAGE, TARGET_LANGUAGE, min_rank, max_rank, count]
        )

    return cursor.fetchall()


def build_prompt(lemma_rows):
    input_items = []

    for lemma_id, rank, lemma, pos, translation, definition in lemma_rows:
        input_items.append({
            "lemma_id": lemma_id,
            "lemma_rank": rank,
            "lemma": lemma,
            "part_of_speech": pos,
            "translation_en": translation,
            "definition": definition,
        })

    return f"""
You are creating high-quality {TARGET_LANGUAGE_NAME} vocabulary diagnostic questions for intermediate and advanced learners.

For each {TARGET_LANGUAGE_NAME} lemma, create one multiple-choice fill-in-the-blank question entirely in {TARGET_LANGUAGE_NAME}.

The most important requirement:
- Exactly one answer option must be clearly and naturally correct.
- The other three options must be plausible-looking but wrong in meaning.
- If more than one option could naturally fit the blank, the question is invalid and must be rewritten.

Question design rules:
- The question must be a natural {TARGET_LANGUAGE_NAME} sentence with exactly one blank.
- Write the blank exactly as _____.
- The sentence must include a semantic clue that points uniquely to the correct answer.
- Do not write generic sentences where many options could fit.
- Do not rely only on grammar to make distractors wrong.
- Do not make distractors obviously absurd.
- Do not use distractors that are near-synonyms of the correct answer.
- Do not use distractors that are just spelling variants, gender variants, number variants, or conjugation variants of the correct answer.
- Do not include the correct answer anywhere in the question sentence.
- Do not include English anywhere in the question or answer options.
- Keep the sentence short, natural, and clear.
- Prefer everyday contexts, but make the clue strong enough to remove ambiguity.
- The correct_answer must be the target {TARGET_LANGUAGE_NAME} lemma, or the most natural inflected form if the sentence requires inflection.

Inflection and form variety:
- When the target lemma is a verb, adjective, noun, pronoun, determiner, or other word type with multiple natural forms, use the form that best fits the sentence.
- Do not always use the dictionary form of the lemma.
- For verbs, prefer natural conjugated forms when appropriate. For example, the lemma "ser" should sometimes appear as "es", "soy", "son", "era", "fue", etc., depending on the sentence.
- For nouns and adjectives, use natural gender and number forms when appropriate. For example, a lemma may appear in singular, plural, masculine, or feminine form if the sentence calls for it.
- For very common lemmas, especially verbs, avoid overusing the infinitive form unless the sentence naturally requires an infinitive.
- The correct_answer should be the exact form that belongs in the blank, not necessarily the lemma itself.
- The question should still test knowledge of the target lemma, even when the answer is an inflected form.
- Distractors should match the general grammatical shape of the correct answer when possible, but they must be wrong in meaning. For example, if the correct answer is a conjugated verb, the distractors should often also be conjugated verbs.

Before finalizing each item, mentally test all four options in the blank:
1. Put the correct answer into the blank.
2. Put distractor_1 into the blank.
3. Put distractor_2 into the blank.
4. Put distractor_3 into the blank.

Only return the item if the correct answer is the only natural answer.

Bad example:
Question: "Compré _____ manzanas en el mercado."
Options: "varias", "tres", "ninguna", "pocas"
Why bad: varias, tres, and pocas can all fit naturally.

Good example:
Question: "Compré _____ manzanas; no recuerdo el número exacto."
Correct answer: "varias"
Distractors: "tres", "ninguna", "cada"
Why good: "no recuerdo el número exacto" points uniquely toward an indefinite quantity.

Bad example:
Question: "El _____ requiere presupuesto y un equipo dedicado."
Options: "proyecto", "evento", "contrato", "curso"
Why bad: proyecto, evento, and curso can all fit naturally.

Good example:
Question: "El _____ tendrá varias fases, fechas límite y un equipo dedicado."
Correct answer: "proyecto"
Distractors: "contrato", "evento", "curso"
Why good: "varias fases" and "fechas límite" point most clearly to proyecto.

Good example with an inflected verb:
Question: "Mi hermana _____ médica y trabaja en un hospital."
Correct answer: "es"
Distractors: "tiene", "hace", "va"
Why good: The target lemma is "ser", but the natural form in this sentence is "es", not "ser".

Bad example with an overused dictionary form:
Question: "Mi hermana quiere _____ médica en el futuro."
Correct answer: "ser"
Distractors: "tener", "hacer", "ir"
Why weaker: This is grammatically fine, but overusing infinitives makes the questions less natural and less varied. Use conjugated forms when they fit naturally.

Output requirements:
- Return only valid JSON.
- Return a JSON object with one key: "results".
- "results" must be an array.
- Return exactly one result for each input lemma.
- Each object inside "results" must include:
  - lemma_id
  - lemma_rank
  - question
  - correct_answer
  - distractor_1
  - distractor_2
  - distractor_3
  - ambiguity_check

The ambiguity_check field must be a short {TARGET_LANGUAGE_NAME} or English explanation of why only the correct answer fits naturally.
Do not include any markdown.

Input lemmas:
{json.dumps(input_items, ensure_ascii=False)}
"""


def validate_generated_questions(results, expected_lemma_ids):
    if not isinstance(results, list):
        raise ValueError("OpenAI response results was not a list.")

    required_fields = [
        "lemma_id",
        "lemma_rank",
        "question",
        "correct_answer",
        "distractor_1",
        "distractor_2",
        "distractor_3",
        "ambiguity_check",
    ]

    returned_lemma_ids = set()

    for item in results:
        for field in required_fields:
            if field not in item:
                raise ValueError(f"Missing field: {field}")

            if item[field] is None or str(item[field]).strip() == "":
                raise ValueError(f"Empty field: {field}")

        lemma_id = int(item["lemma_id"])
        lemma_rank = int(item["lemma_rank"])

        returned_lemma_ids.add(lemma_id)

        question = str(item["question"]).strip()
        correct_answer = str(item["correct_answer"]).strip()
        distractor_1 = str(item["distractor_1"]).strip()
        distractor_2 = str(item["distractor_2"]).strip()
        distractor_3 = str(item["distractor_3"]).strip()
        ambiguity_check = str(item["ambiguity_check"]).strip()

        if "_____" not in question:
            raise ValueError(f"Question for lemma_id {lemma_id} does not include _____.")

        if question.count("_____") != 1:
            raise ValueError(f"Question for lemma_id {lemma_id} does not have exactly one blank.")

        if correct_answer.casefold() in question.casefold():
            raise ValueError(f"Question for lemma_id {lemma_id} appears to contain the correct answer.")

        options = [
            correct_answer,
            distractor_1,
            distractor_2,
            distractor_3,
        ]

        normalized_options = [
            option.strip().casefold()
            for option in options
        ]

        if len(set(normalized_options)) != 4:
            raise ValueError(f"Duplicate answer options for lemma_id {lemma_id}.")

        bad_ambiguity_phrases = [
            "more than one",
            "multiple",
            "ambiguous",
            "could fit",
            "also fits",
            f"também se encaixa",
            f"mais de uma",
            f"várias opções",
            f"ambíguo",
            f"ambígua",
        ]

        ambiguity_lower = ambiguity_check.casefold()

        for phrase in bad_ambiguity_phrases:
            if phrase in ambiguity_lower:
                raise ValueError(
                    f"Ambiguity check for lemma_id {lemma_id}, rank {lemma_rank}, suggests the item may be ambiguous: {ambiguity_check}"
                )

        if len(question.split()) < 5:
            raise ValueError(f"Question for lemma_id {lemma_id} is too short.")

        if len(correct_answer.split()) > 4:
            raise ValueError(f"Correct answer for lemma_id {lemma_id} is too long.")

        for option in options:
            if len(option.split()) > 4:
                raise ValueError(f"Option for lemma_id {lemma_id} is too long: {option}")

    missing_lemma_ids = expected_lemma_ids - returned_lemma_ids
    unexpected_lemma_ids = returned_lemma_ids - expected_lemma_ids

    if missing_lemma_ids:
        raise ValueError(f"Missing lemma IDs: {sorted(missing_lemma_ids)}")

    if unexpected_lemma_ids:
        raise ValueError(f"Unexpected lemma IDs: {sorted(unexpected_lemma_ids)}")


def build_review_prompt(generated_questions):
    return f"""
You are reviewing {TARGET_LANGUAGE_NAME} fill-in-the-blank vocabulary diagnostic questions.

Your task:
- Identify whether each question has exactly one clearly correct answer.
- Reject any item where two or more options can naturally fit the blank.
- Reject any item where the correct answer is only correct because of grammar and not meaning.
- Reject any item where the distractors are too absurd or too obviously wrong.
- Reject any item where the sentence is too generic.
- Accept only questions that are fair diagnostic questions.

Return only valid JSON.

Output format:
{{
  "reviews": [
    {{
      "lemma_id": 123,
      "lemma_rank": 123,
      "is_valid": true,
      "reason": "Only the correct answer fits because..."
    }}
  ]
}}

Questions to review:
{json.dumps(generated_questions, ensure_ascii=False)}
"""


def review_questions_with_openai(client, generated_questions):
    if not generated_questions:
        return []

    expected_lemma_ids = {int(item["lemma_id"]) for item in generated_questions}
    prompt = build_review_prompt(generated_questions)

    response = client.responses.create(
        model=MODEL,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "vocab_diagnostic_question_reviews",
                "schema": {
                    "type": "object",
                    "properties": {
                        "reviews": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "lemma_id": {"type": "integer"},
                                    "lemma_rank": {"type": "integer"},
                                    "is_valid": {"type": "boolean"},
                                    "reason": {"type": "string"},
                                },
                                "required": [
                                    "lemma_id",
                                    "lemma_rank",
                                    "is_valid",
                                    "reason",
                                ],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["reviews"],
                    "additionalProperties": False,
                },
            }
        },
    )

    parsed = json.loads(response.output_text)
    reviews = parsed["reviews"]

    returned_lemma_ids = {int(item["lemma_id"]) for item in reviews}

    missing_lemma_ids = expected_lemma_ids - returned_lemma_ids
    unexpected_lemma_ids = returned_lemma_ids - expected_lemma_ids

    if missing_lemma_ids:
        raise ValueError(f"Review response missing lemma IDs: {sorted(missing_lemma_ids)}")

    if unexpected_lemma_ids:
        raise ValueError(f"Review response included unexpected lemma IDs: {sorted(unexpected_lemma_ids)}")

    review_lookup = {
        int(item["lemma_id"]): item
        for item in reviews
    }

    valid_questions = []

    for question in generated_questions:
        lemma_id = int(question["lemma_id"])
        lemma_rank = int(question["lemma_rank"])
        review = review_lookup[lemma_id]

        if review["is_valid"]:
            valid_questions.append(question)
        else:
            print(f"Rejected lemma_id {lemma_id}, rank {lemma_rank}: {review['reason']}")

    return valid_questions


def generate_questions_with_openai(client, lemma_rows):
    expected_lemma_ids = {int(row[0]) for row in lemma_rows}
    prompt = build_prompt(lemma_rows)

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.responses.create(
                model=MODEL,
                input=prompt,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "vocab_diagnostic_questions",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "results": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "lemma_id": {"type": "integer"},
                                            "lemma_rank": {"type": "integer"},
                                            "question": {"type": "string"},
                                            "correct_answer": {"type": "string"},
                                            "distractor_1": {"type": "string"},
                                            "distractor_2": {"type": "string"},
                                            "distractor_3": {"type": "string"},
                                            "ambiguity_check": {"type": "string"},
                                        },
                                        "required": [
                                            "lemma_id",
                                            "lemma_rank",
                                            "question",
                                            "correct_answer",
                                            "distractor_1",
                                            "distractor_2",
                                            "distractor_3",
                                            "ambiguity_check",
                                        ],
                                        "additionalProperties": False,
                                    },
                                }
                            },
                            "required": ["results"],
                            "additionalProperties": False,
                        },
                    }
                },
            )

            parsed = json.loads(response.output_text)
            results = parsed["results"]

            validate_generated_questions(results, expected_lemma_ids)

            reviewed_results = review_questions_with_openai(client, results)

            if len(reviewed_results) != len(results):
                rejected_count = len(results) - len(reviewed_results)
                raise ValueError(f"Review rejected {rejected_count} generated question(s). Retrying batch.")

            cleaned_results = []

            for item in reviewed_results:
                cleaned_results.append({
                    "lemma_id": item["lemma_id"],
                    "lemma_rank": item["lemma_rank"],
                    "question": item["question"],
                    "correct_answer": item["correct_answer"],
                    "distractor_1": item["distractor_1"],
                    "distractor_2": item["distractor_2"],
                    "distractor_3": item["distractor_3"],
                })

            return cleaned_results

        except Exception as e:
            last_error = e

            print(f"\nOpenAI generation/review failed. Attempt {attempt}/{MAX_RETRIES}")
            print("Error type:", type(e).__name__)
            print("Error:", e)
            traceback.print_exc()

            if attempt < MAX_RETRIES:
                sleep_seconds = (2 ** (attempt - 1)) + random.uniform(0, 0.75)
                print(f"Retrying in {sleep_seconds:.2f} seconds...")
                time.sleep(sleep_seconds)

    raise last_error


def insert_questions(cursor, generated_questions):
    inserted_count = 0

    for item in generated_questions:
        cursor.execute(
            """
            INSERT INTO vocab_test_bank (
                lemma_id,
                question,
                correct_answer,
                distractor_1,
                distractor_2,
                distractor_3
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                item["lemma_id"],
                item["question"],
                item["correct_answer"],
                item["distractor_1"],
                item["distractor_2"],
                item["distractor_3"],
            ]
        )

        inserted_count += 1

    return inserted_count


def chunk_list(items, chunk_size):
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]


def main():
    client = get_openai_client()
    conn = get_db_connection()
    cursor = conn.cursor()

    targets = get_level_targets(TOTAL_QUESTIONS_TARGET)

    print("Question generation target:")
    print(f"Target language: {TARGET_LANGUAGE}")
    print(f"Translation language: {TRANSLATION_LANGUAGE}")

    for level, target in targets.items():
        print(f"  Level {level}: {target}")

    total_inserted = 0

    try:
        for level in range(1, 7):
            target_for_level = targets[level]
            existing_count = get_existing_question_count_for_level(cursor, level)
            remaining_for_level = max(target_for_level - existing_count, 0)

            print("\n----------------------------------------")
            print(f"Level {level}")
            print(f"Target: {target_for_level}")
            print(f"Existing questions: {existing_count}")
            print(f"Need to generate: {remaining_for_level}")

            if remaining_for_level == 0:
                continue

            lemma_rows = fetch_lemmas_for_level(
                cursor=cursor,
                level=level,
                count=remaining_for_level
            )

            if not lemma_rows:
                print(f"No eligible lemmas found for level {level}. Skipping.")
                continue

            print(f"Fetched {len(lemma_rows)} lemmas for level {level}.")

            for batch_number, lemma_batch in enumerate(chunk_list(lemma_rows, OPENAI_BATCH_SIZE), start=1):
                print(
                    f"Level {level}, batch {batch_number}: "
                    f"generating {len(lemma_batch)} questions..."
                )

                try:
                    generated_questions = generate_questions_with_openai(client, lemma_batch)

                    inserted_count = insert_questions(cursor, generated_questions)
                    conn.commit()

                    total_inserted += inserted_count

                    print(
                        f"Committed {inserted_count} questions. "
                        f"Total inserted this run: {total_inserted}"
                    )

                    time.sleep(SLEEP_BETWEEN_OPENAI_CALLS)

                except Exception as e:
                    conn.rollback()

                    print("\nFailed to generate/review/insert this batch. Rolled back this batch only.")
                    print("Continuing to next batch.")
                    print("Error:", e)
                    traceback.print_exc()

                    continue

        print("\nDone.")
        print(f"Total inserted this run: {total_inserted}")

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()