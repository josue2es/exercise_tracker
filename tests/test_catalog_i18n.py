"""Spanish catalog overlay: importer, search, services and the translation script."""

import json

from sqlalchemy import select

from db.models import Exercise
from db.session import get_session
from scripts import import_catalog, translate_catalog as tc
from services.catalog import get_exercise, search_exercises
from services.context import UserContext
from services.workouts import create_workout, get_workout, set_workout_exercises
from services.schemas import WorkoutExerciseItem
from tests.conftest import make_exercise
from ui.i18n import exercise_instructions, exercise_name

CTX = UserContext(user_id=1)
BENCH_KEY = "free_exercise_db:bench_press"


def _set_spanish(exercise_id: int, name_es: str, instructions_es=None) -> None:
    with get_session() as session:
        ex = session.get(Exercise, exercise_id)
        ex.name_es, ex.instructions_es = name_es, instructions_es


# --- importer overlay ------------------------------------------------------------------


def test_apply_translations_sets_reverts_and_is_idempotent(engine):
    exercise_id = make_exercise(name="Bench Press")  # source_id "bench_press"
    overlay = {BENCH_KEY: {"name": "Press de banca", "instructions": ["Acuéstate."]}}

    with get_session() as session:
        assert import_catalog.apply_translations(session, overlay) == 1
    with get_session() as session:
        assert import_catalog.apply_translations(session, overlay) == 0  # unchanged

    detail = get_exercise(CTX, exercise_id)
    assert (detail.name_es, detail.instructions_es) == ("Press de banca", ["Acuéstate."])
    assert (detail.name, detail.instructions) == ("Bench Press", ["Lie down."])  # English untouched

    # Removing the entry reverts to English (None), so a bad translation can be pulled.
    with get_session() as session:
        assert import_catalog.apply_translations(session, {}) == 1
    assert get_exercise(CTX, exercise_id).name_es is None


def test_load_translations_missing_file_is_empty(tmp_path):
    assert import_catalog.load_translations(tmp_path / "nope.json") == {}


# --- search and services ---------------------------------------------------------------


def test_search_matches_spanish_name_without_accents(engine, user_id):
    exercise_id = make_exercise(name="Lat Pulldown")
    make_exercise(name="Barbell Squat", source_id="squat")
    _set_spanish(exercise_id, "Jalón al pecho")

    for query in ("jalon", "Jalón", "PECHO jal", "pulldown"):
        page = search_exercises(CTX, query=query)
        assert [i.name for i in page.items] == ["Lat Pulldown"], query
        assert page.items[0].name_es == "Jalón al pecho"


def test_workout_items_carry_spanish_name(engine, user_id):
    exercise_id = make_exercise(name="Bench Press")
    _set_spanish(exercise_id, "Press de banca")
    workout = create_workout(UserContext(user_id=user_id), "Pecho")
    item = WorkoutExerciseItem(
        exercise_id=exercise_id, exercise_name="", position=0, target_sets=3, target_reps_min=8, target_reps_max=12
    )
    set_workout_exercises(UserContext(user_id=user_id), workout.id, [item])
    (entry,) = get_workout(UserContext(user_id=user_id), workout.id).exercises
    assert (entry.exercise_name, entry.exercise_name_es) == ("Bench Press", "Press de banca")


def test_ui_helpers_fall_back_to_english(engine):
    exercise_id = make_exercise(name="Bench Press")
    english = get_exercise(CTX, exercise_id)
    assert (exercise_name(english), exercise_instructions(english)) == ("Bench Press", ["Lie down."])
    _set_spanish(exercise_id, "Press de banca", ["Acuéstate."])
    spanish = get_exercise(CTX, exercise_id)
    assert (exercise_name(spanish), exercise_instructions(spanish)) == ("Press de banca", ["Acuéstate."])


# --- translation script: validation ------------------------------------------------------

CHECKS = tc.glossary_checks(tc.load_glossary())


def test_glossary_file_is_consistent():
    glossary = tc.load_glossary()
    terms = [e for key, section in glossary.items() if isinstance(section, list) and key != "style" for e in section]
    assert len(terms) > 150
    for e in terms:
        assert e["en"] == e["en"].lower() and e["es"], e
        for stem in e.get("check", "").split("|"):
            assert stem == tc.fold(stem), f"check stems must be lowercase and accent-free: {e}"


def test_check_name_accepts_good_translations():
    good = {
        "Barbell Bench Press": "Press de banca con barra",
        "Lever Leg Press": "Prensa de piernas en máquina",  # 'leg press' wins over 'press'
        "Dumbbell Incline Row": "Remo inclinado con mancuerna",
        "Cable Seated Row": "Remo sentado en polea",
        "Kettlebell One Arm Swing": "Swing con kettlebell a una mano",
        "Lying Leg Curl": "Curl femoral tumbado",  # accepted Spain variant
        "Seated Calf Raise": "Elevación de talones sentado",
    }
    for english, spanish in good.items():
        assert tc.check_name(english, spanish, CHECKS) == [], english


def test_check_name_flags_problems():
    assert tc.check_name("Barbell Row", "Remo con mancuerna", CHECKS) == ["glossary: 'barbell' should be 'barra'"]
    assert "glossary: 'deadlift' should be 'peso muerto'" in tc.check_name("Dumbbell Deadlift", "Deadlift con mancuerna", CHECKS)
    assert tc.check_name("Squat v2", "Sentadilla v2", CHECKS) == ["leftover tag (v2/pov/male/female)"]
    assert tc.check_name("Squat", "  ", CHECKS) == ["empty name"]


def test_check_instructions():
    en = ["Stand up.", "Sit down."]
    assert tc.check_instructions(en, ["Ponte de pie.", "Siéntate."]) == []
    assert tc.check_instructions(en, ["Ponte de pie."]) == ["1 steps, English has 2"]
    problems = tc.check_instructions(en, ["Ponte de pie.", "Keep your back straight and slowly repeat the movement."])
    assert problems and problems[0].startswith("step looks untranslated")


# --- translation script: seeding and merging ------------------------------------------


def _source(key="exercisedb_v1:abc1234", name="Barbell Curl", steps=None):
    return tc.SourceExercise(
        key=key, name=name, instructions=steps or ["Stand tall.", "Curl the bar up."],
        muscles=["biceps"], equipment=["barbell"], body_parts=["upper arms"],
    )


def test_seed_uses_only_matching_english():
    catalog = {
        "exercisedb_v1:abc1234": _source(),
        "exercisedb_v1:changed": _source("exercisedb_v1:changed", steps=["Something else entirely.", "Different."]),
        "free_exercise_db:abc1234": _source("free_exercise_db:abc1234"),  # other source, same id: no match
    }
    record = {"instruction_steps": {"en": ["Stand tall.", "Curl the bar up."], "es": ["Ponte erguido.", "Sube la barra."]}}
    dataset = [{"media_id": "abc1234", **record}, {"media_id": "changed", **record}]

    overlay = {}
    assert tc.seed_from_exercises_dataset(overlay, catalog, dataset) == 1
    assert list(overlay) == ["exercisedb_v1:abc1234"]
    entry = overlay["exercisedb_v1:abc1234"]
    assert entry["instructions"] == ["Ponte erguido.", "Sube la barra."]
    assert entry["instructions_origin"] == "exercises-dataset"
    # Idempotent: already done, nothing to fill.
    assert tc.seed_from_exercises_dataset(overlay, catalog, dataset) == 0


def test_staleness_and_manual_protection():
    ex = _source()
    assert tc.needs_name({}, ex)
    assert not tc.needs_name({"name": "Curl con barra", "en_name": "Barbell Curl"}, ex)
    assert tc.needs_name({"name": "Curl con barra", "en_name": "Old Name"}, ex)  # upstream renamed
    assert not tc.needs_name({"name": "Curl con barra", "en_name": "Old Name", "name_origin": "manual"}, ex)
    done = {"instructions": ["a", "b"], "en_instructions_sha": tc.instructions_sha(ex.instructions)}
    assert not tc.needs_instructions(done, ex)
    assert tc.needs_instructions({**done, "en_instructions_sha": "stale"}, ex)


def test_merge_result_rejects_structural_problems_and_flags_glossary():
    catalog = {
        "k:1": _source("k:1", "Barbell Curl"),
        "k:2": _source("k:2", "Barbell Row"),
        "k:3": _source("k:3", "Barbell Shrug"),
        "k:4": _source("k:4", "Barbell Squat"),
    }
    group = [{"key": k, "translate_instructions": k == "k:3"} for k in catalog]
    output = {"items": [
        {"key": "k:1", "name": "Curl con barra", "instructions": []},
        {"key": "k:2", "name": "Remo con mancuerna", "instructions": []},  # glossary mismatch: merged, flagged later
        {"key": "k:3", "name": "Encogimiento de hombros con barra", "instructions": ["Solo un paso."]},  # 1 of 2 steps
        # k:4 missing
    ]}
    overlay = {}
    updated, rejected = tc.merge_result(overlay, catalog, group, output, CHECKS, "claude-opus-5")
    assert updated == 2
    assert set(overlay) == {"k:1", "k:2"}
    assert overlay["k:1"] == {"name": "Curl con barra", "name_origin": "claude-opus-5", "en_name": "Barbell Curl", "reviewed": False}
    assert rejected == {"k:3": ["1 steps, English has 2"], "k:4": ["missing from the model's answer"]}
    assert tc.validate(overlay, catalog, tc.load_glossary()) == {"k:2": ["glossary: 'barbell' should be 'barra'"]}


def test_requests_group_similar_items_and_request_structured_output():
    catalog = {f"k:{i}": _source(f"k:{i}", f"Curl {i}") for i in range(50)}
    items = tc.work_items({}, catalog)
    groups = tc.group_items(items)
    # Each item also needs instructions, so groups are the smaller size.
    assert max(len(g) for g in groups) <= tc.FULL_PER_REQUEST
    assert sum(len(g) for g in groups) == 50
    (first, *_) = tc.build_requests(groups, tc.load_glossary())
    params = first["params"]
    assert params["output_config"]["format"]["schema"] == tc.OUTPUT_SCHEMA
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    sent = json.loads(params["messages"][0]["content"].split("\n\n", 1)[1])
    assert {i["key"] for i in sent} == {i["key"] for i in groups[0]}


def test_overlay_round_trip_is_sorted(tmp_path):
    path = tmp_path / "es.json"
    tc.save_overlay({"b:1": {"name": "B"}, "a:1": {"name": "Á"}}, path)
    assert list(json.loads(path.read_text(encoding="utf-8"))) == ["a:1", "b:1"]
    assert "Á" in path.read_text(encoding="utf-8")  # readable, not \\u escapes
    assert tc.load_overlay(path) == {"a:1": {"name": "Á"}, "b:1": {"name": "B"}}


def test_import_csv_marks_manual_and_reviewed(engine, tmp_path, monkeypatch):
    make_exercise(name="Bench Press")
    monkeypatch.setattr(tc, "TRANSLATIONS_PATH", tmp_path / "es.json")
    monkeypatch.setattr(tc, "load_overlay", lambda: json.loads((tmp_path / "es.json").read_text()) if (tmp_path / "es.json").exists() else {})
    monkeypatch.setattr(tc, "save_overlay", lambda overlay: (tmp_path / "es.json").write_text(json.dumps(overlay)))
    csv_path = tmp_path / "names.csv"
    csv_path.write_text(f"key,english,spanish,problems,reviewed\n{BENCH_KEY},Bench Press,Press de banca,,sí\n", encoding="utf-8")

    class Args:
        path = str(csv_path)

    assert tc.cmd_import_csv(Args) == 0
    entry = json.loads((tmp_path / "es.json").read_text())[BENCH_KEY]
    assert entry == {"name": "Press de banca", "name_origin": "manual", "en_name": "Bench Press", "reviewed": True}
    with get_session() as session:
        assert session.scalars(select(Exercise)).one().name_es is None  # CSV edits reach the DB via the importer


def test_script_migrates_a_database_from_before_the_overlay(tmp_path, capsys):
    """Regression: running the script before `alembic upgrade head` must not crash."""
    from alembic import command
    from alembic.config import Config

    from db.upgrade import ROOT

    db_path = tmp_path / "old.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "238576a4eb17")  # the revision before name_es/instructions_es

    assert tc.main(["--db", str(db_path), "status"]) == 0
    assert "exercises:    0" in capsys.readouterr().out
