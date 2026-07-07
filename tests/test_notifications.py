"""
tests/test_notifications.py — Mixtape

Regression tests for notification creation.

Issue #4: rating a shared song produced no notification for the sharer, even
though adding the song to a playlist did. These tests pin down the intended
behavior: rating notifies the sharer, exactly like add_to_playlist does.
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed(app):
    """A song shared by `sharer`, and a separate `rater` user."""
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Crown Heights Anthem", artist="Borough Kings",
                    genre="rap", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()
        yield {"sharer": sharer, "rater": rater, "song": song}


def test_rating_a_song_notifies_the_sharer(app, seed):
    """Rating a shared song creates a 'song_rated' notification for the sharer."""
    with app.app_context():
        sharer_id = seed["sharer"].id
        rater_id = seed["rater"].id
        song_id = seed["song"].id

        assert get_notifications(sharer_id) == []  # nothing yet

        rate_song(rater_id, song_id, 5)

        notifs = get_notifications(sharer_id)
        assert len(notifs) == 1  # Bug #4 caused this to be 0
        assert notifs[0]["type"] == "song_rated"
        assert "rater" in notifs[0]["body"]
        assert "Crown Heights Anthem" in notifs[0]["body"]


def test_rating_your_own_song_does_not_notify(app, seed):
    """A user rating their own shared song should not notify themselves."""
    with app.app_context():
        sharer_id = seed["sharer"].id
        song_id = seed["song"].id

        rate_song(sharer_id, song_id, 4)

        assert get_notifications(sharer_id) == []


def test_updating_a_rating_still_notifies(app, seed):
    """Re-rating (updating an existing rating) also notifies the sharer."""
    with app.app_context():
        sharer_id = seed["sharer"].id
        rater_id = seed["rater"].id
        song_id = seed["song"].id

        rate_song(rater_id, song_id, 3)
        rate_song(rater_id, song_id, 5)  # update

        notifs = get_notifications(sharer_id)
        assert len(notifs) == 2  # one per rating action
