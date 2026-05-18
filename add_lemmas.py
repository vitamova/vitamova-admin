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
with open(filepath, "r", encoding="utf-8") as file:
    lines = file.readlines()

# Start at line 8 based on your current script: lines[7:]
# Python indexes start at 0, so index 7 means the 8th line.
for line in lines[9:]:
    values = line.rstrip("\n").split("\t")

    rank = int(values[0])
    lemma = values[1]
    pos = values[2]

    cursor.execute(
        """
        INSERT INTO lemmas (language, rank, lemma, pos)
        VALUES (%s, %s, %s, %s)
        """,
        (language, rank, lemma, pos)
    )

conn.commit()

cursor.close()
conn.close()

print("Done importing lemmas.")