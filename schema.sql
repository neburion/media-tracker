-- Media tracker schema.
--
-- The vault this was imported from could only ever hold current state: one
-- markdown note per series, each frontmatter key overwritten in place. Moving
-- to a real database is what buys the two things it could not express —
-- referential integrity over the vocabularies, and history.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── vocabularies ─────────────────────────────────────────────────────────
-- Closed sets that were free text in the frontmatter, which is how the vault
-- ended up spelling one value two ways. `pos` carries presentation order,
-- which is not alphabetical: what you are reading now belongs at the top of the
-- shelf and what you abandoned belongs at the bottom.

-- `kind` is the tracker a row belongs to, and there are two: Reading and
-- Watching. It is deliberately not a filter any more — the app opens on a
-- choice between the two and everything after that happens inside one of them,
-- so nothing in the UI ever asks which kind a series is. Walking through the
-- Reading door is what says it.
--
-- `unit` is what a kind's progress column counts by default: chapters or
-- episodes. A type can override it (see `type.progress`) for the things you do
-- not count at all.
--
-- Playing is gone. One playthrough was ever filed under it and games have
-- their own tracker; a third door for a shelf of one was noise.

CREATE TABLE IF NOT EXISTS kind (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  pos  INTEGER NOT NULL,
  unit TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS status (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  pos  INTEGER NOT NULL
);

-- `pub` answers "is the author still writing it", which is not the same
-- question as "am I still reading it" — that is `status`. The vault confused
-- the two and left a `Publication Status: Hold` on one note; Hold is a shelf,
-- so that value is gone and the note reads Hiatus.
CREATE TABLE IF NOT EXISTS pub (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  pos  INTEGER NOT NULL
);

-- `type` hangs off a kind: Manhwa belongs to Reading, Movie to Watching, and
-- offering both in one menu is the same mistake the flat tag list was.
-- Nullable kind_id for a value that predates the split.
--
-- `progress` is how you are partway through this type of thing:
--
--   ''      you count, in the kind's unit — chapters of a manhwa, episodes
--           of a show
--   'once'  you do not count. A film is watched or it is not, and a counter
--           reading "episode 1 of a movie" was always a lie.
--
-- It lives on the type rather than the kind because Watching holds both: a
-- Show has episodes and a Movie has an evening.
CREATE TABLE IF NOT EXISTS type (
  id       INTEGER PRIMARY KEY,
  name     TEXT NOT NULL UNIQUE,
  pos      INTEGER NOT NULL,
  kind_id  INTEGER REFERENCES kind(id) ON DELETE SET NULL,
  progress TEXT NOT NULL DEFAULT ''
);

-- ── series ───────────────────────────────────────────────────────────────
-- `title` is the natural key: it is what the vault note was named, and it is
-- what seed.py re-attaches on, so re-importing never duplicates a row.
--
-- The vocabulary columns are nullable because the vault has a note with no
-- Type and a note with no Publication Status. ON DELETE RESTRICT so a
-- vocabulary entry cannot be removed out from under a series that uses it.

CREATE TABLE IF NOT EXISTS series (
  id         INTEGER PRIMARY KEY,
  title      TEXT NOT NULL UNIQUE,
  -- Still called `chapter`, and it now counts episodes too — and for a film it
  -- holds 1 or nothing, because "watched" is progress with only two values.
  -- Renaming it would mean rewriting the view, both history tables, every read
  -- and write in app.py and the whole of ui.html, against a live database with
  -- reading history hanging off it — a lot of blast radius for a word. What it
  -- means is carried by kind.unit and type.progress, which is the part the
  -- reader sees.
  chapter    REAL,
  -- Volumes, and optional in the real sense: null means you are not counting
  -- them, which is most of the shelf. Deliberately not logged — reading_log is
  -- about chapters, and a second unit in it would make "what have I been
  -- reading" a question with two answers.
  tome       REAL,
  -- 0-10. This once admitted -10, to hold a single series rated in anger; that
  -- was a verdict rather than a score, and it has been set to 0. Databases
  -- created before that keep the wider CHECK — rebuilding a table to tighten a
  -- constraint is not worth the risk to the reading history hanging off it —
  -- but nothing can write a negative any more: app.py clamps on the way in and
  -- seed.py clamps on import, which are the only two writers there are.
  rating     REAL CHECK (rating IS NULL OR (rating >= 0 AND rating <= 10)),
  kind_id    INTEGER REFERENCES kind(id)   ON DELETE RESTRICT,
  status_id  INTEGER REFERENCES status(id) ON DELETE RESTRICT,
  pub_id     INTEGER REFERENCES pub(id)    ON DELETE RESTRICT,
  type_id    INTEGER REFERENCES type(id)   ON DELETE RESTRICT,
  cover      TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS series_status ON series(status_id);
CREATE INDEX IF NOT EXISTS series_updated ON series(updated_at DESC);

-- ── setting and genre ────────────────────────────────────────────────────
-- A real many-to-many, which is what makes `UPDATE tag SET name = ?` a
-- complete rename rather than a rewrite of 11 files.
--
-- The table is still called `tag`, because that is what it stores: a word
-- attached to many series. What is gone is the idea that the *user* deals in
-- tags. `axis` says which of the two questions a word answers — where it is
-- set, or what it feels like — and the app exposes those as two named fields
-- you pick from, Setting and Genre. There is no free-text box any more, so there
-- is no way to invent a word, which is why nothing here has to guard against
-- one being spelled two ways.
CREATE TABLE IF NOT EXISTS tag (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  axis TEXT
);

CREATE TABLE IF NOT EXISTS series_tag (
  series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  tag_id    INTEGER NOT NULL REFERENCES tag(id)    ON DELETE CASCADE,
  PRIMARY KEY (series_id, tag_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS series_tag_tag ON series_tag(tag_id);

-- ── history ──────────────────────────────────────────────────────────────
-- The point of owning a database. Every chapter change is appended here, so
-- the shelf can answer "what have I actually been reading lately", which no
-- amount of frontmatter could. `from_ch` is nullable for a series that had no
-- chapter recorded before the change — which is every film the first time you
-- watch it, so the log doubles as the date you saw it.

CREATE TABLE IF NOT EXISTS reading_log (
  id        INTEGER PRIMARY KEY,
  series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  from_ch   REAL,
  to_ch     REAL,
  at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS reading_log_at ON reading_log(at DESC);
CREATE INDEX IF NOT EXISTS reading_log_series ON reading_log(series_id, at DESC);

-- Status changes are worth keeping too: "when did I give up on this" is a
-- question the vault threw away every time it was answered.
CREATE TABLE IF NOT EXISTS status_log (
  id        INTEGER PRIMARY KEY,
  series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  from_id   INTEGER REFERENCES status(id) ON DELETE SET NULL,
  to_id     INTEGER REFERENCES status(id) ON DELETE SET NULL,
  at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS status_log_at ON status_log(at DESC);

-- ── seed bookkeeping ─────────────────────────────────────────────────────
-- Which seed.json entries have ever been imported.
--
-- The obvious implementation of an additive seeder is "insert every title that
-- is not already in `series`" — and it is wrong, because a title is not stable:
-- the app can rename one. Rename a seeded series and the next restart sees its
-- original title missing and imports it again, so a duplicate quietly appears
-- after a reboot. The same hole swallows deletions: anything you removed on
-- purpose comes back.
--
-- Recording what was imported, rather than inferring it, closes both. The key
-- is the title *as it appeared in seed.json*, which never changes because that
-- file is in the read-only store.

CREATE TABLE IF NOT EXISTS seed_applied (
  title     TEXT PRIMARY KEY,
  series_id INTEGER REFERENCES series(id) ON DELETE SET NULL,
  at        TEXT NOT NULL DEFAULT (datetime('now'))
) WITHOUT ROWID;

-- One-time repairs that have already run, so they do not run again. The seeder
-- is otherwise entirely declarative — this is the escape hatch for the handful
-- of things that are a *change of mind* rather than a change of data, like
-- retiring a vocabulary value or reclassifying every tag on the shelf. Those
-- must happen exactly once, because the user is free to undo them afterwards
-- and a seeder that re-applied them would be arguing with him every restart.

CREATE TABLE IF NOT EXISTS migration (
  name TEXT PRIMARY KEY,
  at   TEXT NOT NULL DEFAULT (datetime('now'))
) WITHOUT ROWID;

-- ── views ────────────────────────────────────────────────────────────────
-- One row per series with the vocabularies resolved and both word lists
-- collapsed, so the API is a single SELECT rather than 300 round trips.
--
-- `unit` is the word the progress number wears, already resolved: the kind's,
-- unless the type says you do not count this at all, in which case there is no
-- number and no word for it. `progress` carries that distinction itself, so
-- the UI can tell "no unit" from "a film".
--
-- Setting and genre come out as two separate lists rather than one, because
-- they are two fields. Collapsing them into one and splitting by axis later
-- would mean the API knowing something the query already knew.

CREATE VIEW IF NOT EXISTS v_series AS
SELECT
  s.id, s.title, s.chapter, s.tome, s.rating, s.cover,
  s.created_at, s.updated_at,
  COALESCE(kn.name, '') AS kind,
  COALESCE(ty.progress, '') AS progress,
  CASE WHEN COALESCE(ty.progress, '') = 'once' THEN ''
       ELSE COALESCE(kn.unit, '') END AS unit,
  COALESCE(st.name, '') AS status,
  COALESCE(pb.name, '') AS pub,
  COALESCE(ty.name, '') AS type,
  st.pos AS status_pos,
  (SELECT COUNT(*) FROM reading_log rl WHERE rl.series_id = s.id) AS log_count,
  (SELECT MAX(rl.at) FROM reading_log rl WHERE rl.series_id = s.id) AS last_read,
  (SELECT GROUP_CONCAT(t.name, CHAR(31))
     FROM series_tag stg JOIN tag t ON t.id = stg.tag_id
    WHERE stg.series_id = s.id AND t.axis = 'setting'
    ORDER BY t.name) AS setting,
  (SELECT GROUP_CONCAT(t.name, CHAR(31))
     FROM series_tag stg JOIN tag t ON t.id = stg.tag_id
    WHERE stg.series_id = s.id AND t.axis = 'genre'
    ORDER BY t.name) AS genre
FROM series s
LEFT JOIN kind   kn ON kn.id = s.kind_id
LEFT JOIN status st ON st.id = s.status_id
LEFT JOIN pub    pb ON pb.id = s.pub_id
LEFT JOIN type   ty ON ty.id = s.type_id;

-- ── search ───────────────────────────────────────────────────────────────
-- FTS5 over title, the two word lists and type. nixpkgs builds python3's
-- sqlite3 with FTS5, same as the Elden Ring tracker relies on.
--
-- Deliberately NOT an external-content table and deliberately not maintained by
-- triggers: a series' searchable text includes its setting and genre, which
-- live in a join table, so a trigger would have to fire on series_tag as well
-- and recompute the parent row anyway. `reindex()` in seed.py does that in one
-- place and is called from every write path, which is easier to keep honest
-- than four triggers that have to agree. rowid is series.id.

CREATE VIRTUAL TABLE IF NOT EXISTS series_fts USING fts5(
  title, tags, type,
  tokenize = "unicode61 remove_diacritics 2"
);
