"""
tests/test_feed.py — Mixtape

Regression tests for the "Friends Listening Now" feed.

Issue #2: the feed used a rolling 24-hour window, so a friend's late-night
listen stayed visible the next morning. These tests freeze "now" to a known
time and assert the feed is scoped to the current calendar day.
"""

import pytest
from datetime import datetime, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
import services.feed_service as feed_service


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed(app):
    """`me` is friends with `early` (listened last night) and `today` (listened this morning)."""
    with app.app_context():
        me = User(username="me", email="me@example.com")
        early = User(username="early", email="early@example.com")
        today = User(username="today", email="today@example.com")
        db.session.add_all([me, early, today])
        db.session.flush()

        for other in (early, today):
            db.session.execute(friendships.insert().values(user_id=me.id, friend_id=other.id))
            db.session.execute(friendships.insert().values(user_id=other.id, friend_id=me.id))

        song = Song(title="Track", artist="Various", shared_by=me.id)
        db.session.add(song)
        db.session.flush()

        # early listened at 11pm YESTERDAY; today listened at 8am TODAY
        db.session.add(ListeningEvent(user_id=early.id, song_id=song.id,
                                      listened_at=datetime(2026, 7, 6, 23, 0, tzinfo=timezone.utc)))
        db.session.add(ListeningEvent(user_id=today.id, song_id=song.id,
                                      listened_at=datetime(2026, 7, 7, 8, 0, tzinfo=timezone.utc)))
        db.session.commit()
        yield {"me": me}


def _freeze_now(monkeypatch, frozen):
    """Freeze datetime.now() inside feed_service to a fixed instant."""
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen
    monkeypatch.setattr(feed_service, "datetime", FrozenDateTime)


def test_feed_excludes_yesterdays_late_night_listen(app, seed, monkeypatch):
    """At 9am, a friend whose last listen was 11pm yesterday should NOT appear."""
    with app.app_context():
        _freeze_now(monkeypatch, datetime(2026, 7, 7, 9, 0, tzinfo=timezone.utc))
        names = [f["friend"]["username"] for f in feed_service.get_friends_listening_now(seed["me"].id)]
        assert "today" in names       # listened this morning -> shown
        assert "early" not in names   # listened 11pm yesterday -> hidden (bug showed it)


def test_feed_includes_todays_early_morning_listen(app, seed, monkeypatch):
    """A listen from earlier the same calendar day is included, even hours later."""
    with app.app_context():
        _freeze_now(monkeypatch, datetime(2026, 7, 7, 23, 59, tzinfo=timezone.utc))
        names = [f["friend"]["username"] for f in feed_service.get_friends_listening_now(seed["me"].id)]
        assert "today" in names  # 8am listen still counts at 11:59pm same day
