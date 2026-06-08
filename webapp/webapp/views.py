from django.shortcuts import render
import db

def home(request):
    users_last_24_hours = db.User().count_by_hours(24)
    users_last_7_days = db.User().count_by_days(7)
    users_last_30_days = db.User().count_by_days(30)
    users_all_time = db.User().count_all()
    top_users = db.User().top()
    return render(request, 'home.html',
        {
            "users_last_24_hours": users_last_24_hours,
            "users_last_7_days": users_last_7_days,
            "users_last_30_days": users_last_30_days,
            "users_all_time": users_all_time,
            "top_users": top_users
        }
    )