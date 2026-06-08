from django.shortcuts import render

def home(request):
    users_last_24_hours = 5
    users_last_7_days = 10
    users_last_30_days = 25
    return render(request, 'home.html',
        {
            "users_last_24_hours": users_last_24_hours,
            "users_last_7_days": users_last_7_days,
            "users_last_30_days": users_last_30_days,
        }
    )