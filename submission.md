# Project 5: Mixtape Bug Hunt — Submission

## Milestone 1 — Orientation & Codebase Map

### How the app is structured

Mixtape is a Flask + SQLAlchemy JSON API. There is no front end — every feature is
an HTTP endpoint that returns JSON. The code is organized in three clear layers:

```
app.py        -> Flask app factory + DB setup (create_app, db = SQLAlchemy())
models.py     -> all SQLAlchemy models and association tables
routes/       -> HTTP layer: parse the request, call a service, format the response
services/     -> business logic layer: all the real work happens here
tests/        -> pytest tests (streaks, search, playlists)
seed_data.py  -> drops + recreates the DB and fills it with realistic test data
```

### Main files and what each one does

**`app.py`** — The application factory. `create_app(config)` builds the Flask app,
points SQLAlchemy at `sqlite:///mixtape.db` (overridable via `DATABASE_URL`),
registers the four blueprints under URL prefixes (`/songs`, `/playlists`,
`/users`, `/feed`), and calls `db.create_all()`. `db` is defined at module level so
every other module imports the *same* SQLAlchemy instance. This is why the app must
be started with `FLASK_APP=app:create_app flask run` and not `python app.py` — the
latter re-imports the module and creates a second `db`.

**`models.py`** — Defines 6 models and 3 association tables:

- `User` — has `listening_streak` and `last_listened_at` columns (used by the streak
  feature), plus a self-referential many-to-many `friends` relationship built on the
  `friendships` association table.
- `Song` — title/artist/album/genre, `shared_by` (FK to the user who shared it), and
  a many-to-many `tags` relationship (`lazy="subquery"`, so tags are always loaded).
- `Tag` — a simple name; joined to songs through the `song_tags` association table.
- `ListeningEvent` — one row per "user listened to a song at time T". This is the
  source of truth for both streaks and the "listening now" feed.
- `Rating` — a user's 1–5 score for a song, with a unique constraint on
  `(user_id, song_id)` so a user can only have one rating per song.
- `Playlist` — has a many-to-many `songs` relationship through the
  `playlist_entries` association table. That join table is the important detail: it
  carries extra columns `position` (explicit ordering, not insertion order),
  `added_by`, and `added_at`.
- `Notification` — `user_id` (recipient), `notification_type`, `body`, `read`.

**`routes/`** — Thin HTTP handlers. Each one parses inputs from the request,
delegates to exactly one service function, and wraps the result in `jsonify`.
`ValueError` from a service is caught and turned into a 400/404 JSON error.
- `songs.py` — `GET /songs/search`, `GET /songs/<id>`, `POST /songs/<id>/rate`,
  `POST /songs/<id>/listen`.
- `playlists.py` — create playlist, get playlist, `GET /playlists/<id>/songs`,
  `POST /playlists/<id>/songs`.
- `users.py` — get user, `GET /users/<id>/streak`, `GET /users/<id>/notifications`,
  mark notification read.
- `feed.py` — `GET /feed/<id>/listening-now`, `GET /feed/<id>/activity`.

**`services/`** — Where all the logic (and all the bugs) live:
- `streak_service.py` — `record_listening_event()` creates a `ListeningEvent` and
  calls `update_listening_streak()`, which compares today's date to
  `user.last_listened_at.date()` and increments / holds / resets the streak.
- `feed_service.py` — `get_friends_listening_now()` returns each friend's single most
  recent listen within a recency cutoff; `get_activity_feed()` returns the last N
  events regardless of age.
- `search_service.py` — `search_songs()` matches the query against title/artist
  (case-insensitive) and returns song dicts (with their tags).
- `notification_service.py` — `create_notification()`, `add_to_playlist()` (adds a
  song to a playlist **and** notifies the sharer), `rate_song()`, `get_notifications()`,
  `mark_as_read()`.
- `playlist_service.py` — `get_playlist_songs()` returns a playlist's songs ordered by
  `playlist_entries.position`, plus create/get helpers.

### Data flow trace — "a user rates a song"

This is the chain the README points to, traced through the real code:

1. `POST /songs/<song_id>/rate` with JSON `{"user_id", "score"}` hits
   `rate()` in [routes/songs.py](routes/songs.py). It validates that both fields are
   present, then calls `rate_song(user_id, song_id, int(score))`.
2. `rate_song()` in [services/notification_service.py](services/notification_service.py)
   validates the score is 1–5, loads the `Song` and the rating `User`, and looks for
   an existing `Rating` for that `(user, song)` pair. If one exists it updates the
   score; otherwise it creates a new `Rating`. Then it commits.
3. The route serializes the returned `Rating` with `.to_dict()` and responds `201`.

There is **no separate rating table lookup on read** — the score lives directly on
the `Rating` row. Compare this to the sibling function `add_to_playlist()` in the
same file, which does one extra thing after committing: it calls
`create_notification()` for `song.shared_by`. That side-by-side difference is the
whole story of Issue #4.

### Data flow trace — "a user listens to a song" (streak)

1. `POST /songs/<song_id>/listen` → `listen()` in [routes/songs.py](routes/songs.py)
   → `record_listening_event(user_id, song_id)`.
2. `record_listening_event()` in
   [services/streak_service.py](services/streak_service.py) creates a
   `ListeningEvent(listened_at=now)` and calls `update_listening_streak(user, now)`.
3. `update_listening_streak()` computes `days_since_last = (today - last_date).days`
   and branches: `0` → no change, `1` → increment, otherwise → reset to 1. Then it
   updates `user.last_listened_at = now` and commits.
4. Reading the streak later goes `GET /users/<id>/streak` → `get_streak()`, which just
   returns `user.listening_streak`.

The same `ListeningEvent` rows written in step 2 are what the feed service reads for
"Friends Listening Now" — one write, two features consuming it.

### Patterns I noticed

- **Strict route → service delegation.** Every route does input parsing + response
  formatting only; 100% of business logic lives in `services/`. So when an endpoint
  misbehaves, the bug is almost always in the service it calls (the README says this
  explicitly, and it holds up).
- **Services raise `ValueError` for "not found"/bad input**, and routes translate that
  into HTTP status codes. No custom exception types.
- **The interesting data lives on association tables, not the models.** Ordering
  (`playlist_entries.position`) and "who did it / when" (`added_by`, `added_at`) are
  columns on the join tables, so the ordered/dedup logic is in the queries, not the
  relationships.
- **Notifications are a side effect of interactions**, created imperatively inside the
  service that performs the interaction — not via events/signals. So a missing
  notification means a missing `create_notification()` call, not a broken subscriber.
- **`db.session.get(Model, id)` is the standard single-row load; `db.session.query(...)`
  for filters.** Search/feed/playlist all use the legacy `Query` API (not 2.0-style
  `select()`), which matters for Issue #3 (see below).

### The five issues — reproduction notes and my plan

I read all five reports and reproduced each against the seeded DB before planning.
Rough root cause for each (details will go in the Milestone 2 write-ups):

| # | Issue | Affected file | Reproduced? | Likely root cause |
|---|-------|---------------|-------------|-------------------|
| 1 | Streak resets on Sunday | `streak_service.py` | ✅ (failing test `test_streak_increments_on_sunday`) | Increment branch has an extra `and today.weekday() != 6`, so consecutive-day increments are skipped on Sundays and fall through to the reset branch. |
| 2 | "Listening now" shows yesterday | `feed_service.py` | ✅ (crafted an 11pm-yesterday event; still shows at 9am) | Uses a rolling `timedelta(hours=24)` cutoff instead of "since the start of today", so last night's listens linger into the morning. |
| 3 | Duplicate songs in search | `search_service.py` | ❌ **could not reproduce** | The query `outerjoin`s `song_tags` (one row per tag) which *would* duplicate multi-tag songs — but the legacy `Query.all()` API auto-dedupes entity rows by identity, so it currently returns each song once. The `outerjoin` is unnecessary (tags load via the `subquery` relationship). Worth hardening regardless. |
| 4 | No notification when a song is rated | `notification_service.py` | ✅ (rated a song; sharer got 0 notifications) | `rate_song()` never calls `create_notification()`. Its sibling `add_to_playlist()` does. Architectural omission, not a typo. |
| 5 | Last song in a playlist is missing | `playlist_service.py` | ✅ (failing test `test_playlist_returns_all_songs`) | `get_playlist_songs()` returns `songs[:-1]`, dropping the most recently ordered song. |

**Plan:** I'll fix at least three. My priority order is the four cleanly reproducible
bugs — **#1 (streak), #5 (playlist), #4 (notification), #2 (feed)** — because I can
verify each with a concrete before/after. For **#3 (search)** I'll document the
finding that it doesn't reproduce under the legacy Query API and, if I harden it, do
so by removing the unnecessary join rather than claiming a behavior change I can't
demonstrate. Each fix will be its own conventional-commit on `bugfix/mixtape`.

### AI tool disclosure

I used Claude Code to help me navigate the codebase during orientation: summarizing
each service file's responsibilities and tracing the route→service→model call chains
above. I verified every claim by reading the actual source and by reproducing each
bug against the seeded database (and the existing pytest suite) myself before writing
it down.

---

## Milestone 3 — Root Cause Analyses

I fixed **four** of the five bugs (#1, #5, #4, #2) and investigated the fifth (#3),
which I could not reproduce — the write-up for #3 explains why. Each fix is its own
commit on `bugfix/mixtape`.

### Issue #1 — My listening streak keeps resetting

**How I reproduced it.** The seed data gives kenji a `listening_streak` of 12. The
report says the reset happens only on Sundays, so the trigger is a *date condition*,
not an input. I reproduced it deterministically with the existing test
`test_streak_increments_on_sunday`, which listens on Saturday 2024-06-15
(`weekday() == 5`) then Sunday 2024-06-16 (`weekday() == 6`) and asserts the streak
goes 1 → 2. Before the fix it came back **1**: `assert 1 == 2`. So a Saturday→Sunday
pair — two consecutive calendar days — was being treated as a skipped day.

**How I found the root cause.** I traced the call chain from the reported endpoint:
`GET /users/<id>/streak` → `get_streak()` just returns the stored value, so the streak
must be corrupted at *write* time, not read time. Writes happen on
`POST /songs/<id>/listen` → `record_listening_event()` → `update_listening_streak()`
in [services/streak_service.py](services/streak_service.py). Reading that function,
the increment branch is the only place a "consecutive day" decision is made:
`elif days_since_last == 1 and today.weekday() != 6:`. The moment I saw the extra
`and today.weekday() != 6` I was confident — `datetime.weekday()` returns **6 for
Sunday**, so on Sundays the condition is `1 and False` → the `elif` is skipped and
execution falls into the `else: user.listening_streak = 1` reset branch, even though
exactly one day had passed.

**The root cause.** The consecutive-day check was `days_since_last == 1 and
today.weekday() != 6`. `days_since_last == 1` already correctly identifies
"yesterday → today". The extra `today.weekday() != 6` clause has no legitimate reason
to exist: it says "increment only if today is not Sunday." Because Python's
`weekday()` numbers days Mon=0 … Sun=6, every listen made on a Sunday failed the
condition, dropped through to the `else`, and reset the streak to 1 — which is exactly
kenji's "12 → 1 on Sunday morning" report.

**My fix and side-effect check.** I removed the `and today.weekday() != 6` clause so
the branch is simply `elif days_since_last == 1:`. That restores the intended rule:
one calendar day gap → increment, larger gap → reset. Side effects I checked: I ran
the full `tests/test_streaks.py` suite (5 tests). All pass, including
`test_streak_resets_after_skipped_day` (Monday→Wednesday still resets to 1) and
`test_streak_does_not_double_count_same_day` — so the *other* side of the boundary
(genuine skips still reset, same-day listens still hold) is intact. The fix touches
only the one boolean condition.
