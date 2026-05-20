import ast
import re
import unicodedata
from pathlib import Path
import psycopg2


BASE_DIR = Path(__file__).resolve().parent.parent

input_log_path = BASE_DIR / "ua_lemma_unknown_pos_remaining_2.log"
remaining_log_path = BASE_DIR / "ua_lemma_unknown_pos_remaining_3.log"
processed_log_path = BASE_DIR / "ua_lemma_unknown_pos_processed_3.log"


# Fill this in manually as you identify POS values.
# Left side = raw Ukrainian POS from the log.
# Right side = value you want stored in ua_lemmas.pos.
MANUAL_POS_MAP = {
    "дієприслівник": "adverbial_participle",
    "іменник чоловічого або середнього роду, істота": "noun_masculine_or_neuter_animate",
    "сполука": "compound",
    "дієприкметник з прислівником": "participle_with_adverb",
    "вигук": "interjection",
    "частка": "particle",
    "займенник": "pronoun",
    "іменник з прийменником": "noun_with_preposition",
    "вставне слово": "parenthetical_word",
    "числівник кількісний": "numeral_cardinal",
    "незмінне слово": "indeclinable",
    "числівник порядковий": "numeral_ordinal",
    "іменник жіночого або середнього роду": "noun_feminine_or_neuter",
    "іменник чоловічого або середнього роду": "noun_masculine_or_neuter",
    "сполучник і частка": "conjunction_and_particle",
    "прислівник і частка": "adverb_and_particle",
    "прислівник з частками": "adverb_with_particles",
    "числівник": "numeral",
    "числівник порядковий": "numeral_ordinal",
    "числівник типу \"два\"": "numeral_dual",
    "іменник чоловічого або жіночого роду": "noun_masculine_or_feminine",
    "іменник жіночого або чоловічого роду": "noun_feminine_or_masculine",
    "іменник жіночого або чоловічого роду, істота": "noun_feminine_or_masculine_animate",
    "іменник жіночого або середнього роду, істота": "noun_feminine_or_neuter_animate",
    "дієприслівник з часткою": "adverbial_participle_with_particle",
}


class LemmaParseError(Exception):
    pass


def remove_ukrainian_stress_marks(text):
    normalized = unicodedata.normalize("NFD", text)
    without_stress = "".join(
        ch for ch in normalized
        if ch != "\u0301"
    )
    return unicodedata.normalize("NFC", without_stress)


def extract_raw_pos_from_error(error_text):
    prefix = "Unexpected part of speech: "

    if not error_text.startswith(prefix):
        raise LemmaParseError(f"Unexpected error format: {error_text!r}")

    return error_text[len(prefix):].strip()


def parse_log_line(line):
    """
    Expected format:

    rank=12345 | csv_word='...' | error='Unexpected part of speech: ...' | tag='...'

    This is intentionally strict enough to avoid silently processing bad lines.
    """

    pattern = re.compile(
        r"^rank=(?P<rank>\d+)\s+\|\s+"
        r"csv_word=(?P<csv_word>'.*?')\s+\|\s+"
        r"error=(?P<error>'.*?')"
        r"(?:\s+\|\s+tag=(?P<tag>'.*'))?"
        r"\s*$"
    )

    match = pattern.match(line)

    if not match:
        raise LemmaParseError(f"Could not parse log line: {line!r}")

    rank = int(match.group("rank"))
    csv_word = ast.literal_eval(match.group("csv_word"))
    error_text = ast.literal_eval(match.group("error"))

    tag_raw = match.group("tag")
    tag = ast.literal_eval(tag_raw) if tag_raw else None

    raw_pos = extract_raw_pos_from_error(error_text)

    return rank, csv_word, raw_pos, tag


def parse_pronunciation_from_tag(tag):
    """
    Pull only the word/pronunciation from the same two known HTML formats.
    POS comes from MANUAL_POS_MAP, not from the normal POS parser.
    """

    patterns = [
        re.compile(
            r'<span class="word_style">\s*(?P<pronunciation>[^<]+?)\s*</span>\s*'
            r'<span class="gram_style">\s*–\s*(?P<raw_pos>[^<]+?)\s*</span>'
        ),
        re.compile(
            r'<p class="word_style">\s*(?P<pronunciation>[^<]+?)\s*'
            r'<span class="gram_style">\s*-\s*(?P<raw_pos>[^<]+?)\s*</span>\s*</p>'
        ),
    ]

    matches = []

    for pattern in patterns:
        match = pattern.search(tag)
        if match:
            matches.append(match)

    if len(matches) != 1:
        raise LemmaParseError(
            f"Expected exactly one pronunciation/POS match in tag, found {len(matches)}"
        )

    pronunciation = matches[0].group("pronunciation").strip()
    raw_pos_from_tag = matches[0].group("raw_pos").strip()

    if not pronunciation:
        raise LemmaParseError("Pronunciation was empty")

    return pronunciation, raw_pos_from_tag


# Prompt database password
db_password = input("Enter the database password: ")

conn = psycopg2.connect(
    host="vitamova-db.cluster-cartvcorpihi.us-east-1.rds.amazonaws.com",
    database="vitamova",
    user="admin_app",
    password=db_password
)

cursor = conn.cursor()

processed = 0
remaining = 0
inserted = 0
skipped_existing = 0
failed_parse = 0


with open(input_log_path, "r", encoding="utf-8") as input_log, \
     open(remaining_log_path, "w", encoding="utf-8") as remaining_log, \
     open(processed_log_path, "w", encoding="utf-8") as processed_log:

    for line_number, line in enumerate(input_log, start=1):
        line = line.rstrip("\n")

        if not line.strip():
            continue

        try:
            rank, csv_word, raw_pos, tag = parse_log_line(line)

            if raw_pos not in MANUAL_POS_MAP:
                print(f"Unknown POS at line {line_number}: {raw_pos!r}")
                remaining_log.write(line + "\n")
                remaining += 1
                continue

            pronunciation = csv_word
            lemma = remove_ukrainian_stress_marks(pronunciation)
            pos = MANUAL_POS_MAP[raw_pos]

            cursor.execute(
                """
                SELECT 1
                FROM ua_lemmas
                WHERE lemma = %s
                  AND pronunciation = %s
                  AND pos = %s
                LIMIT 1;
                """,
                (lemma, pronunciation, pos)
            )

            exists = cursor.fetchone()

            if exists:
                skipped_existing += 1
                processed_log.write(
                    f"rank={rank} | csv_word={csv_word!r} | "
                    f"raw_pos={raw_pos!r} | mapped_pos={pos!r} | status='already_exists'\n"
                )
                continue

            cursor.execute(
                """
                INSERT INTO ua_lemmas
                    (lemma, rank, pronunciation, pos)
                VALUES
                    (%s, %s, %s, %s);
                """,
                (lemma, rank, pronunciation, pos)
            )

            inserted += 1
            processed += 1

            processed_log.write(
                f"rank={rank} | lemma={lemma!r} | pronunciation={pronunciation!r} | "
                f"raw_pos={raw_pos!r} | mapped_pos={pos!r} | status='inserted'\n"
            )

            if line_number % 1000 == 0:
                conn.commit()
                print(f"Processed {line_number} log lines")

        except Exception as e:
            failed_parse += 1
            remaining_log.write(
                line + f" | retry_error={str(e)!r}\n"
            )
            remaining += 1
            continue


conn.commit()

cursor.close()
conn.close()

print(f"Inserted: {inserted}")
print(f"Already existed: {skipped_existing}")
print(f"Processed successfully: {processed}")
print(f"Still remaining: {remaining}")
print(f"Failed parse attempts: {failed_parse}")
print(f"Remaining log written to: {remaining_log_path}")
print(f"Processed log written to: {processed_log_path}")