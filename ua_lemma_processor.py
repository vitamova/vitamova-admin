import csv
import re
import unicodedata
from io import StringIO
from pathlib import Path
import psycopg2
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent

# Prompt filename
filename = "ua_raw.csv"
# Prompt database username
db_user = "admin_app"
# Prompt database password
db_password = input("Enter the database password: ")


conn = psycopg2.connect(
    host="vitamova-db.cluster-cartvcorpihi.us-east-1.rds.amazonaws.com",
    database="vitamova",
    user=db_user,
    password=db_password
)

cursor = conn.cursor()

filepath = BASE_DIR / filename
log_filepath = BASE_DIR / "ua_lemma_mismatches.log"
pos_log_filepath = BASE_DIR / "ua_lemma_unknown_pos.log"


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


def normalize_pos(pos_text, tag=None):
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
        "іменник чоловічого роду, істота": "noun_masculine_animate",
        "іменник жіночого роду, істота": "noun_feminine_animate",
        "дієприкметник": "participle",
        "іменник чоловічого або жіночого роду, істота": "noun_masculine_or_feminine_animate",
        "множинний іменник": "noun_plural",
        "дієслово недоконаного і доконаного виду": "verb_aspectual_pair",
        "прикметник, вищий ступінь": "adjective_comparative",
        "іменник середнього роду, істота": "noun_neuter_animate",
        "прізвище": "surname",
        "сполучник": "conjunction",
        "прийменник": "preposition",
        "власна назва": "proper_noun",
        "прикметник з прислівником": "adjective_with_adverb",
        "множинний іменник, істота": "noun_plural_animate",
        "абревіатура": "abbreviation",
        "займенник з прийменником": "pronoun_with_preposition",
        "присудкове слово": "predicative_word",
        "прикметник, найвищий ступінь": "adjective_superlative",
        "дієприслівник": "adverbial_participle",
        "іменник чоловічого або середнього роду, істота": "noun_masculine_or_neuter_animate",
        "сполука": "compound",
        "дієприкметник з прислівником": "participle_with_adverb",
        "вигук": "interjection",
    }

    if pos_text not in allowed_pos:
        if tag is not None:
            print("OFFENDING TAG:")
            print(tag)

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
    pos = normalize_pos(raw_pos, tag)

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


inserted = 0
skipped = 0

with open(filepath, "r", encoding="utf-8-sig", newline="") as f, \
     open(log_filepath, "a", encoding="utf-8") as mismatch_log_file, \
     open(pos_log_filepath, "a", encoding="utf-8") as pos_log_file:

    reader = csv.DictReader(f)

    for rank, row in enumerate(reader, start=1):
        word_from_csv = row["Word"].strip()
        tag = row["tag"]

        try:
            lemma, pronunciation, pos = parse_tag(tag)

        except LemmaParseError as e:
            # This catches unknown POS and other parse problems.
            # Since this is now a skip-and-log situation, don't crash the script.
            pos_log_file.write(
                f"rank={rank} | "
                f"csv_word={word_from_csv!r} | "
                f"error={str(e)!r}\n"
            )
            pos_log_file.flush()

            print(
                f"Parse/POS issue at rank {rank}: "
                f"CSV Word={word_from_csv!r}. Skipping."
            )

            skipped += 1
            continue

        if word_from_csv != pronunciation:
            mismatch_log_file.write(
                f"rank={rank} | "
                f"csv_word={word_from_csv!r} | "
                f"parsed_pronunciation={pronunciation!r} | "
                f"parsed_lemma={lemma!r} | "
                f"pos={pos!r}\n"
            )
            mismatch_log_file.flush()

            print(
                f"Mismatch at rank {rank}: "
                f"CSV Word={word_from_csv!r}, Parsed={pronunciation!r}. Skipping."
            )

            skipped += 1
            continue

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

        if rank % 1000 == 0:
            conn.commit()
            print(f"Processed {rank} rows")

conn.commit()

print(f"Inserted {inserted} rows")
print(f"Skipped {skipped} rows")
print(f"Mismatches logged to: {log_filepath}")
print(f"Unknown POS / parse issues logged to: {pos_log_filepath}")

cursor.close()
conn.close()