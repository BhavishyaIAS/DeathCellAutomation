from django.conf import settings


def office(request):
    return {"OFFICE": settings.OFFICE}
