import psycopg2
from django.conf import settings

class User:
    def __init__(self):
        self.conn = psycopg2.connect(
            host="vitamova-db.cluster-cartvcorpihi.us-east-1.rds.amazonaws.com",
            database="vitamova",
            user="admin_app",
            password=settings.DB_PASSWORD
        )
        self.cursor = self.conn.cursor()
    def count_by_hours(self,hours):
        self.cursor.execute(
            """
            SELECT COUNT(*) FROM users
            WHERE last_login >= NOW() - INTERVAL '%s hours'
            """,
            (hours,)
        )
        return self.cursor.fetchone()[0]
    def count_by_days(self,days):
        self.cursor.execute(
            """
            SELECT COUNT(*) FROM users
            WHERE last_login >= NOW() - INTERVAL '%s days'
            """,
            (days,)
        )
        return self.cursor.fetchone()[0]
