"""Settings for the Death Claims Cell monitoring platform.

Everything an office is likely to change (office name, letter number prefix,
signatory, helpline, thresholds) lives in the OFFICE block at the bottom.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Set DEATHCELL_SECRET_KEY and DEATHCELL_DEBUG=0 on the office server.
SECRET_KEY = os.environ.get(
    "DEATHCELL_SECRET_KEY", "dev-only-key-change-me-before-deploying-on-the-office-server"
)
DEBUG = os.environ.get("DEATHCELL_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DEATHCELL_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "claims",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "deathcell.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "claims.context.office",
            ],
        },
    },
]

WSGI_APPLICATION = "deathcell.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DEATHCELL_DB", BASE_DIR / "db.sqlite3"),
        # Several people work at once from their own computers: wait for a busy
        # database instead of failing, take the write lock at the start of a
        # transaction (so two users can never be given the same diary or letter
        # number), and let readers carry on while someone is saving.
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}

AUTH_USER_MODEL = "claims.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_AGE = 60 * 60 * 10  # one working day

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = False
USE_TZ = True
DATE_FORMAT = "d.m.Y"
DATETIME_FORMAT = "d.m.Y H:i"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_ROOT.mkdir(exist_ok=True)
WHITENOISE_USE_FINDERS = True  # serve app static files without collectstatic
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Office configuration
# --------------------------------------------------------------------------
OFFICE = {
    "name": "Regional Office, Kadapa",
    "cell": "Death Claims Cell",
    "address": "1/30, Railway Station Road, Kadapa - 516004, Andhra Pradesh",
    # Letter number is "<prefix>/<year>/<serial>"
    "letter_prefix": "AP/GR/CDP/ Death claims cell",
    # U.O. note number is "<uo_prefix>/<estt code>/<member no.>/<year>/<serial>"
    "uo_prefix": "GR/CDP/Death Cell",
    # First serial the application should allot, so that numbering continues
    # from the cell's despatch register (e.g. 2301). Later numbers follow on.
    "letter_serial_start": 1,
    "uo_serial_start": 1,
    "signatory": "Accounts Officer (D/C)",
    "signatory_office": "Regional Office, Kadapa.",
    "helpline": "9491138280",
    "email": "sro.cuddapah@epfindia.gov.in",
    "footer_lines": [
        "EPFO never demands or accepts money for processing claims. "
        "All services provided by EPFO are free of cost.",
        "For any queries related to Death Claims – Kindly reach out to us at 9491138280 "
        "or mail us at sro.cuddapah@epfindia.gov.in",
    ],
    # Days allowed for a reply before a letter shows as overdue.
    "reply_days": 15,
    # A first-time member this old at joining, with short service and no linked
    # past service, triggers a "furnish previous employment details" remark.
    "older_age_at_joining": 45,
    # Claims filed this many years after death go to the AEO (checklist point 28).
    "belated_claim_years": 6,
    # Office codes treated as "this office" when deciding whether a past
    # service is an internal or an external transfer.
    "own_office_names": ["", "kadapa", "ro kadapa", "cuddapah", "gr/cdp", "grcdp", "cdp"],
}
