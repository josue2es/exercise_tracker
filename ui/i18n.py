"""Spanish presentation helpers for the UI.

UI strings are written in Spanish directly in the pages. This module covers
what can't be inline:

- Service-layer error messages stay in English because the REST API and MCP
  server (read by LLM agents) share them; ``error_message`` translates them at
  the UI boundary, falling back to the original text.
- Catalog vocabulary (muscles, equipment, level) is imported English data;
  ``term`` translates known values and passes unknown ones through.
- Dates are formatted with Spanish month names independent of server locale.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

# --- errors ----------------------------------------------------------------------

ERRORS: dict[str, str] = {
    # auth / users
    "Not logged in": "No has iniciado sesión",
    "Invalid email address": "Correo electrónico no válido",
    "Admin access required": "Se requiere acceso de administrador",
    "This action requires a key with write scope": "Esta acción requiere una clave con permiso de escritura",
    "A user with this email already exists": "Ya existe un usuario con este correo",
    "Invite not found": "Invitación no encontrada",
    "This invite was already used": "Esta invitación ya fue utilizada",
    "Please enter your name": "Por favor, escribe tu nombre",
    "Invalid invite link": "Enlace de invitación no válido",
    "This invite link has already been used": "Este enlace de invitación ya fue utilizado",
    "This invite link has been revoked": "Este enlace de invitación fue revocado",
    "This invite link has expired. Ask for a new one.": "Este enlace de invitación expiró. Pide uno nuevo.",
    "Invalid email or password": "Correo o contraseña incorrectos",
    "This account has been deactivated": "Esta cuenta fue desactivada",
    "You cannot deactivate your own account": "No puedes desactivar tu propia cuenta",
    "User not found": "Usuario no encontrado",
    "Unit must be 'kg' or 'lb'": "La unidad debe ser «kg» o «lb»",
    "Unknown time zone": "Zona horaria desconocida",
    "Current password is incorrect": "La contraseña actual es incorrecta",
    # sessions / sets
    "Session not found": "Sesión no encontrada",
    "Reps must be between 0 and 100": "Las repeticiones deben estar entre 0 y 100",
    "Weight cannot be negative": "El peso no puede ser negativo",
    "Unit (kg or lb) is required when weight is set": "Indica la unidad (kg o lb) cuando hay peso",
    "Unit without weight is not valid; omit both for bodyweight": (
        "Unidad sin peso no es válida; deja ambos vacíos para peso corporal"
    ),
    "Workout not found": "Día de rutina no encontrado",
    "Routine not found": "Rutina no encontrada",
    "set_number must be at least 1": "El número de serie debe ser al menos 1",
    "Exercise not found": "Ejercicio no encontrado",
    "This session is already finished": "Esta sesión ya terminó",
    "Session is already finished": "La sesión ya terminó",
    "Sets can only be deleted in the app": "Las series solo se pueden eliminar en la app",
    "Set not found": "Serie no encontrada",
    "No open session": "No hay una sesión abierta",
    # workouts
    "Target sets must be between 1 and 20": "Las series objetivo deben estar entre 1 y 20",
    "Rep range must satisfy 1 <= min <= max <= 100": "El rango de repeticiones debe cumplir 1 ≤ mín ≤ máx ≤ 100",
    "Rest must be between 0 and 3600 seconds": "El descanso debe estar entre 0 y 3600 segundos",
    "Workout name is required": "El nombre del día es obligatorio",
    "Routine name is required": "El nombre de la rutina es obligatorio",
    "The same exercise appears twice in this workout": "El mismo ejercicio aparece dos veces en este día",
    "Workouts can only be deleted in the app": "Los días solo se pueden eliminar en la app",
    "Routines can only be deleted in the app": "Las rutinas solo se pueden eliminar en la app",
    # API keys
    "Please give the key a label": "Ponle un nombre a la clave",
    "Scopes must be a subset of: read, write": "Los permisos deben ser: read, write",
    "API key not found": "Clave API no encontrada",
    "Invalid API key": "Clave API no válida",
}

# Messages built with f-strings in the service layer: (pattern, template).
_ERROR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"Password must be at least (\d+) characters long"),
        "La contraseña debe tener al menos {0} caracteres",
    ),
    (re.compile(r"Role must be one of: (.+)"), "El rol debe ser uno de: {0}"),
    (re.compile(r"Unknown exercise id: (.+)"), "Ejercicio desconocido (id {0})"),
    (
        re.compile(r"Too many failed attempts\. Try again in (\d+) minute\(s\)\."),
        "Demasiados intentos fallidos. Inténtalo de nuevo en {0} minuto(s).",
    ),
]


def error_message(exc: Exception) -> str:
    """Spanish text for a service error, or its original message if unknown."""
    message = str(exc)
    if message in ERRORS:
        return ERRORS[message]
    for pattern, template in _ERROR_PATTERNS:
        match = pattern.fullmatch(message)
        if match:
            return template.format(*match.groups())
    return message


# --- catalog vocabulary ------------------------------------------------------------

# Covers free-exercise-db and ExerciseDB values (lowercase). Unknown terms are
# shown as-is, so a new value from a source degrades to English, not to blank.
TERMS: dict[str, str] = {
    # muscles
    "abdominals": "abdominales",
    "abs": "abdominales",
    "lower abs": "abdominales inferiores",
    "obliques": "oblicuos",
    "core": "core",
    "abductors": "abductores",
    "adductors": "aductores",
    "inner thighs": "cara interna del muslo",
    "groin": "ingle",
    "biceps": "bíceps",
    "brachialis": "braquial",
    "triceps": "tríceps",
    "forearms": "antebrazos",
    "wrist flexors": "flexores de muñeca",
    "wrist extensors": "extensores de muñeca",
    "grip muscles": "músculos de agarre",
    "wrists": "muñecas",
    "hands": "manos",
    "calves": "pantorrillas",
    "soleus": "sóleo",
    "shins": "espinillas",
    "ankles": "tobillos",
    "ankle stabilizers": "estabilizadores de tobillo",
    "feet": "pies",
    "chest": "pecho",
    "upper chest": "pecho superior",
    "pectorals": "pectorales",
    "glutes": "glúteos",
    "hamstrings": "isquiotibiales",
    "quadriceps": "cuádriceps",
    "quads": "cuádriceps",
    "hip flexors": "flexores de cadera",
    "lats": "dorsales",
    "latissimus dorsi": "dorsal ancho",
    "lower back": "zona lumbar",
    "middle back": "espalda media",
    "upper back": "espalda alta",
    "back": "espalda",
    "rhomboids": "romboides",
    "spine": "columna",
    "neck": "cuello",
    "sternocleidomastoid": "esternocleidomastoideo",
    "levator scapulae": "elevador de la escápula",
    "shoulders": "hombros",
    "delts": "deltoides",
    "deltoids": "deltoides",
    "rear deltoids": "deltoides posterior",
    "rotator cuff": "manguito rotador",
    "serratus anterior": "serrato anterior",
    "traps": "trapecios",
    "trapezius": "trapecio",
    "cardiovascular system": "sistema cardiovascular",
    # body parts (ExerciseDB)
    "cardio": "cardio",
    "lower arms": "antebrazos",
    "lower legs": "piernas (parte baja)",
    "upper arms": "brazos",
    "upper legs": "piernas (parte alta)",
    "waist": "cintura",
    # equipment
    "assisted": "asistido",
    "band": "banda",
    "bands": "bandas",
    "resistance band": "banda de resistencia",
    "barbell": "barra",
    "olympic barbell": "barra olímpica",
    "ez barbell": "barra Z",
    "e-z curl bar": "barra Z",
    "trap bar": "barra hexagonal",
    "smith machine": "máquina Smith",
    "body only": "peso corporal",
    "body weight": "peso corporal",
    "weighted": "con lastre",
    "bosu ball": "bosu",
    "cable": "polea",
    "dumbbell": "mancuerna",
    "kettlebell": "pesa rusa",
    "kettlebells": "pesas rusas",
    "machine": "máquina",
    "leverage machine": "máquina de palanca",
    "sled machine": "trineo",
    "skierg machine": "SkiErg",
    "stepmill machine": "escaladora",
    "elliptical machine": "elíptica",
    "stationary bike": "bicicleta estática",
    "upper body ergometer": "ergómetro de brazos",
    "medicine ball": "balón medicinal",
    "exercise ball": "pelota de ejercicio",
    "stability ball": "pelota de estabilidad",
    "foam roll": "rodillo de espuma",
    "roller": "rodillo",
    "wheel roller": "rueda abdominal",
    "hammer": "martillo",
    "rope": "cuerda",
    "tire": "neumático",
    "other": "otro",
    # levels
    "beginner": "principiante",
    "intermediate": "intermedio",
    "expert": "avanzado",
}


def term(value: str) -> str:
    """Spanish name for a catalog term (muscle, equipment, level, body part)."""
    return TERMS.get(value.lower(), value)


def terms(values) -> str:
    """Comma-joined Spanish names for a list of catalog terms."""
    return ", ".join(term(v) for v in values)


# --- exercise names and instructions -----------------------------------------------


def exercise_name(exercise) -> str:
    """Spanish name of a catalog exercise, falling back to the English one."""
    return exercise.name_es or exercise.name


def exercise_instructions(exercise) -> list[str]:
    """Spanish instruction steps, falling back to the English ones."""
    return exercise.instructions_es or exercise.instructions


# --- dates -----------------------------------------------------------------------

MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic")


def fmt_date(value: datetime, tz_name: str, *, year: bool = True) -> str:
    """A naive-UTC datetime as a local short date, e.g. ``26 sept 2026``."""
    local = value.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz_name))
    text = f"{local.day} {MONTHS[local.month - 1]}"
    return f"{text} {local.year}" if year else text
