#!/usr/bin/env python3
"""Build the database from schema.sql and seed.json.

Runs as ExecStartPre on every start, like the Elden Ring tracker's seeder — but
it does a different job, because the data here is not a reference dataset.

There, seed.json is the game's checklist and your ticks are the only thing worth
keeping, so the reference tables are dropped and rebuilt every time. Here
seed.json is an *origin snapshot* of a vault, and everything in it — chapter,
rating, status — is exactly the mutable state the app exists to edit. Rebuilding
it on every start would hand back the reading you did last week.

So seeding is additive, and what it keys on matters:

    a seed entry never imported before  ->  inserted, and recorded
    a seed entry already imported       ->  skipped, whatever became of it
    a series not from seed.json         ->  left completely alone

Note the middle line. Keying on "is this title in the series table?" is the
obvious version and it is wrong, because a title is not stable — the app can
rename one. Rename a seeded series and the next restart would see its original
title missing and import it a second time, so a duplicate appears after a
reboot; delete one on purpose and it comes back. `seed_applied` records what was
imported instead of inferring it, which closes both holes.

That makes the first start an import, every later start a no-op, and adding an
entry to seed.json a way to bulk-add series without touching what is there. The
vocabularies (status / pub / type) are the one exception: those are closed sets
and are upserted every time, so fixing an ordering or adding a status is just an
edit and a redeploy.

    python3 seed.py                # $MT_DB, or ./media.db from a checkout
    python3 seed.py --force-import # re-apply seed.json over existing rows
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = Path(os.environ.get("MT_DB") or HERE / "media.db")
SEED = Path(os.environ.get("MT_SEED") or HERE / "seed.json")
# Title -> tags, on the two axes below. Applied once; see apply_tags().
TAGSFILE = Path(os.environ.get("MT_TAGS") or HERE / "tags.json")
# Title -> {pub, type} looked up on Anime-Planet for the series imported from
# there, which the export itself did not carry. Applied once; see apply_ap().
APFILE = Path(os.environ.get("MT_ANIME_PLANET") or HERE / "anime-planet.json")
# The handful Anime-Planet got wrong, confirmed one at a time. See apply_verified().
VERIFIED = Path(os.environ.get("MT_VERIFIED") or HERE / "verified.json")
SCHEMA = Path(os.environ.get("MT_SCHEMA") or HERE / "schema.sql")

# What v_series joins a series' settings and genres with. Unit separator,
# because no title or vocabulary word can contain one.
SEP = "\x1f"

# Presentation order for the three closed vocabularies.
#
# `pub` has no Hold. Hold is a shelf — it says you stopped reading — and it
# answers a different question from "is the author still writing this". The
# vault had it on one note and it made the filter menu unreadable, because two
# menus offered the same word for two different things.
VOCAB = {
    # Shelves, and deliberately not "Reading" and "Read" any more: those two
    # named the medium rather than the state, and a game on a shelf called
    # Reading reads as a bug. `Current` and `Finished` say the same thing about
    # a book, an anime and a playthrough alike. The ids do not change, so the
    # status history written before the rename still resolves.
    "status": ["Current", "Later", "Hold", "Finished", "Dropped"],
    "pub": ["Ongoing", "Hiatus", "Completed", "Cancelled"],
}

# The two trackers, and what progress counts inside each. The app opens on a
# choice between them and everything after that happens inside one, so this is
# not a filter and never appears as a menu — it is the door you walked through.
#
# Anime, Shows and Films were three kinds for one activity. Nothing about the
# shelf below them differed — same statuses, same ratings, same tags, same
# episode counter twice over — so the split bought a filter that answered a
# question nobody was asking and made "is this an anime film or a film" a thing
# to decide before typing a title. What the thing *is* lives on `type`, which
# is where a distinction belongs when it is one.
#
# Playing is gone with the games it held. One playthrough was ever filed under
# it, games have their own tracker, and a third door onto a shelf of one is a
# door you press past every time you open the app.
KINDS = [
    ("Reading", "ch"),
    ("Watching", "ep"),
]

# Types, per kind, each with how you are partway through it: '' if you count in
# the kind's unit, 'once' if you do not count at all.
#
# Watching used to carry nine of these — TV, Series, Miniseries, Film, Short,
# OVA, ONA, Special, Documentary — which is a taxonomy of broadcast formats,
# not of things you watch. Four words cover it: whether it is animated, and
# whether it has episodes. A film is watched or it is not, which is why Movie
# and Animated Movie count nothing; "episode 1 of a movie" was always a lie the
# counter told because the counter belonged to the kind.
TYPES = {
    "Reading": [("Manhwa", ""), ("Manhua", ""), ("Manga", ""),
                ("Web Novel", ""), ("Indonesian Comic", "")],
    "Watching": [("Show", ""), ("Anime", ""),
                 ("Movie", "once"), ("Animated Movie", "once")],
}

# Watching types the four above replaced. Retired by name in migrate(), and
# only where nothing is filed under them — retyping somebody's library is not
# a rename.
RETIRED_TYPES = ["TV", "Miniseries", "Short", "OVA", "ONA", "Special",
                 "Documentary", "PC", "Console", "Handheld", "Mobile"]

# Setting and Genre — the two fields that replaced a tag box.
#
# This started as a flat pile of 59 hand-written tags in which `Fantasy` (half
# the shelf), `Transmigrassion` (a typo, 109 series) and `Boxing` (one series)
# were peers in one alphabetical menu. Splitting them onto two axes is what made
# them into fields: every word answers exactly one question,
#
#   setting  where does it take place
#   genre    what does it feel like
#
# and the app shows each as its own named row of pills you click. There is no
# free-text box any more, which is the point — a closed vocabulary you pick from
# cannot grow a second spelling of a word it already has, so the merge tool that
# used to sit in a Tags view has nothing left to do and is gone.
#
# Both are multi-select, because the shelf has series carrying two settings and
# three genres and one-of would have thrown that away.
#
# Order inside an axis is presentation order, roughly most-used first.
TAGS = {
    "setting": [
        "Fantasy", "Dark Fantasy", "Modern", "Hunter Fantasy", "Murim",
        "Wuxia", "Apocalypse", "Supernatural", "Sci-Fi", "Historical",
        "Academy", "School Life", "Tower", "Dungeon",
    ],
    "genre": [
        "Action", "Adventure", "Comedy", "Romance", "Drama", "Psychological",
        "Horror", "Thriller", "Mystery", "Slice of Life", "Sports",
    ],
}
AXIS_OF = {name: axis for axis, names in TAGS.items() for name in names}


def connect(path=DB):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")
    return db


def vocab_id(db, table, name):
    """Resolve a vocabulary value, adding it if the data has one we did not
    anticipate. Returns None for the empty string — two notes have no Type."""
    name = (name or "").strip()
    if not name:
        return None
    row = db.execute(f"SELECT id FROM {table} WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    pos = db.execute(f"SELECT COALESCE(MAX(pos), 0) + 1 FROM {table}").fetchone()[0]
    return db.execute(
        f"INSERT INTO {table}(name, pos) VALUES (?,?)", (name, pos)).lastrowid


def kind_units(db):
    return {r["name"]: r["unit"] for r in db.execute("SELECT name, unit FROM kind")}


def tag_id(db, name, axis=None):
    """Resolve a setting or genre, filing a new one onto the axis it came from.

    `axis` is what keeps the pickers honest. A word with none belongs to
    neither menu, so it can be attached to a series and then never seen again
    — which is exactly what the free-text tag box used to produce."""
    name = name.strip()
    row = db.execute("SELECT id, axis FROM tag WHERE name = ?", (name,)).fetchone()
    if row:
        if axis and not row["axis"]:
            db.execute("UPDATE tag SET axis = ? WHERE id = ?", (axis, row["id"]))
        return row["id"]
    return db.execute("INSERT INTO tag(name, axis) VALUES (?,?)",
                      (name, axis or AXIS_OF.get(name))).lastrowid


def reindex(db, series_id):
    """Rebuild one series' FTS row. The single place search text is defined."""
    r = db.execute("SELECT title, kind, type, setting, genre FROM v_series "
                   "WHERE id = ?", (series_id,)).fetchone()
    db.execute("DELETE FROM series_fts WHERE rowid = ?", (series_id,))
    if r:
        # Setting and genre share the index's `tags` column and kind rides in
        # its `type` column, rather than each getting one of its own: an FTS5
        # table's columns cannot be added later without rebuilding it, and
        # "anime" and "Movie" are the same kind of search term either way.
        words = " ".join(filter(None, (r["setting"], r["genre"]))).replace(SEP, " ")
        db.execute(
            "INSERT INTO series_fts(rowid, title, tags, type) VALUES (?,?,?,?)",
            (series_id, r["title"], words.strip(),
             f"{r['kind'] or ''} {r['type'] or ''}".strip()))


def upsert_vocab(db):
    for table, names in VOCAB.items():
        for pos, name in enumerate(names, start=1):
            db.execute(
                f"INSERT INTO {table}(name, pos) VALUES (?,?) "
                f"ON CONFLICT(name) DO UPDATE SET pos = excluded.pos", (name, pos))

    for pos, (name, unit) in enumerate(KINDS, start=1):
        db.execute("INSERT INTO kind(name, pos, unit) VALUES (?,?,?) "
                   "ON CONFLICT(name) DO UPDATE SET pos = excluded.pos, "
                   "unit = excluded.unit", (name, pos, unit))

    kinds = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM kind")}
    pos = 0
    for kind, entries in TYPES.items():
        for name, progress in entries:
            pos += 1
            # `name` is UNIQUE across both kinds, which the four Watching words
            # and the five Reading ones have no trouble with. `progress` is
            # authoritative here and overwritten every start: it is a property
            # of the word, not something a row can drift away from.
            db.execute("INSERT INTO type(name, pos, kind_id, progress) "
                       "VALUES (?,?,?,?) "
                       "ON CONFLICT(name) DO UPDATE SET pos = excluded.pos, "
                       "kind_id = COALESCE(type.kind_id, excluded.kind_id), "
                       "progress = excluded.progress",
                       (name, pos, kinds.get(kind), progress))
    # Tags are upserted by name too, but only their axis is authoritative here:
    # a tag the user invented in the sheet keeps existing with axis NULL, and
    # one of ours gets its axis restored if it was somehow cleared.
    for axis, names in TAGS.items():
        for name in names:
            db.execute(
                "INSERT INTO tag(name, axis) VALUES (?,?) "
                "ON CONFLICT(name) DO UPDATE SET axis = excluded.axis",
                (name, axis))


def once(db, name):
    """True the first time a named repair is asked for, False ever after."""
    if db.execute("SELECT 1 FROM migration WHERE name = ?", (name,)).fetchone():
        return False
    db.execute("INSERT INTO migration(name) VALUES (?)", (name,))
    return True


def add_column(db, table, column, decl):
    """ALTER TABLE ... ADD COLUMN, but only when it is actually missing.

    `CREATE TABLE IF NOT EXISTS` in schema.sql is a no-op against a database
    that already exists, so a new column in that file reaches a fresh install
    and nothing else. This is how it reaches the one on the server."""
    cols = [r["name"] for r in db.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def migrate(db):
    """One-time repairs. Each runs once, on whichever start first sees it."""
    add_column(db, "tag", "axis", "TEXT")
    add_column(db, "series", "kind_id", "INTEGER REFERENCES kind(id)")
    add_column(db, "series", "tome", "REAL")
    add_column(db, "series", "season", "REAL")
    add_column(db, "type", "kind_id", "INTEGER REFERENCES kind(id)")
    add_column(db, "type", "progress", "TEXT NOT NULL DEFAULT ''")

    # v_series gained kind and unit. A view is not a table: dropping and
    # recreating it costs nothing and is the only way to change one, and
    # `CREATE VIEW IF NOT EXISTS` in schema.sql will not touch an existing one.
    if once(db, "view-kind"):
        db.execute("DROP VIEW IF EXISTS v_series")

    # Reading and Read named the medium, not the state. Renaming the rows keeps
    # their ids, so every status change already in status_log still resolves.
    if once(db, "status-kind-neutral"):
        for old_name, new_name in (("Reading", "Current"), ("Read", "Finished")):
            db.execute("UPDATE status SET name = ? WHERE name = ?",
                       (new_name, old_name))
        print("  migrate: shelves renamed Reading→Current, Read→Finished")

    # Everything that existed before there were kinds is Reading.
    if once(db, "kind-backfill"):
        db.execute("INSERT INTO kind(name, pos, unit) VALUES ('Reading',1,'ch') "
                   "ON CONFLICT(name) DO NOTHING")
        rid = db.execute("SELECT id FROM kind WHERE name='Reading'").fetchone()[0]
        n = db.execute("UPDATE series SET kind_id = ? WHERE kind_id IS NULL",
                       (rid,)).rowcount
        db.execute("UPDATE type SET kind_id = ? WHERE kind_id IS NULL", (rid,))
        print(f"  migrate: {n} series filed under Reading")

    # A rating of -10 was a verdict rather than a score. Clamping it is the
    # user's own call, recorded here rather than done silently every start.
    if once(db, "rating-nonneg"):
        n = db.execute("UPDATE series SET rating = 0 WHERE rating < 0").rowcount
        if n:
            print(f"  migrate: {n} negative rating(s) set to 0")

    # Xianxia and wuxia are one shelf here, under the name that is actually
    # said out loud. Renaming rather than merging: nothing was tagged Wuxia.
    if once(db, "tag-xianxia-to-wuxia"):
        n = db.execute(
            "UPDATE tag SET name = 'Wuxia' WHERE name = 'Xianxia' "
            "AND NOT EXISTS (SELECT 1 FROM tag WHERE name = 'Wuxia')").rowcount
        if n:
            print("  migrate: Xianxia renamed to Wuxia")

    # The premise axis is gone — setting and genre are the two questions worth
    # asking. Dropping the tags takes their join rows with them by cascade;
    # the FTS rows for the series that wore them have to be rebuilt by hand,
    # because series_fts is not a real foreign key.
    if once(db, "tag-drop-premise"):
        hit = [r[0] for r in db.execute(
            "SELECT DISTINCT series_id FROM series_tag WHERE tag_id IN "
            "(SELECT id FROM tag WHERE axis = 'premise')")]
        n = db.execute("DELETE FROM tag WHERE axis = 'premise'").rowcount
        for sid in hit:
            reindex(db, sid)
        if n:
            print(f"  migrate: dropped {n} premise tag(s) from {len(hit)} series")
        # Three series wore nothing *but* premise tags and came out of that
        # delete with none at all. Losing an axis should not mean losing a
        # series from the filters, so they are re-read from tags.json — which
        # apply_tags() will not do on its own, having already run.
        if TAGSFILE.exists():
            plan = json.loads(TAGSFILE.read_text(encoding="utf-8"))["tags"]
            bare = db.execute(
                "SELECT id, title FROM series WHERE id NOT IN "
                "(SELECT series_id FROM series_tag)").fetchall()
            for row in bare:
                for name in plan.get(row["title"], []):
                    db.execute("INSERT OR IGNORE INTO series_tag(series_id, tag_id) "
                               "VALUES (?,?)", (row["id"], tag_id(db, name)))
                reindex(db, row["id"])
            if bare:
                print(f"  migrate: re-tagged {len(bare)} series left bare by that")

    # Hold left `pub` (see VOCAB). Anything wearing it meant Hiatus.
    if once(db, "pub-drop-hold"):
        row = db.execute("SELECT id FROM pub WHERE name = 'Hold'").fetchone()
        if row:
            hiatus = db.execute("SELECT id FROM pub WHERE name = 'Hiatus'").fetchone()
            n = db.execute("UPDATE series SET pub_id = ? WHERE pub_id = ?",
                           (hiatus["id"], row["id"])).rowcount
            db.execute("DELETE FROM pub WHERE id = ?", (row["id"],))
            print(f"  migrate: pub 'Hold' retired, {n} series moved to Hiatus")

    # Anime, Shows and Films collapse into Watching, and Games becomes Playing.
    # Renaming rather than replacing, so kind_id keeps pointing where it did
    # and nothing has to be re-filed: Anime *is* the Watching row now. The
    # other two are re-pointed and then deleted, types first — type.kind_id is
    # ON DELETE SET NULL, so dropping a kind out from under Series and
    # Miniseries would strand them in no menu at all.
    if once(db, "kind-three-verbs"):
        db.execute("UPDATE kind SET name = 'Watching' WHERE name = 'Anime'")
        db.execute("UPDATE kind SET name = 'Playing' WHERE name = 'Games'")
        row = db.execute("SELECT id FROM kind WHERE name = 'Watching'").fetchone()
        if row:
            gone = "SELECT id FROM kind WHERE name IN ('Shows', 'Films')"
            db.execute(f"UPDATE type SET kind_id = ? WHERE kind_id IN ({gone})",
                       (row["id"],))
            n = db.execute(f"UPDATE series SET kind_id = ? WHERE kind_id IN ({gone})",
                           (row["id"],)).rowcount
            db.execute(f"DELETE FROM kind WHERE id IN ({gone})")
            print(f"  migrate: Anime/Shows/Films are one kind, Watching"
                  + (f" ({n} series moved)" if n else ""))

        # Movie and Film were the same word in two kinds. One kind, one word —
        # but only if nothing is filed under it, because retyping somebody's
        # library is not a rename.
        film = db.execute("SELECT id FROM type WHERE name = 'Film'").fetchone()
        movie = db.execute("SELECT id FROM type WHERE name = 'Movie'").fetchone()
        if film and movie:
            n = db.execute("UPDATE series SET type_id = ? WHERE type_id = ?",
                           (film["id"], movie["id"])).rowcount
            db.execute("DELETE FROM type WHERE id = ?", (movie["id"],))
            print(f"  migrate: type 'Movie' retired into 'Film'"
                  + (f", {n} series moved" if n else ""))


    # Two trackers, and the shelf below each of them is its own.
    #
    # Everything in here is a change of mind rather than a change of data, so
    # it runs exactly once and the user is free to undo any of it afterwards.
    if once(db, "two-trackers"):
        # Playing leaves, and the one playthrough filed under it leaves with
        # it — Elden Ring has a tracker of its own and does not need a door
        # here. Series first: type.kind_id is ON DELETE SET NULL but
        # series.type_id is RESTRICT, so a type cannot go while a row wears it.
        gone = [r["id"] for r in db.execute(
            "SELECT s.id FROM series s JOIN kind k ON k.id = s.kind_id "
            "WHERE k.name = 'Playing'")]
        for sid in gone:
            db.execute("DELETE FROM series WHERE id = ?", (sid,))
            db.execute("DELETE FROM series_fts WHERE rowid = ?", (sid,))
        n = db.execute("DELETE FROM type WHERE kind_id IN "
                       "(SELECT id FROM kind WHERE name = 'Playing')").rowcount
        if db.execute("DELETE FROM kind WHERE name = 'Playing'").rowcount:
            print(f"  migrate: Playing retired ({len(gone)} series, {n} type(s))")

        # Watching's nine broadcast formats become four words. Renaming rather
        # than re-filing keeps type_id pointing where it did: Series *is* Show
        # now, and Film *is* Movie. Guarded on the new name not already
        # existing, because a rename onto a taken name is a UNIQUE violation.
        for old_name, new_name in (("Series", "Show"), ("Film", "Movie")):
            db.execute(
                "UPDATE type SET name = ? WHERE name = ? AND NOT EXISTS "
                "(SELECT 1 FROM type WHERE name = ?)",
                (new_name, old_name, new_name))

        # The rest go only if nothing is filed under them. Retyping somebody's
        # library is not a rename, and a type left standing with one series on
        # it is a smaller wrong than a series silently retyped.
        for name in RETIRED_TYPES:
            row = db.execute("SELECT id FROM type WHERE name = ?", (name,)).fetchone()
            if row and not db.execute(
                    "SELECT 1 FROM series WHERE type_id = ?", (row["id"],)).fetchone():
                db.execute("DELETE FROM type WHERE id = ?", (row["id"],))

        # Notes go. 618 of them held the leftovers of a vault import — a
        # `Best Read:` line, a stray paragraph — and none of it was ever read
        # back. Dropping a column means taking down everything built over it
        # first: the view names it, and the FTS table indexed it. Both are
        # recreated by the second executescript in main(), and the heal loop
        # at the end of main() rebuilds every search row, because there is no
        # FTS table left for any of them to be in.
        cols = [r["name"] for r in db.execute("PRAGMA table_info(series)")]
        if "notes" in cols:
            db.execute("DROP VIEW IF EXISTS v_series")
            db.execute("DROP TABLE IF EXISTS series_fts")
            db.execute("ALTER TABLE series DROP COLUMN notes")
            print("  migrate: notes dropped, search index rebuilding")
        else:
            # A database that never had notes still needs the view rebuilt:
            # it gained tome, progress, and setting and genre as two columns.
            db.execute("DROP VIEW IF EXISTS v_series")
            db.execute("DROP TABLE IF EXISTS series_fts")


    # A Show or an Anime always has a season, so the ones that predate the
    # column get the only answer that is nearly always right. It is a number in
    # a field he can edit, not a claim — which is the difference between a
    # default and a guess written somewhere he cannot see it.
    #
    # Matched on the type's *name*, not on `type.progress`. This runs before
    # upsert_vocab, so on a database that has only just been given the progress
    # column every row still reads '' — and keying off that filed a film under
    # season 1. TYPES above is the authority on which types count, and it does
    # not depend on what order anything ran in.
    if once(db, "season-backfill"):
        counted = [name for name, mode in TYPES["Watching"] if mode == ""]
        films   = [name for name, mode in TYPES["Watching"] if mode == "once"]
        marks = lambda names: ",".join("?" * len(names))
        n = db.execute(f"""
            UPDATE series SET season = 1 WHERE season IS NULL AND type_id IN
              (SELECT id FROM type WHERE name IN ({marks(counted)}))""",
            counted).rowcount
        # And a film has no season at all, however it came by one.
        db.execute(f"""
            UPDATE series SET season = NULL WHERE season IS NOT NULL AND type_id IN
              (SELECT id FROM type WHERE name IN ({marks(films)}))""", films)
        if n:
            print(f"  migrate: {n} show(s) filed under season 1")


def apply_tags(db, plan):
    """Re-tag the shelf from tags.json, once.

    Deliberately a migration and not part of the additive seed. These are
    *classifications* — the axis vocabulary above applied to every title — and
    replacing a series' tags is destructive of anything hand-typed. Doing it
    once means the user can re-tag anything he disagrees with afterwards and
    keep the change; doing it every start would mean losing that edit on the
    next reboot, which is the exact failure `seed_applied` exists to prevent.
    """
    if not once(db, "tags-three-axis"):
        return 0
    by_title = {r["title"]: r["id"] for r in db.execute("SELECT id, title FROM series")}
    touched = 0
    for title, names in plan.items():
        sid = by_title.get(title)
        if sid is None:
            continue
        db.execute("DELETE FROM series_tag WHERE series_id = ?", (sid,))
        for name in names:
            db.execute(
                "INSERT OR IGNORE INTO series_tag(series_id, tag_id) VALUES (?,?)",
                (sid, tag_id(db, name)))
        reindex(db, sid)
        touched += 1
    # Whatever is left over from the old flat vocabulary and now carries
    # nothing. Tags the user made himself are not in TAGS and are kept only if
    # something still wears them, which is the same rule.
    dead = db.execute(
        "DELETE FROM tag WHERE id NOT IN (SELECT tag_id FROM series_tag)").rowcount
    print(f"  migrate: re-tagged {touched} series, dropped {dead} unused tag(s)")
    return touched


def clamp_rating(v):
    """0-10, or None. One of the two write paths that enforce it; app.py is the
    other. See the CHECK in schema.sql for why it is done here and not there."""
    if v is None or v == "":
        return None
    try:
        return min(10.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return None


def apply_series(db, s, series_id=None):
    """Insert, or overwrite an existing row when --force-import is given."""
    vals = (
        s["title"], s.get("chapter"), clamp_rating(s.get("rating")),
        vocab_id(db, "kind", s.get("kind") or "Reading"),
        vocab_id(db, "status", s.get("status")),
        vocab_id(db, "pub", s.get("pub")),
        vocab_id(db, "type", s.get("type")),
        s.get("cover") or "",
    )
    if series_id is None:
        series_id = db.execute(
            "INSERT INTO series(title, chapter, rating, kind_id, status_id, pub_id,"
            " type_id, cover) VALUES (?,?,?,?,?,?,?,?)", vals).lastrowid
    else:
        db.execute(
            "UPDATE series SET title=?, chapter=?, rating=?, kind_id=?, status_id=?,"
            " pub_id=?, type_id=?, cover=?, updated_at=datetime('now')"
            " WHERE id=?", vals + (series_id,))
        db.execute("DELETE FROM series_tag WHERE series_id = ?", (series_id,))

    for t in s.get("tags") or []:
        db.execute("INSERT OR IGNORE INTO series_tag(series_id, tag_id) VALUES (?,?)",
                   (series_id, tag_id(db, t)))
    reindex(db, series_id)
    return series_id


def apply_ap(db, data):
    """Fill publication status and type from Anime-Planet, once, and only where
    the field is still empty.

    Only-where-empty is the same rule the import itself ran under: anything
    already recorded here beats anything a lookup says, whether it was typed
    last week or came out of the vault. This is a backfill for fields that were
    blank because the export had nothing to put in them.

    Anime-Planet publishes a year range and nothing else — "2018 - ?" is
    running, "2018 - 2023" is finished — so this can only ever produce Ongoing
    and Completed. Hiatus and Cancelled are judgements it does not make, and
    guessing them from a stalled year range would put a wrong word on a shelf
    rather than leave an honest blank.
    """
    if not once(db, "anime-planet-backfill"):
        return
    by_title = {r["title"]: r["id"] for r in db.execute("SELECT id, title FROM series")}
    pubs = types = 0
    for title, got in data.items():
        sid = by_title.get(title)
        if sid is None:
            continue
        row = db.execute("SELECT pub_id, type_id FROM series WHERE id = ?",
                         (sid,)).fetchone()
        if got.get("pub") and row["pub_id"] is None:
            db.execute("UPDATE series SET pub_id = ? WHERE id = ?",
                       (vocab_id(db, "pub", got["pub"]), sid))
            pubs += 1
        if got.get("type") and row["type_id"] is None:
            db.execute("UPDATE series SET type_id = ? WHERE id = ?",
                       (vocab_id(db, "type", got["type"]), sid))
            reindex(db, sid)          # type is in the search index
            types += 1
    print(f"  migrate: Anime-Planet filled {pubs} publication status(es) "
          f"and {types} type(s)")


def apply_verified(db, data, ap_data):
    """Hand-checked publication statuses, applied over the Anime-Planet guess.

    Anime-Planet publishes a year range and no more, so a series it still shows
    as `2019 - ?` may have finished years ago. Nothing fills that gap in bulk:
    MangaDex was wrong on both series it was spot-checked against, and AniList
    on three of six — a licensed webtoon taken off an aggregator looks exactly
    like one that stopped. So these are one at a time, off a source that said
    the fact outright.

    Only written where the current value is still the one Anime-Planet wrote.
    A field he has touched since is his, and beats a lookup.
    """
    if not once(db, "verified-pub-2026-08-b"):
        return
    rows = {r["title"]: r for r in db.execute(
        "SELECT s.id, s.title, p.name AS pub FROM series s "
        "LEFT JOIN pub p ON p.id = s.pub_id")}
    n = already = skipped = 0
    for title, got in data.items():
        row = rows.get(title)
        if row is None:
            continue
        if row["pub"] == got["pub"]:
            already += 1          # an earlier batch already set this one
            continue
        was = (ap_data.get(title) or {}).get("pub")
        if was and row["pub"] != was:
            # Not what Anime-Planet wrote and not what we want: he changed it.
            skipped += 1
            continue
        db.execute("UPDATE series SET pub_id = ? WHERE id = ?",
                   (vocab_id(db, "pub", got["pub"]), row["id"]))
        n += 1
    print(f"  migrate: {n} verified publication status(es)"
          + (f", {already} already set" if already else "")
          + (f", {skipped} left alone as edited by hand" if skipped else ""))


def main():
    ap = argparse.ArgumentParser(description="Seed the reading tracker database")
    ap.add_argument("--force-import", action="store_true",
                    help="re-apply seed.json over rows that already exist")
    a = ap.parse_args()

    DB.parent.mkdir(parents=True, exist_ok=True)
    db = connect()
    schema = SCHEMA.read_text(encoding="utf-8")
    db.executescript(schema)
    migrate(db)
    # Again, deliberately. Everything in schema.sql is IF NOT EXISTS, so the
    # second run is a no-op except for whatever `migrate` just dropped —
    # which is how a view gets changed at all, `CREATE VIEW IF NOT EXISTS`
    # having no opinion about a view that already exists and is wrong.
    db.executescript(schema)
    upsert_vocab(db)

    payload = json.loads(SEED.read_text(encoding="utf-8"))
    applied = {r["title"]: r["series_id"]
               for r in db.execute("SELECT title, series_id FROM seed_applied")}
    by_title = {r["title"]: r["id"] for r in db.execute("SELECT id, title FROM series")}

    added = updated = 0
    for s in payload["series"]:
        title = s["title"]
        if title not in applied:
            # A title present in `series` but not in `seed_applied` is a series
            # the user created by hand that happens to share the name. Adopt it
            # rather than fail on the UNIQUE constraint.
            sid = by_title.get(title)
            if sid is None:
                sid = apply_series(db, s)
                added += 1
            db.execute(
                "INSERT OR REPLACE INTO seed_applied(title, series_id) VALUES (?,?)",
                (title, sid))
        elif a.force_import and applied[title] is not None:
            apply_series(db, s, applied[title])
            updated += 1

    if TAGSFILE.exists():
        apply_tags(db, json.loads(TAGSFILE.read_text(encoding="utf-8"))["tags"])
    ap_data = {}
    if APFILE.exists():
        ap_data = json.loads(APFILE.read_text(encoding="utf-8"))["series"]
        apply_ap(db, ap_data)
    if VERIFIED.exists():
        apply_verified(db, json.loads(VERIFIED.read_text(encoding="utf-8"))["series"],
                       ap_data)

    # A row whose FTS entry went missing — an interrupted write, or a database
    # that predates the index — would be invisible to search while looking
    # perfectly fine everywhere else. Cheap to check, so check every start.
    healed = 0
    for (sid,) in db.execute(
            "SELECT id FROM series WHERE id NOT IN "
            "(SELECT rowid FROM series_fts)").fetchall():
        reindex(db, sid)
        healed += 1

    db.commit()
    total = db.execute("SELECT COUNT(*) FROM series").fetchone()[0]
    tags = db.execute("SELECT COUNT(*) FROM tag").fetchone()[0]
    kept = total - added
    print(f"seed: {total} series ({added} imported, {kept} left untouched"
          + (f", {updated} re-imported" if updated else "")
          + (f", {healed} search rows healed" if healed else "")
          + f"), {tags} tags → {DB}")
    db.close()


if __name__ == "__main__":
    sys.exit(main())
