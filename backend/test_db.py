
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

try:
    connection = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )

    cursor = connection.cursor()
    cursor.execute("SELECT current_database();")

    database_name = cursor.fetchone()[0]

    print("PostgreSQL Connection Successful!")
    print("Connected Database:", database_name)

    cursor.close()
    connection.close()

except Exception as error:
    print("Database Connection Failed!")
    print(error)
