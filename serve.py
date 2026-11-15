"""Run the Death Claims Cell application for the office network.

    python serve.py            (or double-click start.bat on Windows)

Starts the application on this computer so that it can be opened from any
computer on the same office network.  Each start it also

  * creates a private signing key for this installation, the first time
  * brings the database up to date
  * takes a dated backup copy of the database into the "backups" folder
    (the latest 30 are kept)

The port is 8000 unless DEATHCELL_PORT says otherwise.
"""

import os
import secrets
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
KEEP_BACKUPS = 30


def configure():
    os.chdir(BASE)
    sys.path.insert(0, str(BASE))
    key_file = BASE / ".secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(64))
    os.environ.setdefault("DEATHCELL_SECRET_KEY", key_file.read_text().strip())
    os.environ.setdefault("DEATHCELL_DEBUG", "0")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "deathcell.settings")


def backup(database):
    """Copy the database safely even if it is in use; returns the copy's path."""
    database = Path(database)
    if not database.exists():
        return None
    folder = BASE / "backups"
    folder.mkdir(exist_ok=True)
    target = folder / f"deathcell-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    source = sqlite3.connect(database)
    copy = sqlite3.connect(target)
    with copy:
        source.backup(copy)
    copy.close()
    source.close()
    for old in sorted(folder.glob("deathcell-*.sqlite3"))[:-KEEP_BACKUPS]:
        old.unlink()
    return target


def lan_address():
    """This computer's address on the office network, if it has one."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))  # no packet is actually sent
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def main():
    configure()
    import django
    from django.conf import settings
    from django.core.management import call_command

    django.setup()
    database = settings.DATABASES["default"]["NAME"]
    saved = backup(database)
    call_command("migrate", verbosity=0, interactive=False)

    from django.contrib.auth import get_user_model
    from waitress import serve

    from deathcell.wsgi import application

    port = int(os.environ.get("DEATHCELL_PORT", "8000"))
    host, address = socket.gethostname(), lan_address()
    print("=" * 66)
    print("  Death Claims Cell is running. Keep this window open.")
    print("=" * 66)
    print(f"  On this computer     : http://localhost:{port}")
    print(f"  From other computers : http://{host}:{port}")
    if address:
        print(f"                    or : http://{address}:{port}")
    if saved:
        print(f"  Backup taken         : backups/{saved.name}")
    if not get_user_model().objects.exists():
        print()
        print("  No users exist yet. Stop this window (Ctrl+C) and run setup.bat,")
        print("  or:  python manage.py seed_demo --masters")
    print()
    print("  To stop: press Ctrl+C or close this window.")
    print("=" * 66, flush=True)
    serve(application, host="0.0.0.0", port=port, threads=8, ident="DeathCell")


if __name__ == "__main__":
    main()
