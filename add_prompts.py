import json
import psycopg2

from openai import OpenAI

# Prompt user for keys
DB_PASSWORD = input("Enter database password: ")
OPENAI_KEY = input("Enter OpenAI API key: ")

DB_HOST = "vitamova-db.cluster-cartvcorpihi.us-east-1.rds.amazonaws.com"
DB_NAME = "vitamova"
DB_USER = "admin_app"

OPENAI_MODEL = "gpt-4.1-mini"

# Add your original English prompts here.
PROMPTS = [
    {
        "title": "A Snow Day",
        "text": "Imagine it snowed overnight. How would you spend the next day having fun in the snow?"
    },
    {
        "title": "A Poem About Your Family",
        "text": "Write a poem about your family. What are some words to describe your loved ones?"
    },
    {
        "title": "Talking to Animals",
        "text": "You wake up with the ability to talk to animals. What do the birds outside your window have to say?"
    },
    {
        "title": "A Dolphin in Your Bathtub",
        "text": "There’s a dolphin in your bathtub. How did it get there and how will you get it back home to the ocean?"
    },
    {
        "title": "President for a Day",
        "text": "You’re president for a day. What do you do?"
    },
    {
        "title": "A Dragon with a Sore Throat",
        "text": "You’re a veterinarian for a dragon who has a sore throat. How will you help them?"
    },
    {
        "title": "Nocturnal for a Night",
        "text": "Suddenly you’re nocturnal and stay awake all night long. What happens late at night that’s different from daytime?"
    },
    {
        "title": "A Villain Who Wants to Be Good",
        "text": "Write a superhero story where the villain wants to become good. How do they convince the superheroes to trust them?"
    },
    {
        "title": "Robot School",
        "text": "It’s the first day of school for robots. What do you learn at robot school?"
    },
    {
        "title": "A New National Holiday",
        "text": "You get to make a new national holiday. What is it about and how do you celebrate?"
    },
    {
        "title": "Spending One Hundred Dollars",
        "text": "You get $100 but you have to spend it by the end of the day. What do you do with it?"
    },
    {
        "title": "The Best Toy Inventor",
        "text": "You are an inventor whose job is to create the best toys. What will you make?"
    },
    {
        "title": "Ducks Do Not Like You",
        "text": "Suddenly ducks don’t like you. They quack at you and chase you away at the park. How do you get them to like you again?"
    },
    {
        "title": "Ducks Love You Too Much",
        "text": "Oh, no! Suddenly ducks love you and won’t leave you alone! How do you get them to let you be?"
    },
    {
        "title": "Meeting Your Parents in the Past",
        "text": "You go back in time and meet your parents when they were your age. What do you tell them?"
    },
    {
        "title": "World Champion",
        "text": "You’ve just won the world championship. What did you win it in? Describe the competition."
    },
    {
        "title": "The World’s Greatest Detective",
        "text": "You’re the world’s greatest detective. Describe your favorite unusual case. Did you solve it or is it still a mystery?"
    },
    {
        "title": "A Superhero Without Powers",
        "text": "Write a story about a superhero who loses their powers but still has to find a way to be a hero."
    },
    {
        "title": "Trading Places with Your Parents",
        "text": "You trade places with your parents for a day. What would you do?"
    },
    {
        "title": "A New Ending for Your Favorite Book",
        "text": "Rewrite the ending of your favorite book. Will it be happy or sad?"
    },
    {
        "title": "A World Without Electricity",
        "text": "One morning, the world doesn’t have electricity any more. What are your days like now?"
    },
    {
        "title": "Showing Aliens Around",
        "text": "Space aliens land and it’s your lucky job to show them around. Where will you take them?"
    },
    {
        "title": "Why the Chicken Crossed the Road",
        "text": "The chicken finally tells you why it crossed the road. What is the secret?"
    },
    {
        "title": "Mayor for the Community",
        "text": "You’re the mayor. What will you do to make your community better?"
    },
    {
        "title": "A Surprising Family Dinner",
        "text": "Tonight you cook dinner for your family. What interesting food is on the menu that will surprise them?"
    },
    {
        "title": "Stuck in a Movie",
        "text": "You wake up stuck in the last movie you watched. What is it, and what do you do?"
    },
    {
        "title": "The Best Magic Trick",
        "text": "Write about the best magic trick you can imagine."
    },
    {
        "title": "Championship Game",
        "text": "You’re the star of your favorite sports team, and you’re playing for the championship. Write about the game."
    },
    {
        "title": "Letter to Your Favorite Author",
        "text": "Write a letter to your favorite author telling them what you like about their books."
    },
    {
        "title": "The Secret Light Switch",
        "text": "There’s a light switch in your home but nobody knows what it’s for. One day, you discover the secret. What does it light up?"
    },
    {
        "title": "A Pet Dinosaur",
        "text": "You have a pet dinosaur. What’s your dinosaur’s name? What’s your day together like?"
    },
    {
        "title": "Flying Like a Bird",
        "text": "You can fly like a bird. What kind of fun would you have soaring in the clouds?"
    },
    {
        "title": "Describe Yourself with Rhymes",
        "text": "Describe yourself with rhyming words."
    },
    {
        "title": "A Funny Road Trip",
        "text": "Your family takes a big road trip that goes wrong in the funniest way possible. What happens?"
    },
    {
        "title": "A New Island",
        "text": "You discover an island no one has ever seen before. Describe what it’s like."
    },
    {
        "title": "A New Ending for Your Favorite Movie",
        "text": "Rewrite the ending of your favorite movie the way you want it to be."
    },
    {
        "title": "A Wizard Teacher",
        "text": "You show up to the first day of school and discover that your new teacher is a wizard or a witch. What will they teach in class?"
    },
    {
        "title": "A Mysterious Treasure Map",
        "text": "You find a mysterious treasure map. How would you start your treasure hunt?"
    },
    {
        "title": "A New Dinosaur",
        "text": "You’re a paleontologist and find a new type of dinosaur. What did it look like, and what did it do back in the past?"
    },
    {
        "title": "Helping Your Favorite Superhero",
        "text": "Your favorite superhero needs your help! What can you do to save the day?"
    },
    {
        "title": "The Youngest Astronaut",
        "text": "You’re the world’s youngest astronaut and get to fly to the moon. Describe your space trip."
    },
    {
        "title": "Your Dream Treehouse",
        "text": "You get to build your own dream treehouse. Describe what it’s like."
    },
    {
        "title": "Garden Gnomes at Night",
        "text": "One night, you discover that garden gnomes come to life until the sun rises. What fun do they have all night long?"
    },
    {
        "title": "Letter from a Moon Base",
        "text": "Imagine you’re living on a moon base. Write a letter to your cousin on Earth describing how things are going."
    },
    {
        "title": "A Magical Grocery Store",
        "text": "You get to go shopping at a magical grocery store with your family. What special treats would you buy?"
    },
    {
        "title": "A Magic Wand",
        "text": "You have a magic wand. What spells will you cast?"
    },
    {
        "title": "Your Shadow’s Secret Life",
        "text": "What does your shadow do when you’re not around?"
    },
    {
        "title": "Animal Powers",
        "text": "You can have the powers of any animal in the world. What animal is it? What would your powers be?"
    },
    {
        "title": "One Hundred Years in the Future",
        "text": "Write a journal entry from the point of view of a time traveler who visits 100 years into the future. What surprises them in that different world?"
    },
    {
        "title": "A Night in a Museum",
        "text": "You get to spend the night in a museum. What kind of museum? How will you have fun?"
    }
]

TARGET_LANGUAGES = {
    "es": "Spanish",
    "ru": "Russian",
    "pt": "Portuguese",
    "uk": "Ukrainian",
}


client = OpenAI(api_key=OPENAI_KEY)


def translate_prompt(title, text, language_name):
    response = client.responses.create(
        model=OPENAI_MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "You translate language-learning writing prompts. "
                    "Translate naturally, clearly, and simply. "
                    "Preserve the meaning and keep the prompt appropriate for intermediate language learners."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Translate this writing prompt into {language_name}.\n\n"
                    f"Title: {title}\n"
                    f"Text: {text}"
                )
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "translated_writing_prompt",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {
                            "type": "string"
                        },
                        "text": {
                            "type": "string"
                        }
                    },
                    "required": ["title", "text"]
                }
            }
        }
    )

    return json.loads(response.output_text)


def insert_prompt(cursor, language, title, text):
    cursor.execute(
        """
        INSERT INTO writing_prompts (language, title, text)
        VALUES (%s, %s, %s)
        RETURNING id;
        """,
        (language, title, text)
    )

    return cursor.fetchone()[0]


def main():
    conn = None

    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            port=5432,
            sslmode="require"
        )

        with conn.cursor() as cursor:
            for prompt in PROMPTS:
                english_title = prompt["title"]
                english_text = prompt["text"]

                english_id = insert_prompt(
                    cursor=cursor,
                    language="en",
                    title=english_title,
                    text=english_text
                )

                print(f"Inserted English prompt {english_id}: {english_title}")

                for language_code, language_name in TARGET_LANGUAGES.items():
                    translated = translate_prompt(
                        title=english_title,
                        text=english_text,
                        language_name=language_name
                    )

                    translated_id = insert_prompt(
                        cursor=cursor,
                        language=language_code,
                        title=translated["title"],
                        text=translated["text"]
                    )

                    print(
                        f"Inserted {language_name} prompt {translated_id}: "
                        f"{translated['title']}"
                    )

        conn.commit()
        print("All prompts inserted successfully.")

    except Exception as e:
        if conn is not None:
            conn.rollback()

        print("Error inserting prompts.")
        print(e)

    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()