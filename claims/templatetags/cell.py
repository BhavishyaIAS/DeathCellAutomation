from django import template

from ..checklist import fmt_date, fmt_month
from ..models import ClaimStatus, Communication, Route

register = template.Library()

STATUS_TONE = {
    ClaimStatus.REGISTERED: "neutral", ClaimStatus.UNDER_SCRUTINY: "info",
    ClaimStatus.WITH_SS: "info", ClaimStatus.WITH_AO: "info",
    ClaimStatus.PENDING_COMPLIANCE: "warn", ClaimStatus.READY_FOR_INWARD: "good",
    ClaimStatus.INWARDED: "good", ClaimStatus.SETTLED: "done",
    ClaimStatus.CLOSED: "neutral",
    Communication.Status.DRAFT: "neutral", Communication.Status.APPROVED: "info",
    Communication.Status.ISSUED: "warn", Communication.Status.REPLIED: "done",
    Communication.Status.CANCELLED: "neutral",
    "OPEN": "warn", "RESOLVED": "done", "SUPERSEDED": "neutral",
}
ROUTE_SHORT = {
    Route.EMPLOYER: "Employer", Route.CLAIMANT: "Claimant", Route.AEO: "AEO",
    Route.ACCOUNTS: "Accounts Group", Route.OTHER_RO: "Other RO",
    Route.INTERNAL: "Death Cell", Route.NOTE: "Note",
}


@register.filter
def tone(status):
    return STATUS_TONE.get(status, "neutral")


@register.filter
def route_short(route):
    return ROUTE_SHORT.get(route, route)


@register.filter
def dmy(value):
    return fmt_date(value) or "—"


@register.filter
def my(value):
    return fmt_month(value) or "—"


@register.filter
def get(mapping, key):
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""


@register.filter
def pct(value, maximum):
    try:
        return round(100 * float(value) / float(maximum)) if float(maximum) else 0
    except (TypeError, ValueError):
        return 0


@register.simple_tag
def qs_replace(params, **changes):
    """Current query string with some parameters replaced."""
    query = params.copy()
    for key, value in changes.items():
        if value in ("", None):
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()
