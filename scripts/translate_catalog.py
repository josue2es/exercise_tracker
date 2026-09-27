"""Build and maintain catalog_i18n/es.json: Spanish exercise names and instructions.

The overlay is keyed like the importer (``source:source_id``) and read by
``import_catalog`` (after every import, or with ``--translations-only``).
English stays the source of truth in the database; each entry remembers the
English it was made from, so upstream changes are re-translated.

Workflow:
    python -m scripts.translate_catalog status
    python -m scripts.translate_catalog seed-instructions   # reuse exercises-dataset (MIT) Spanish
    python -m scripts.translate_catalog translate           # Claude, Message Batches API (needs an API key)
    python -m scripts.translate_catalog check               # validate every entry
    python -m scripts.translate_catalog export-csv names.csv   # review names in a spreadsheet
    python -m scripts.translate_catalog import-csv names.csv   # apply edits, mark reviewed
    ./import_catalog --translations-only                    # load the overlay into the DB

``translate`` needs the optional dependency group: ``uv sync --group translate``.
Entries whose origin is "manual" are never overwritten by the model.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import httpx
from sqlalchemy import select

from config import settings
from db.models import Exercise
from db.session import configure, get_session
from scripts.import_catalog import EXERCISEDB_SOURCE, TRANSLATIONS_PATH, ensure_schema, overlay_key

I18N_DIR = TRANSLATIONS_PATH.parent
GLOSSARY_PATH = I18N_DIR / "glossary_es.json"
PENDING_BATCH_PATH = I18N_DIR / ".pending_batch.json"

# hasaneyldrm/exercises-dataset: MIT-licensed Spanish instructions for the
# original ExerciseDB catalog; its media_id equals ExerciseDB V1's exerciseId.
EXERCISES_DATASET_URL = "https://raw.githubusercontent.com/hasaneyldrm/exercises-dataset/main/data/exercises.json"
EXERCISES_DATASET_ORIGIN = "exercises-dataset"
SEED_MIN_SIMILARITY = 0.95  # English instructions must match ours this closely

MODEL = "claude-opus-5"
NAMES_PER_REQUEST = 40
FULL_PER_REQUEST = 15  # name + instructions
MAX_NAME_LENGTH = 120
ENGLISH_MARKERS = re.compile(r"\b(the|your|and|with|keep|slowly|repeat)\b", re.IGNORECASE)
LEFTOVER_TAGS = re.compile(r"\b(v\d|pov)\b|\((male|female)\)", re.IGNORECASE)


# --- data ---------------------------------------------------------------------------


@dataclass
class SourceExercise:
    """English catalog data for one exercise, as imported."""

    key: str
    name: str
    instructions: list[str]
    muscles: list[str]
    equipment: list[str]
    body_parts: list[str]


def instructions_sha(steps: list[str]) -> str:
    return hashlib.sha256(json.dumps(steps, ensure_ascii=False).encode()).hexdigest()[:12]


def fold(text: str) -> str:
    """Lowercase, accent-free text for glossary checks."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def load_catalog() -> dict[str, SourceExercise]:
    """Active exercises from the database, keyed like the overlay."""
    with get_session() as session:
        rows = session.scalars(select(Exercise).where(Exercise.retired_at.is_(None))).all()
        return {
            overlay_key(r.source, r.source_id): SourceExercise(
                key=overlay_key(r.source, r.source_id),
                name=r.name,
                instructions=list(r.instructions or []),
                muscles=list(r.primary_muscles or []) + list(r.secondary_muscles or []),
                equipment=list(r.equipment or []),
                body_parts=list(r.body_parts or []),
            )
            for r in rows
        }


def load_overlay(path: Path = TRANSLATIONS_PATH) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_overlay(overlay: dict[str, dict], path: Path = TRANSLATIONS_PATH) -> None:
    """Sorted, one entry per block: stable, reviewable git diffs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(sorted(overlay.items())), ensure_ascii=False, indent=1, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


def load_glossary(path: Path = GLOSSARY_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- what needs work ------------------------------------------------------------------


def needs_name(entry: dict, ex: SourceExercise) -> bool:
    if entry.get("name_origin") == "manual":
        return False
    return not entry.get("name") or entry.get("en_name") != ex.name


def needs_instructions(entry: dict, ex: SourceExercise) -> bool:
    if not ex.instructions or entry.get("instructions_origin") == "manual":
        return False
    return not entry.get("instructions") or entry.get("en_instructions_sha") != instructions_sha(ex.instructions)


# --- validation -----------------------------------------------------------------------


def glossary_checks(glossary: dict) -> list[tuple[re.Pattern[str], list[str], str]]:
    """(English pattern, accepted Spanish stems, term) for terms with a check,
    longest term first so phrases win over the words inside them."""
    entries = [e for key, section in glossary.items() if isinstance(section, list) and key != "style" for e in section]
    checks = []
    for e in sorted(entries, key=lambda e: len(e["en"]), reverse=True):
        pattern = re.compile(rf"(?<![a-z]){re.escape(e['en'].lower())}(?![a-z])")
        stems = [s.strip() for s in e.get("check", "").split("|") if s.strip()]
        checks.append((pattern, stems, e["en"]))
    return checks


def check_name(en_name: str, es_name: str, checks) -> list[str]:
    """Problems with one translated name (empty list = fine)."""
    problems = []
    if not es_name.strip():
        return ["empty name"]
    if len(es_name) > MAX_NAME_LENGTH:
        problems.append(f"name longer than {MAX_NAME_LENGTH} characters")
    if LEFTOVER_TAGS.search(es_name):
        problems.append("leftover tag (v2/pov/male/female)")
    english, spanish = en_name.lower(), fold(es_name)
    consumed: list[tuple[int, int]] = []
    for pattern, stems, term in checks:
        for match in pattern.finditer(english):
            span = match.span()
            if any(a < span[1] and span[0] < b for a, b in consumed):
                continue  # part of a longer glossary phrase already checked
            consumed.append(span)
            if stems and not any(fold(stem) in spanish for stem in stems):
                problems.append(f"glossary: '{term}' should be '{stems[0]}'")
    return problems


def check_instructions(en_steps: list[str], es_steps: list[str]) -> list[str]:
    problems = []
    if len(es_steps) != len(en_steps):
        problems.append(f"{len(es_steps)} steps, English has {len(en_steps)}")
    if any(not s.strip() for s in es_steps):
        problems.append("empty step")
    english_like = [s for s in es_steps if len(ENGLISH_MARKERS.findall(s)) >= 3]
    if english_like:
        problems.append("step looks untranslated: " + english_like[0][:60])
    return problems


def validate(overlay: dict[str, dict], catalog: dict[str, SourceExercise], glossary: dict) -> dict[str, list[str]]:
    """Problems per overlay key, for entries of exercises still in the catalog."""
    checks = glossary_checks(glossary)
    report = {}
    for key, entry in overlay.items():
        ex = catalog.get(key)
        if ex is None:
            continue
        problems = []
        if entry.get("name"):
            problems += check_name(ex.name, entry["name"], checks)
        if entry.get("instructions"):
            problems += check_instructions(ex.instructions, entry["instructions"])
        if problems:
            report[key] = problems
    return report


# --- seeding from exercises-dataset -------------------------------------------------


def seed_from_exercises_dataset(
    overlay: dict[str, dict], catalog: dict[str, SourceExercise], dataset: list[dict]
) -> int:
    """Copy Spanish instructions where the dataset's English matches ours.

    Matches on id (media_id == ExerciseDB V1 exerciseId), then requires the
    English steps to be near-identical with the same count, so the Spanish is a
    translation of exactly the text we show. Returns entries filled.
    """
    by_media = {d.get("media_id"): d for d in dataset}
    filled = 0
    for key, ex in catalog.items():
        source, _, source_id = key.partition(":")
        record = by_media.get(source_id) if source == EXERCISEDB_SOURCE else None
        entry = overlay.get(key, {})
        if record is None or not needs_instructions(entry, ex):
            continue
        steps = record.get("instruction_steps") or {}
        en, es = steps.get("en") or [], steps.get("es") or []
        if len(en) != len(ex.instructions) or len(es) != len(en):
            continue
        similarity = difflib.SequenceMatcher(None, " ".join(ex.instructions), " ".join(en)).ratio()
        if similarity < SEED_MIN_SIMILARITY or check_instructions(ex.instructions, es):
            continue
        entry.update(
            instructions=[s.strip() for s in es],
            instructions_origin=EXERCISES_DATASET_ORIGIN,
            en_instructions_sha=instructions_sha(ex.instructions),
        )
        overlay[key] = entry
        filled += 1
    return filled


# --- prompting ---------------------------------------------------------------------------


def render_glossary(glossary: dict) -> str:
    lines = ["Style rules:"] + [f"- {rule}" for rule in glossary.get("style", [])]
    for section, entries in glossary.items():
        if not isinstance(entries, list) or section == "style":
            continue
        lines.append(f"\n{section.replace('_', ' ').title()} (English -> Spanish):")
        for e in entries:
            line = f"- {e['en']} -> {e['es']}"
            if e.get("also"):
                line += f" (also: {', '.join(e['also'])})"
            if e.get("note"):
                line += f" [{e['note']}]"
            lines.append(line)
    return "\n".join(lines)


SYSTEM_PROMPT = """You translate a gym app's exercise catalog from English to Spanish for Spanish-speaking gym-goers.

Accuracy and naturalness matter more than speed: a user must recognise the exercise from its Spanish name, and follow the instructions safely. Use the glossary below for every term it covers, so the whole catalog stays consistent. When the glossary gives alternatives, use the main term.

For each item:
- "name": the name a Spanish-speaking lifter would actually use for this exercise. Use the instructions, muscles and equipment to understand what the exercise is before naming it; never translate an idiomatic name word by word. Sentence case (only the first word and proper names capitalised).
- "instructions": only when the item has "translate_instructions": true. Translate every English step into one Spanish step, same count and order, imperative "tú". Keep the meaning exact: no added cues, no dropped details, numbers and units unchanged. Otherwise return [].

Examples of names:
- Barbell Bench Press -> Press de banca con barra
- Dumbbell Incline Bench Press -> Press de banca inclinado con mancuernas
- Cable Seated Row -> Remo sentado en polea
- Lever Seated Leg Curl -> Curl femoral sentado en máquina
- Dumbbell Standing Alternate Hammer Curl -> Curl martillo alterno de pie con mancuernas
- Wide-Grip Lat Pulldown -> Jalón al pecho con agarre abierto
- Barbell Romanian Deadlift -> Peso muerto rumano con barra
- Kettlebell One Arm Swing -> Swing con pesa rusa a una mano
- Weighted Pull-Up -> Dominada con lastre
- Smith Close-Grip Bench Press -> Press de banca con agarre cerrado en máquina Smith
- Farmer's Walk -> Paseo del granjero
- 3/4 Sit-Up -> Abdominal 3/4

"""


def build_system(glossary: dict) -> list[dict]:
    # Identical across requests, so it is cached after the first one.
    return [{"type": "text", "text": SYSTEM_PROMPT + render_glossary(glossary), "cache_control": {"type": "ephemeral"}}]


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "name": {"type": "string"},
                    "instructions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["key", "name", "instructions"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def work_items(overlay: dict[str, dict], catalog: dict[str, SourceExercise]) -> list[dict]:
    """What the model has to do, one item per exercise needing a name and/or instructions."""
    items = []
    for key, ex in sorted(catalog.items()):
        entry = overlay.get(key, {})
        want_name, want_steps = needs_name(entry, ex), needs_instructions(entry, ex)
        if not (want_name or want_steps):
            continue
        item = {
            "key": key,
            "name": ex.name,
            "muscles": ex.muscles,
            "equipment": ex.equipment,
            "body_parts": ex.body_parts,
            "instructions_en": ex.instructions,
            "translate_instructions": want_steps,  # else the English is context for the name
        }
        items.append(item)
    return items


def group_items(items: list[dict]) -> list[list[dict]]:
    """Similar exercises together (same equipment and muscle), so the model names
    variants consistently; smaller groups when instructions are included."""
    items = sorted(items, key=lambda i: (i["equipment"][:1], i["muscles"][:1], i["name"]))
    # Each item uses a share of one request: 1/15 with instructions, 1/40 name only.
    groups, current, size = [], [], Fraction(0)
    for item in items:
        cost = Fraction(1, FULL_PER_REQUEST if item["translate_instructions"] else NAMES_PER_REQUEST)
        if current and size + cost > 1:
            groups.append(current)
            current, size = [], Fraction(0)
        current.append(item)
        size += cost
    if current:
        groups.append(current)
    return groups


def build_requests(groups: list[list[dict]], glossary: dict, model: str = MODEL) -> list[dict]:
    system = build_system(glossary)
    return [
        {
            "custom_id": f"group-{n}",
            "params": {
                "model": model,
                "max_tokens": 32000,
                "system": system,
                "output_config": {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
                "messages": [
                    {
                        "role": "user",
                        "content": "Translate these exercises. Return every key exactly once.\n\n"
                        + json.dumps(group, ensure_ascii=False, separators=(",", ":")),
                    }
                ],
            },
        }
        for n, group in enumerate(groups)
    ]


# --- merging results ---------------------------------------------------------------------


def merge_result(
    overlay: dict[str, dict],
    catalog: dict[str, SourceExercise],
    group: list[dict],
    output: dict,
    checks,
    origin: str,
) -> tuple[int, dict[str, list[str]]]:
    """Merge one validated group result. Returns (entries updated, rejected keys -> problems).

    A rejected item leaves the overlay untouched, so the next run retries it.
    """
    requested = {item["key"]: item for item in group}
    returned = {item["key"]: item for item in output.get("items", []) if item.get("key") in requested}
    updated, rejected = 0, {}
    for key, item in requested.items():
        result, ex = returned.get(key), catalog[key]
        if result is None:
            rejected[key] = ["missing from the model's answer"]
            continue
        problems = check_name(ex.name, result["name"], checks)
        if item["translate_instructions"]:
            problems += check_instructions(ex.instructions, result["instructions"])
        # Glossary mismatches may be false positives: merge, and let check /
        # export-csv surface them for review. Anything else is retried.
        hard = [p for p in problems if not p.startswith("glossary")]
        if hard:
            rejected[key] = hard
            continue
        entry = overlay.get(key, {})
        if needs_name(entry, ex):
            entry.update(name=result["name"].strip(), name_origin=origin, en_name=ex.name, reviewed=False)
        if item["translate_instructions"]:
            entry.update(
                instructions=[s.strip() for s in result["instructions"]],
                instructions_origin=origin,
                en_instructions_sha=instructions_sha(ex.instructions),
            )
        overlay[key] = entry
        updated += 1
    return updated, rejected


# --- commands -----------------------------------------------------------------------------


def cmd_status(args) -> int:
    catalog, overlay = load_catalog(), load_overlay()
    names = sum(1 for k, ex in catalog.items() if not needs_name(overlay.get(k, {}), ex))
    steps = sum(1 for k, ex in catalog.items() if ex.instructions and not needs_instructions(overlay.get(k, {}), ex))
    with_steps = sum(1 for ex in catalog.values() if ex.instructions)
    reviewed = sum(1 for k in catalog if overlay.get(k, {}).get("reviewed"))
    print(f"exercises:    {len(catalog)}")
    print(f"names:        {names} done, {len(catalog) - names} to translate")
    print(f"instructions: {steps} done, {with_steps - steps} to translate")
    print(f"reviewed:     {reviewed} names")
    return 0


def cmd_seed_instructions(args) -> int:
    catalog, overlay = load_catalog(), load_overlay()
    if args.dataset:
        dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    else:
        print(f"Downloading {EXERCISES_DATASET_URL} ...", flush=True)
        response = httpx.get(EXERCISES_DATASET_URL, timeout=120, follow_redirects=True)
        response.raise_for_status()
        dataset = response.json()
    filled = seed_from_exercises_dataset(overlay, catalog, dataset)
    save_overlay(overlay)
    print(f"Seeded instructions for {filled} exercises from exercises-dataset (MIT).")
    return 0


def cmd_translate(args) -> int:
    import anthropic  # optional dependency group "translate"

    client = anthropic.Anthropic()
    glossary, catalog, overlay = load_glossary(), load_catalog(), load_overlay()

    if PENDING_BATCH_PATH.exists():
        pending = json.loads(PENDING_BATCH_PATH.read_text())
        print(f"Resuming batch {pending['batch_id']} ...")
    else:
        items = work_items(overlay, catalog)[: args.limit or None]
        if not items:
            print("Nothing to translate.")
            return 0
        groups = group_items(items)
        requests = build_requests(groups, glossary, args.model)
        print(f"{len(items)} exercises in {len(requests)} requests.")
        if args.dry_run:
            print(requests[0]["params"]["messages"][0]["content"][:2000])
            return 0
        batch = client.messages.batches.create(requests=requests)
        pending = {"batch_id": batch.id, "model": args.model, "groups": groups}
        PENDING_BATCH_PATH.write_text(json.dumps(pending, ensure_ascii=False))
        print(f"Submitted batch {batch.id}; safe to interrupt and re-run to resume.")

    while (batch := client.messages.batches.retrieve(pending["batch_id"])).processing_status != "ended":
        counts = batch.request_counts
        print(f"  {batch.processing_status}: {counts.processing} processing, {counts.succeeded} done", flush=True)
        time.sleep(60)

    checks = glossary_checks(glossary)
    total, rejected, failed = 0, {}, []
    groups = pending["groups"]
    for result in client.messages.batches.results(pending["batch_id"]):
        group = groups[int(result.custom_id.removeprefix("group-"))]
        if result.result.type != "succeeded":
            failed.append(f"{result.custom_id}: {result.result.type}")
            continue
        message = result.result.message
        if message.stop_reason != "end_turn":
            failed.append(f"{result.custom_id}: stop_reason={message.stop_reason}")
            continue
        text = next((b.text for b in message.content if b.type == "text"), "")
        try:
            output = json.loads(text)
        except json.JSONDecodeError:
            failed.append(f"{result.custom_id}: invalid JSON")
            continue
        group = [g for g in group if g["key"] in catalog]  # catalog may have changed while waiting
        updated, bad = merge_result(overlay, catalog, group, output, checks, pending["model"])
        total += updated
        rejected.update(bad)

    save_overlay(overlay)
    PENDING_BATCH_PATH.unlink()
    print(f"Merged {total} exercises. Rejected {len(rejected)} (re-run to retry). Failed requests: {len(failed)}.")
    for key, problems in list(rejected.items())[:20]:
        print(f"  rejected {key}: {'; '.join(problems)}")
    for line in failed[:20]:
        print(f"  failed {line}")
    return 0


def cmd_check(args) -> int:
    report = validate(load_overlay(), load_catalog(), load_glossary())
    for key, problems in sorted(report.items()):
        print(f"{key}: {'; '.join(problems)}")
    print(f"{len(report)} entries with problems.")
    return 1 if report else 0


CSV_FIELDS = ["key", "english", "spanish", "problems", "reviewed"]


def cmd_export_csv(args) -> int:
    catalog, overlay = load_catalog(), load_overlay()
    report = validate(overlay, catalog, load_glossary())
    # utf-8-sig (with BOM) so Excel shows accents correctly when opening the file.
    with open(args.path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        # Flagged names first: they need attention most.
        for key, ex in sorted(catalog.items(), key=lambda kv: (kv[0] not in report, kv[1].name)):
            entry = overlay.get(key, {})
            writer.writerow(
                {
                    "key": key,
                    "english": ex.name,
                    "spanish": entry.get("name", ""),
                    "problems": "; ".join(report.get(key, [])),
                    "reviewed": "yes" if entry.get("reviewed") else "",
                }
            )
    print(f"Wrote {len(catalog)} rows to {args.path}. Edit 'spanish', set 'reviewed' to yes, then import-csv.")
    return 0


def cmd_import_csv(args) -> int:
    catalog, overlay = load_catalog(), load_overlay()
    edited = approved = 0
    # Spreadsheets save CSV their own way: Excel adds a BOM and, in Spanish
    # locales, separates with ";". Accept both, and "," or tabs.
    with open(args.path, newline="", encoding="utf-8-sig") as f:
        dialect = csv.Sniffer().sniff(f.readline(), delimiters=",;\t")
        f.seek(0)
        reader = csv.DictReader(f, dialect=dialect)
        if "key" not in (reader.fieldnames or []) or "spanish" not in reader.fieldnames:
            print(f"{args.path}: expected columns key, spanish, reviewed; found {reader.fieldnames}", file=sys.stderr)
            return 1
        for row in reader:
            key, spanish = row["key"], (row.get("spanish") or "").strip()
            if key not in catalog or not spanish:
                continue
            entry = overlay.setdefault(key, {})
            if spanish != entry.get("name"):
                entry.update(name=spanish, name_origin="manual", en_name=catalog[key].name)
                edited += 1
            if (row.get("reviewed") or "").strip().lower() in {"yes", "y", "si", "sí", "x", "1", "true"}:
                approved += not entry.get("reviewed")
                entry["reviewed"] = True
    save_overlay(overlay)
    print(f"{edited} names edited (kept as manual), {approved} newly marked reviewed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain the Spanish catalog overlay (catalog_i18n/es.json)")
    parser.add_argument("--db", default=None, help="Database path override (default: settings.database_path)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Coverage of names and instructions")
    seed = commands.add_parser("seed-instructions", help="Reuse Spanish instructions from exercises-dataset (MIT)")
    seed.add_argument("--dataset", help="Local copy of exercises.json instead of downloading it")
    translate = commands.add_parser("translate", help="Translate what is missing or stale with Claude (Batches API)")
    translate.add_argument("--limit", type=int, default=0, help="Only the first N exercises (try a small run first)")
    translate.add_argument("--model", default=MODEL)
    translate.add_argument("--dry-run", action="store_true", help="Show the first request instead of submitting")
    commands.add_parser("check", help="Validate every entry against the glossary and the English")
    export = commands.add_parser("export-csv", help="Names for human review, flagged ones first")
    export.add_argument("path")
    import_ = commands.add_parser("import-csv", help="Apply reviewed/edited names from the CSV")
    import_.add_argument("path")
    args = parser.parse_args(argv)

    configure(f"sqlite:///{Path(args.db).resolve()}" if args.db else settings.database_url)
    ensure_schema()  # apply pending migrations (name_es/instructions_es), like the importer
    handlers = {
        "status": cmd_status,
        "seed-instructions": cmd_seed_instructions,
        "translate": cmd_translate,
        "check": cmd_check,
        "export-csv": cmd_export_csv,
        "import-csv": cmd_import_csv,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
