import csv
import re
import unicodedata
from io import StringIO
from pathlib import Path
import psycopg2

BASE_DIR = Path(__file__).resolve().parent.parent

# Prompt filename
filename = input("Enter the filename (with path if not in the same directory): ")
# Prompt database username
db_user = input("Enter the database username: ")
# Prompt database password
db_password = input("Enter the database password: ")
# Prompt language
language = input("Enter the two-letter language code (e.g., 'en', 'es'): ")


conn = psycopg2.connect(
    host="vitamova-db.cluster-cartvcorpihi.us-east-1.rds.amazonaws.com",
    database="vitamova",
    user=db_user,
    password=db_password
)

cursor = conn.cursor()

filepath = BASE_DIR / filename


class LemmaParseError(Exception):
    pass


def remove_ukrainian_stress_marks(text):
    """
    Removes the accent/stress mark from Ukrainian words.
    Example: приві́ска -> привіска
    """
    normalized = unicodedata.normalize("NFD", text)
    without_stress = "".join(
        ch for ch in normalized
        if ch != "\u0301"  # combining acute accent
    )
    return unicodedata.normalize("NFC", without_stress)


def normalize_pos(pos_text):
    """
    Keep this strict-ish but readable.
    You can either store the Ukrainian grammar phrase directly
    or map it to your app's preferred POS labels.
    """

    allowed_pos = {
        "іменник жіночого роду": "noun_feminine",
        "іменник чоловічого роду": "noun_masculine",
        "іменник середнього роду": "noun_neuter",
        "прикметник": "adjective",
        "прислівник": "adverb",
        "дієслово доконаного виду": "verb_perfective",
        "дієслово недоконаного виду": "verb_imperfective",
    }

    if pos_text not in allowed_pos:
        raise LemmaParseError(f"Unexpected part of speech: {pos_text}")

    return allowed_pos[pos_text]


def parse_tag(tag):
    """
    Strictly parse the Ukrainian lemma tag.

    Accepts only the two observed formats:

    1. <span class="word_style">WORD </span> <span class="gram_style">– POS</span>
    2. <p class="word_style">WORD<span class="gram_style"> - POS</span></p>

    If the format changes, this throws an error and prints the offending tag.
    """

    patterns = [
        # Main format:
        # <span class="word_style">приві́ска </span>
        # <span class="gram_style">– іменник жіночого роду</span>
        re.compile(
            r'<span class="word_style">\s*(?P<pronunciation>[^<]+?)\s*</span>\s*'
            r'<span class="gram_style">\s*–\s*(?P<pos>[^<]+?)\s*</span>'
        ),

        # Compact adverb-style format:
        # <p class="word_style">привіта́льно<span class="gram_style"> - прислівник</span></p>
        re.compile(
            r'<p class="word_style">\s*(?P<pronunciation>[^<]+?)\s*'
            r'<span class="gram_style">\s*-\s*(?P<pos>[^<]+?)\s*</span>\s*</p>'
        ),
    ]

    matches = []

    for pattern in patterns:
        match = pattern.search(tag)
        if match:
            matches.append(match)

    if len(matches) != 1:
        print("OFFENDING TAG:")
        print(tag)
        raise LemmaParseError(
            f"Expected exactly one strict pronunciation/POS match, found {len(matches)}"
        )

    pronunciation = matches[0].group("pronunciation").strip()
    raw_pos = matches[0].group("pos").strip()
    pos = normalize_pos(raw_pos)

    if not pronunciation:
        print("OFFENDING TAG:")
        print(tag)
        raise LemmaParseError("Pronunciation was empty")

    if not pos:
        print("OFFENDING TAG:")
        print(tag)
        raise LemmaParseError("Part of speech was empty")

    lemma = remove_ukrainian_stress_marks(pronunciation)

    return lemma, pronunciation, pos


reader = csv.DictReader(lines)

inserted = 0
skipped = 0

with open("ukrainian_lemmas.csv", "r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)

    for rank, row in enumerate(reader, start=1):
        word_from_csv = row["Word"].strip()
        tag = row["tag"]

        lemma, pronunciation, pos = parse_tag(tag)

        # Strict safety check: the word column should match the parsed pronunciation.
        # If it doesn't, something about the file or parser is not what we expect.
        if word_from_csv != pronunciation:
            print("OFFENDING TAG:")
            print(tag)
            raise LemmaParseError(
                f"CSV word does not match parsed pronunciation. "
                f"CSV Word={word_from_csv!r}, Parsed={pronunciation!r}"
            )

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
            skipped += 1
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

    print(f"Inserted {inserted} rows")
    print(f"Skipped {skipped} duplicate rows")