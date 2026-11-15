from .models import Role
from .models import office as office_config


def office(request):
    user = getattr(request, "user", None)
    can_configure = bool(user and user.is_authenticated and user.has_role(Role.AO))
    return {"OFFICE": office_config(), "can_configure": can_configure}
