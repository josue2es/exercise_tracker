"""Shared pytest fixtures: a temporary SQLite database per test."""

import pytest

from db import models
from db.session import configure, get_session


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path}/test.db"


@pytest.fixture
def engine(db_url):
    engine = configure(db_url)
    models.Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def make_user(email="user@example.com", display_name="User", role="member") -> int:
    """Insert a user directly; used by tests that don't exercise the accounts flow."""
    from argon2 import PasswordHasher

    with get_session() as session:
        user = models.User(
            email=email.lower(),
            display_name=display_name,
            password_hash=PasswordHasher().hash("password123"),
            role=role,
        )
        session.add(user)
        session.flush()
        return user.id


def make_exercise(name="Bench Press", source="free_exercise_db", source_id=None) -> int:
    """Insert an exercise directly."""
    with get_session() as session:
        exercise = models.Exercise(
            source=source,
            source_id=source_id or name.lower().replace(" ", "_"),
            name=name,
            category="strength",
            primary_muscles=["pectorals"],
            secondary_muscles=[],
            equipment=["barbell"],
            level="beginner",
            instructions=["Lie down."],
            media=[],
            attribution="test",
            search_text=name.lower() + " pectorals barbell",
        )
        session.add(exercise)
        session.flush()
        return exercise.id


@pytest.fixture
def user_id(engine):
    return make_user()


@pytest.fixture
def exercise_id(engine):
    return make_exercise()
