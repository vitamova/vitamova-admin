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
            SELECT COUNT(*) FROM auth_user
            WHERE date_joined >= NOW() - INTERVAL '%s hours'
            """,
            (hours,)
        )
        return self.cursor.fetchone()[0]
    def count_by_days(self,days):
        self.cursor.execute(
            """
            SELECT COUNT(*) FROM auth_user
            WHERE date_joined >= NOW() - INTERVAL '%s days'
            """,
            (days,)
        )
        return self.cursor.fetchone()[0]
    def count_all(self):
        self.cursor.execute(
            """
            SELECT COUNT(*) FROM auth_user
            """
        )
        return self.cursor.fetchone()[0]
    def top(self, limit=10):
        # Get sum of points from the points table for each user
        # user_id maps to id in auth_user
        # Return in descending order of points
        # Need to have first_name, last_name, and email from auth_user
        self.cursor.execute(
            """
            SELECT u.first_name, u.last_name, u.email, COALESCE(SUM(p
.points), 0) as total_points
            FROM auth_user u
            LEFT JOIN points p ON u.id = p.user_id
            GROUP BY u.id
            ORDER BY total_points DESC
            LIMIT %s
            """,
            (limit,)
        )
        result = self.cursor.fetchall()
        # Should be returned as a list of dicts with keys: first_name, last_name, email, total_points
        return [
            {
                "first_name": row[0],
                "last_name": row[1],
                "email": row[2],
                "total_points": row[3]
            }
            for row in result
        ]
    