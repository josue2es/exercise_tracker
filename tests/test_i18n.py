"""Spanish UI helpers: error translation coverage, catalog terms, dates."""

import ast
import pathlib
from datetime import datetime

import pytest

from services.errors import RateLimitError, ValidationError
from ui.i18n import error_message, fmt_date, term, terms

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _raised_messages() -> list[str]:
    """Messages of every ``raise SomeError("...")`` the UI can surface.

    f-string messages get "1" for each interpolated value, so the patterns
    that translate them are exercised too.
    """
    messages = []
    files = [*sorted((ROOT / "services").glob("*.py")), ROOT / "ui" / "auth.py"]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args):
                continue
            arg = node.exc.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                messages.append(arg.value)
            elif isinstance(arg, ast.JoinedStr):
                messages.append(
                    "".join(v.value if isinstance(v, ast.Constant) else "1" for v in arg.values)
                )
    return messages


def test_every_service_error_has_a_spanish_translation():
    messages = _raised_messages()
    assert len(messages) > 40  # sanity check that the scan found the raises
    untranslated = [m for m in messages if error_message(ValidationError(m)) == m]
    assert untranslated == [], "add these to ui/i18n.py ERRORS or _ERROR_PATTERNS"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Invalid email or password", "Correo o contraseña incorrectos"),
        ("Password must be at least 10 characters long", "La contraseña debe tener al menos 10 caracteres"),
        (
            "Too many failed attempts. Try again in 5 minute(s).",
            "Demasiados intentos fallidos. Inténtalo de nuevo en 5 minuto(s).",
        ),
        ("Something brand new", "Something brand new"),  # unknown: shown as-is
    ],
)
def test_error_message(message, expected):
    assert error_message(RateLimitError(message)) == expected


def test_catalog_terms_translate_and_fall_back():
    assert term("Barbell") == "barra"
    assert term("lower back") == "zona lumbar"
    assert term("unheard-of gadget") == "unheard-of gadget"
    assert terms(["chest", "triceps"]) == "pecho, tríceps"


def test_fmt_date_uses_spanish_months_and_local_time():
    # 03:00 UTC on 1 Sep is still 31 Aug in Mexico City (UTC-6).
    value = datetime(2026, 9, 1, 3, 0)
    assert fmt_date(value, "UTC") == "1 sept 2026"
    assert fmt_date(value, "America/Mexico_City") == "31 ago 2026"
    assert fmt_date(value, "UTC", year=False) == "1 sept"
