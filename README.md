# Media tracker

SQLite + a small web UI. Stdlib Python only — no Flask, no pip.

**Two trackers, and you walk into one.** The app opens on a choice — Reading
or Watching — and everything after that happens inside a single shelf: its own
types, its own filters, its own statistics. There is no view that mixes them
and nothing that asks which one a title belongs to, because the door answered
that. It began as a reading tracker, which is why the 921 series it started
with are all filed under `Reading` and why the progress column in the database
is still called `chapter`.

Deployed from `app.json` on `personal-server`: **http://personal-server:8778**
on the tailnet, and publicly at **https://media.azuresalt.app**.

`reading.azuresalt.app` used to be a second tunnel onto the same service, from
when this was only a reading tracker. It was one name too many for one app, and
it is gone — tunnel and DNS record deleted, not just unwired. The NixOS config
reads `app.json` out of this repo and generates the unit, the system user, the
state directory, the credential wiring, the firewall rule and the tunnel from
it — see `modules/system/services/app-platform/` there. There is no Nix in this
repo, and there does not need to be.

944 series, 939 of them Reading. 300 came from the **Reading-Ob** Obsidian
vault on pod042; the other 617 from an Anime-Planet export (`export-manga-Jacine0520.json`) of an
older account, imported 2026-08-17. Both are snapshots — see below.

## Files

| file | what it is |
|---|---|
| `app.json` | what this asks the host for: a port, a URL, a secret, a state directory |
| `fonts/` | the webfonts, as woff2 — self-hosted, so a checkout looks like the deploy |
| `schema.sql` | tables, views, FTS5 index |
| `seed.json` | the origin snapshot: 300 vault series + 617 from Anime-Planet |
| `tags.json` | every title's setting and genre, applied once |
| `anime-planet.json` | publication status + type looked up on Anime-Planet, applied once |
| `verified.json` | the handful Anime-Planet got wrong, confirmed one at a time |
| `seed.py` | builds the database; **additive**, never overwrites your edits |
| `app.py` | HTTP server + JSON API + cover cache |
| `ui.html` | the UI |
| `import-vault.py` | hand-run, one-way: Obsidian vault → `seed.json` |

## The vault is gone from the loop

`seed.json` was produced once by `import-vault.py` reading
`~/Media/Books/Reading-Ob` on pod042. That is a **snapshot, not a link**:

- The vault is not a dependency of this service. It lives on a laptop; this runs
  on a server, and it works with that laptop shut.
- Edits made here **do not travel back** to the markdown notes.
- Edits made in Obsidian **do not arrive here** unless you re-run
  `import-vault.py`, and even then only as *new* series (see below).

So pick one. Keeping both is how you end up with two half-right shelves.
`import-vault.py` is deliberately read-only — there is no write path in the
file — so the direction of travel cannot be got wrong by accident.

## Seeding is additive

`seed.py` runs as `ExecStartPre` on every start, like the Elden Ring tracker's
seeder, but it does a different job.

There, `seed.json` is the game's reference checklist: the reference tables are
dropped and rebuilt every start, and only your ticks are preserved. Here
everything in `seed.json` — chapter, rating, status — *is* the mutable state the
app exists to edit. Rebuilding it on every start would hand back the reading you
did last week.

| seed entry | what happens |
|---|---|
| never imported before | inserted, and recorded in `seed_applied` |
| already imported | skipped, whatever became of it |
| not from `seed.json` at all | left completely alone |

**Why `seed_applied` exists.** The obvious version of an additive seeder asks
"is this title already in `series`?" — and it is wrong, because a title is not
stable. Rename a series in the app and the next restart sees its original title
missing and imports it a second time, so a duplicate quietly appears after a
reboot. Delete one on purpose and it comes back. Recording what was imported
instead of inferring it closes both holes; the key is the title *as it appears
in `seed.json`*, which never changes because that file is in the read-only
store.

Verified: rename one series and delete another, re-seed twice, and the count
stays at 299 with neither resurrected. A hand-made series that happens to share
a seeded title is adopted rather than duplicated.

`--force-import` re-applies `seed.json` over the rows it originally created.
The vocabularies are the one thing upserted every start, so fixing an ordering
or adding a status is an edit and a redeploy.

### The view and the index look after themselves

`CREATE VIEW IF NOT EXISTS` has no opinion about a view that already exists and
is wrong. So every time `schema.sql` grew a column, the *live* view kept its old
shape and the app started answering `No item with that key` out of
`row_to_series()` — which is what adding `season` did, and what adding `kind`
did before it, each patched afterwards by a one-off `once()` that existed only
because somebody remembered to write it.

Nobody has to remember now. `migrate()` ends by dropping `v_series`
unconditionally: a view is metadata, dropping it costs nothing, and the second
`executescript(schema)` in `main()` puts it straight back from the file that is
the authority on its shape. It goes *last* because the repairs above it call
`reindex()`, which reads the view.

The FTS index cannot be treated the same way — rebuilding it is 900-odd
`reindex()` calls — so it is checked rather than dropped. Its columns cannot be
altered in place either, so a mismatch between what it has and what `reindex()`
writes is a rebuild, and the heal loop at the end of `main()` refills it,
because after the drop no row is in it.

## What the database buys

The vault could only ever describe the present: one note per series, each
frontmatter key overwritten in place. Two things follow from owning a real
schema.

**History.** `reading_log` and `status_log` append on every progress and status
change, so the shelf can answer *what have I actually been reading lately* —
which the vault threw away every time it was answered. Watching a film writes a
row too, which makes the log double as the date you saw it. `/api/history`
serves it; there is no screen for it, see below.

**Integrity.** Status, publication and medium are closed vocabularies with
foreign keys rather than free text, which is how the vault ended up with one
note reading `Publication Status: Hold` where every other says Hiatus. Setting
and genre are a real many-to-many, so renaming a word is one `UPDATE` rather
than a rewrite of eleven files.

## Two trackers, and a door for each

| tracker | progress counts | types |
|---|---|---|
| Reading | a chapter, and an optional tome | Manhwa, Manhua, Manga, Web Novel, Indonesian Comic |
| Watching | a season and an episode, or nothing at all | Show, Anime, Movie, Animated Movie |

`kind` is no longer a filter and never appears as a menu. It used to be a strip
of chips above the shelves with an **All** in front of them, which meant every
session began by pressing past a view that mixed nine hundred manhwa into five
films, and every new title began with a question the shelf you were standing in
had already answered. Now the app opens on two cards, the tracker lives in the
URL hash — `#reading`, `#watching` — so a reload lands where you left off and
Back walks you out of a tracker rather than off the page, and the header, the
filters, the statistics and the add dialog all belong to one of them.

Everything else in the schema is tracker-agnostic and needed no change: status,
rating, setting, genre and both history tables mean the same thing about a book
and a season.

**Anime, Shows and Films were three kinds for one activity.** Nothing below
them differed: same shelves, same ratings, same tags, the same episode counter
spelled out twice. The split bought a filter answering a question nobody asks
and charged for it up front, by making "is this an anime film or a film" a
thing to settle before typing a title. What the thing *is* lives on `type`, one
level down, which is where a distinction belongs when it is a real one.

**Playing is gone.** One playthrough was ever filed under it, games have their
own tracker, and a third door onto a shelf of one is a door you press past every
time you open the app. `Elden Ring` and its history went with the kind — see
`two-trackers` in `seed.py`.

### A film is watched or it is not

Progress used to be a property of the kind, so a film in the Watching tracker
got an episode counter and read `1 ep` when it was done. That was the unit
leaking onto a type that does not have one.

`type.progress` fixes it at the level where the difference actually lives:

- `''` — you count, in the tracker's unit. Chapters of a manhwa, episodes of a
  Show or an Anime.
- `'once'` — you do not count. A Movie or an Animated Movie is watched or it is
  not, the card reads `WATCHED` / `UNWATCHED`, the sheet's stepper is a switch,
  and the `+` on the card is a tick that disappears once it is pressed.

Underneath it is still the `chapter` column holding 1 or nothing, so watching a
film still writes a dated row to `reading_log` — which makes the log double as
the date you saw it. Change a Movie to a Show in the sheet and the control
redraws into a counter without a save, because the type you are picking is the
thing that decides which control it should be.

### Tomes, and seasons

Two columns above `chapter`, and two rather than one because they answer
different questions.

`series.tome` is Reading-only and optional in the real sense: null means you are
not counting volumes, which is most of the shelf. It shows on the card as
`T 12` beside the chapter when it is set and not at all when it is not.

`series.season` is the opposite. A season is part of an episode's *address* —
"episode 12" of a show with four seasons does not identify anything — so a Show
or an Anime always has one, it defaults to 1, and the card reads `S 2 EP 10`.
Films have no season and are not given one.

It is printed only *beside an episode*. A show with nothing watched reads
`UNWATCHED`, same as a film: on that card the season is the default talking,
and printing it there covered up the one thing the card had to say.

Getting back to `UNWATCHED` is the `−` button. It goes below 1 to **nothing**,
not to zero — "episode 0" is a number you are on and there is no such episode,
so flooring it at 0 meant a show could be started and never unstarted without
reaching for the keyboard. The season floors at 1 instead, because it is never
absent.

Collapsing the two into one "the bigger number" column would have been cheaper
by a column and wrong for the same reason `status` and `pub` are two fields: a
volume is a way of buying a book and a season is a coordinate.

Neither is logged. `reading_log` is about chapters and episodes, and a second
unit in it would make *what have I been watching* a question with two answers.
The `+` on a card advances the episode; the season is changed in the sheet,
where changing it is a deliberate thing rather than a mis-tap.

The backfill for the shows that predate the column matches on the type's
**name**, not on `type.progress`. `migrate()` runs before `upsert_vocab()`, so
on a database that has only just been given the `progress` column every row
still reads `''` — keying off that filed a film under season 1.

### Nine broadcast formats became four words

Watching used to carry TV, Series, Miniseries, Film, Short, OVA, ONA, Special
and Documentary — a taxonomy of how a thing was distributed, not of what it is.
Two questions cover it: is it animated, and does it have episodes. So: **Show,
Anime, Movie, Animated Movie.**

`two-trackers` in `seed.py` renames rather than re-files, so `type_id` keeps
pointing where it did — `Series` *is* `Show` now and `Film` *is* `Movie`. The
rest are dropped only where nothing was filed under them, because retyping
somebody's library is not a rename.

### It opens on Current

Whether or not anything is on it. It used to fall back to All when Current was
empty, which on a Reading shelf of 974 rows with not one of them Current meant
every session opened on all 974 — the pile, not the thing you are in the middle
of. An empty Current is the honest answer to *what am I reading*, and it is a
shelf the review queue fills.

### Shelves are Current and Finished

They were **Reading** and **Read**, which named the medium rather than the
state. The ids did not change, so every status change already in `status_log`
still resolves.

### The progress column is still called `chapter`

Renaming it would mean rewriting the view, both history tables, every read and
write in `app.py` and the whole of `ui.html`, against a live database with
reading history hanging off it — a lot of blast radius for a word. What it
*means* is carried by `kind.unit` and `type.progress`, which is the part anyone
sees.

## Setting and Genre are fields, not tags

The vault's tags were a flat pile of 59, written by hand over years, in which
`Fantasy` (half the shelf), `Transmigrassion` (a typo, 109 series) and `Boxing`
(one series) were peers in one alphabetical menu. Eighteen of the 59 were used
twice or less, and six were the same tag spelled two ways.

They are now a closed vocabulary of 24 split across two questions, and the app
does not call them tags at all — it shows two named fields:

| field | the question | e.g. |
|---|---|---|
| **Setting** | where does it take place | Fantasy, Murim, Modern, Hunter Fantasy, Apocalypse, Academy, Video Game |
| **Genre** | what does it feel like | Action, Adventure, Romance, Horror, Chill |

Both are **picked, not typed**. The sheet shows every word in the vocabulary as
a pill you click, many-of, with a square corner where the single-choice rows
are round. There is no free-text box and no `datalist`, which is the point: a
list you choose from cannot grow a second spelling of a word it already has.

That removed a whole screen. The **Tags** view existed to show a tag cloud and
to merge two spellings of one word — `tag_key()`, the variant detector, the
merge panel and `POST /api/tags/merge` are all gone with it, because with a
closed vocabulary there is nothing left for them to find. The cloud they
replaced is two dropdowns in the filter drawer, ANDed, so choosing a setting
*and* a genre is one query rather than a choice between them.

They stay two columns end to end: `axis` on `tag`, two lists off `v_series`,
two arrays in the API, and two separate writes in `update_series()` — writing
Setting deletes only rows on the setting axis, because both live in one join
table and clearing it wholesale would take Genre with it.

There was a third axis for a while — **premise**, holding Transmigration,
Regression, System, Revenge and eleven more. It described the shelf accurately
and it is gone anyway, because it was not asked for. `tag-drop-premise` in
`seed.py` removes it; three series wore nothing else and are re-read from
`tags.json` so that losing an axis does not mean losing a series from the
filters.

**Wuxia, not Xianxia.** They were separate for one revision — Chinese immortal
cultivation against Korean martial arts — and are now one tag under the name
that gets said out loud. `Murim` stays separate.

`tags.json` holds the classification for every title. It was built from three
layers, in increasing authority: regex over the title — which works far better
here than it should, because this genre names its books after their own
synopsis — then the tags the vault already carried, then a hand-written table
for the ~380 whose titles give nothing away. Each axis is capped (2 settings,
3 genres), keeping the rarest, because eight true tags is not a classification,
it is the synopsis again.

It is applied **once**, as a recorded migration, not on every start: replacing a
series' words is destructive of anything set by hand, and a seeder that
re-applied it would undo your edits on the next reboot. That is the same reason
`seed_applied` exists — see below.

## Going through a shelf

989 titles arrived from the vault and most of them had never been looked at
since: 608 with no cover, 590 with no rating, and 672 of the 974 Reading rows
filed under Dropped. Opening 672 sheets is not a way to go through that, so
there are two ways that are, and they are two because there are two different
problems.

### The reviewed mark

`series.checked_at` — null until you have been through the row yourself, and
nothing else in the database can stand in for it.

Something else *almost* can, which is worth writing down because it is the
reason this is a column rather than a query. Having both a cover and a rating
correlates with having been sorted, and on Hold it does so perfectly: 153 rows
have both, 63 have neither, and **nothing at all** falls in between. Finished is
28 for 28. Only Dropped is messy, and even there 576 of 672 fall cleanly.

It was still the wrong thing to build on. That split is a fact about one import,
not a definition, and it stops being true the moment a pass starts filling
covers in — after which "has both" means "the queue touched it" rather than "I
decided about it". So it is recorded.

It is deliberately **not** `updated_at`. That column means the series changed;
this one means you looked. Marking two hundred rows reviewed in one gesture must
not reorder a shelf whose default sort is *recently touched*, so
`update_series()` holds `reviewed` out of that stamp — and out of the reindex,
since nothing about it is searchable.

Nothing was seeded. Every row starts unmarked.

### Select mode, for when you already know

The plate becomes a checkbox and one bar applies one decision to everything
ticked: shelf, publication status, add or remove a word, mark or unmark
reviewed. It composes with the filter drawer, so *filter to Hold* → **All
shown** → one menu is four taps for two hundred rows.

**Modifiers behave the way they do in every file list.** Ctrl or Cmd on a card
toggles it; Shift extends from the last card touched to this one. The range is
over the grid *as displayed*, so it follows the sort and the filters rather than
id order — the only reading of "everything between these two" that matches what
is on screen. Extending only ever adds: shift-clicking back over a range you
have already taken is not a way to un-take it.

A plain tap toggles too, because a phone has no modifier key and select mode has
to work with a thumb.

A modified click on an **ordinary** card picks it and turns select mode on,
rather than opening the sheet. Holding a modifier and clicking is how selecting
starts everywhere else, so it should not first require finding a button — and a
sheet opening over a selection you were trying to start is the wrong answer to
an unambiguous gesture.

Ticking does not re-render the grid. Rebuilding 672 cards to tick one is a
wasted frame and a scroll position thrown away on every tap, which on a shelf
you are working down is the difference between the mode being usable and being
a trap. A shift-range does redraw once, because it changes many cards at once.

Undo restores **every** field the action wrote, per row, grouped by the state
each row came from. Moving a shelf also marks the rows reviewed; an Undo that
put the shelf back while leaving them marked would quietly take them out of the
queue it had just put them into.

Setting and Genre cannot be written the way the other fields are. They are
many-of, so sending the same list to twenty series would *replace* what each
already wore — "tag these twenty Murim" would strip every other word off all
twenty. Hence `add` and `remove` on `/api/bulk`, resolved per series against
what that series already has and then handed to `update_series()` as an
ordinary axis write, so the per-tracker vocabulary and the reindex happen once,
in the one place that knows how.

### One at a time, for when you have to look

One series, nothing else on screen, **two buttons**. *Leave in Dropped* and
*Move to Hold*, or a drag, or ← and → at a desk.

The way in is a labelled bar above the grid saying how many are waiting. It was
an unlabelled square in a row of five other unlabelled squares in the toolbar,
which is a feature nobody finds — and didn't. The bar is not drawn once the
shelf is clear.

The card shows the series and, underneath it, **whatever that series is
missing**, so a cover or a rating or a publication status can be filled in on
the spot. Whichever button you press commits the blanks and the verdict in one
write. A field you have already answered is not a question and is not drawn.

This was briefly two modes with a switch between them — *re-file* and *fill in*
— which was one screen too clever. They are not two jobs, they are the same card
being looked at.

**There is no Skip.** A queue you can decline to answer is a queue that never
shortens, and *leave it where it is* is already the answer for anything you do
not want to move: it is a decision, and it records that you made one.

**Two buttons and no more.** The other three shelves were a row of pills under
the verdict, offered because the code could. On a Dropped shelf the question is
Dropped or Hold; the rest was noise under it, and anything that really belongs
in Finished can be opened on the shelf.

The queue is a snapshot taken on entry, built from whatever the shelf is
currently showing, filters included, and then it stops listening. A queue that
reshuffled itself every time a save changed a sort key would lose your place on
every card. Reviewed rows are held back, which is the whole purpose of the mark:
the counter goes down and stays down.

Cover art is fetched for the card on screen and **one** card ahead, no further.

`/api/images` fans out to six live catalogues per call; walking 608 blanks would
be 608 of those, and running them in bulk is how you get DuckDuckGo to stop
answering. A result that comes back empty is not cached either — a catalogue
being down for a second looks exactly like a title nobody has art for, and
remembering the first as though it were the second would leave the card saying
*nothing found* for the rest of the session with no way to ask again.

## Notes are gone

618 series carried a `notes` field: whatever `import-vault.py` could not model,
folded into free text so nothing in the vault was silently dropped on the way
across. Nothing ever read it back. The column is dropped, the textarea is out of
the sheet, and `notes` is out of the FTS index — which meant taking `v_series`
and `series_fts` down and rebuilding both, since SQLite will not drop a column
anything else is built over. `seed.py` reindexes all 945 rows on the start that
does it.

This is a real deletion and there is no undo. `import-vault.py` no longer emits
the field either; it counts the note bodies it left behind and says so.

## Vocabularies, and what each one is asking

Three closed sets, and the whole point is that they ask different questions.
They were easy to confuse when the menus were unlabelled, and the vault had in
fact confused two of them:

| field | the question | values |
|---|---|---|
| **status** — *Shelf* | where **you** are with it | Current, Later, Hold, Finished, Dropped |
| **pub** — *Publication status* | whether the **author** is still writing it | Ongoing, Hiatus, Completed, Cancelled |
| **type** — *Type* | what it is | per tracker: Manhwa, Manhua, Manga, Web Novel, Indonesian Comic — or Show, Anime, Movie, Animated Movie |

`Hold` used to appear in *both* status and pub, on the strength of one vault
note reading `Publication Status: Hold`. Hold is a shelf. That value is retired
and the note reads Hiatus; the migration is `pub-drop-hold` in `seed.py`.

Every filter dropdown carries its question above it rather than a placeholder
describing what it will accept — "Any publication" told you what the menu held
and never what it was for. **Order** comes first, being the one control that
changes the shelf rather than narrowing it.

The filters do not survive leaving the shelf. Going to Stats, or out to the two
doors, clears them and hides the control that opens them, because the
alternative was returning to a shelf quietly showing a third of itself with the
only evidence a lit icon inside a closed drawer.

## Ratings are 0–10

They were once −10 to 10, to admit a single series rated −10. That was a verdict
rather than a score; it is 0 now, the `CHECK` in `schema.sql` is `0..10`, and
both writers — `update_series()` in app.py, `clamp_rating()` in seed.py — refuse
a negative. Databases created before the change keep the wider `CHECK`, since
rebuilding a table to tighten a constraint is not worth the risk to the reading
history hanging off it, and nothing can write a negative through it anyway.

## Stats is per shelf, with no recommendations and no clock

The tab row scopes this view, not just the grid. A mean rating over nine hundred
manhwa and five films was a number about no shelf in particular — and so, it
turned out, was a mean over Dropped and Current at once. *Mean chapter* on Hold
is 47; on Dropped it is 27, which is the shape of the shelf saying what it is.
Watching counts episodes and, separately, films watched: a film contributes no
episodes, because counting each as one would put a number in the tally that
means something else.

**Mean chapter** divides by the titles that actually count chapters and have a
number — not by the shelf. Films have no chapters and 84 rows simply do not
record one; both would drag a mean toward zero while pretending to be zeroes.

The numbers are computed **in the browser**, and `/api/library` no longer sends
a `stats` block at all. That is not a performance trick, it is the only way to
answer the question: a server cannot scope to the selected tab without a round
trip per tab, and the whole library is already in the page. The full block over
the real 974 Reading rows takes 0.15 ms, measured, which is less than
serialising the answer would have cost.

`app.py --stats` keeps its own SQL version, and it is not a duplicate — it runs
against a database nobody has opened in a browser.


It used to end with two lists — *shelved and now complete*, *on hold and still
publishing* — computed by joining status against pub. Those were not statistics.
They were the page deciding what you should read next out of two fields that
were never asked that question. They are gone, and what replaced them is a
breakdown of the shelf by setting and genre, which is a fact about the library
rather than a nudge.

The *this week* / *this month* chapter counters are gone too. The reading log
they were computed from is still written on every chapter change — see below.

## No reading-history view, no Tags view, and no light mode

The first is a removal of a screen, not of a capability. `reading_log` is still
appended on every chapter change and `/api/history` still answers; there is
simply no tab for it. Putting the view back is a dock button and a
`renderHistory()`. Deleting the log to hide a tab would have been the expensive
half of a cheap decision — a note can only ever hold the number you are on now,
which is the whole reason this is a database.

The Tags view is the opposite: the capability went first and the screen followed.
It existed to merge two spellings of one word, and words are picked from a
closed list now. Its dock slot is **Trackers**, the way back out to the two
doors.

The theme is dark, full stop: no toggle, no `prefers-color-scheme`, no stored
preference.

## The palette is a neutral scale, and there is no accent

The first dark pass kept the old "paper" palette's warmth — `#14130F` ground,
`#EDE9DC` text — which is a warm near-white on a warm near-black, and reads as
cream rather than as white. The mustard on the Hold marker was only the most
obvious part of it.

It is now [Radix Colors](https://www.radix-ui.com/colors)' `gray` dark scale,
used as designed: a true neutral where R, G and B are equal at every step, with
each step having a documented job — 1 app background, 3 element background, 6
border, 11 low-contrast text, 12 high-contrast text. That numbering is why a
dark UI stops being guesswork about which grey goes where.

The greys are the ground, not the whole palette. Colour sits on top of them in
exactly two places, and both of them mean something.

**The accent is Radix `iris`**, at the steps it was designed for: 9 as a solid
fill (primary button, floating Add), 11 as text on dark (links, active nav), 3
as a subtle tinted background, 7 as a border and focus ring. A dark UI cannot
take a fully saturated hue — it optically vibrates against the ground and
struggles to clear 4.5:1, which is why the advice is uniformly to desaturate
20–30% for dark mode. Radix's dark scales are already built that way, which is
the reason for taking them rather than mixing by hand.

**The shelf markers are three hues and two greys.** Reading is green, Later
blue, Hold orange — the shelves where something is still true of the book.
Read and Dropped are finished states and are told apart by lightness, which
leaves colour to mean *this is live*. The mustard that was on Hold is now
orange at step 11: the same idea at a lightness that reads as orange on a dark
ground rather than as something spilled on it.

Red survives on exactly one control, the one that deletes a series, where it is
semantic rather than decorative.

Placeholder plates for the 605 series with no artwork keep a per-title hue so
they stay distinguishable, at 7% saturation, which reads as a shade rather than
as a colour scheme.

### The selected chip cannot be a solid light fill

It was, for one revision, and Reading's marker vanished into it — a near-white
dot on a near-white chip, on the one shelf you were most likely to be looking
at. That is not a value to nudge; it is the shape of the idea. Any solid light
fill can swallow some marker, and which marker is a property you cannot keep
hold of as the palette changes.

So a selected chip is the tinted-surface pattern instead: `--a3` ground,
`--a7` border, `--a11` label. Both states are dark, so all five markers clear
3:1 in both. Every text colour clears 4.5:1 on all three surface greys — that
is checked, not asserted; `--ink-3` moved off Radix step 9 because step 9 is a
*solid* step, not a text step, and at 3.7:1 it was failing the 11px labels it
was carrying.

## Radius

`--r-sm 4` · `--r-md 6` · `--r-lg 10` · `--r-xl 12`, and full-round for dots
and nothing else.

That is the scale every serious system converged on:
[Vercel's Geist](https://vercel.com/geist) caps functional UI at 12px and uses
6px as standard; [Linear](https://linear.app) uses exactly 6 for controls and
12 for containers; [GitHub's Primer](https://primer.style) sits lower still.
The pass before this one had 999px pills on every button and a 28px sheet lip,
which is a phone-app costume rather than a scale.

## The serif is for titles only

Literata sets book titles — on the cover plates, in the fallback art, and at
the top of the sheet — because that is a name and it is what an e-reader face
is good at. Everything that is interface is Public Sans. Serif section
headings on a tool make it look like a magazine.

## What Anime-Planet was asked, and what it can answer

The export carried a name, a status, a chapter and a rating, and nothing else —
so 617 series arrived with no publication status and 604 with no type.
`anime-planet.json` fills both from the source they came from: **617/617 have a
publication status and a type**. 372 Completed, 245 Ongoing; 262 Manhwa, 179
Manhua, 161 Manga, 15 Web Novel.

The lookup is AP's own search, which 302s straight to the entry on an exact
name, and these titles *are* AP's names. What the entry page gives:

- **Publication status**, from the year range in the entry bar: `2018 - ?` is
  running, `2018 - 2023` is finished. Some Chinese webcomics carry no year at
  all, or say `TBA`; the chapter count answers those, because AP writes
  `Ch: 759+` while a series runs and `Ch: 387` once it has stopped. Between the
  two, all 617 resolved.

  It can only ever produce **Ongoing** and **Completed** — Anime-Planet does not
  distinguish hiatus or cancellation from completion. Those two are judgements
  it does not make, and guessing them from a stalled year range would put a
  wrong word on a shelf rather than leave an honest blank.
- **Type**, from the tags: `Manhwa`, `Manhua` and `Light Novels` are tagged
  explicitly, and a Japanese manga carries no medium tag at all because on a
  manga database that is the default. So *no medium tag* means Manga. (`Based
  on a Light Novel` is a source tag and deliberately does not match.)

The backfill is a recorded migration like the others, and it only writes where
the field is **still empty**. Anything already on the shelf beats anything a
lookup says — the same rule the import ran under.

AP answers 429 at any real pace, so the scraper waits 2.5s between calls and
backs off on `Retry-After`.

### Hiatus and Cancelled are not obtainable in bulk

They were chased and they are not there. In order:

- **Anime-Planet** has no such concept. A full page was searched for every
  variant of the words; Hunter x Hunter, on indefinite hiatus for years, reads
  `1998 - ?`, identical to a weekly series.
- **MangaDex** has a `hiatus` status and got **2 of 2** spot-checks wrong — it
  called *My S-Class Hunters* and *Library of Heaven's Path* hiatus while both
  were publishing in 2026. The cause is licensing: 100 of the 245 candidates
  are missing or chapter-less there because they moved to Webtoon or Kakao, and
  a takedown looks exactly like a death.
- **MyAnimeList** has the right field and was down (504 on every call).
- **AniList** has `RELEASING / FINISHED / HIATUS / CANCELLED` and returned
  **zero hiatus and zero cancelled across all 245**. Of the 14 it called
  FINISHED, hand-checking found 3 right, 3 wrong and 1 unclear.

So the shelf carries Ongoing and Completed, which is what can be known, and
`verified.json` holds the ones checked by hand afterwards. It applies once and
only over a value that still matches what `anime-planet.json` wrote — a field
edited since is his, and beats any lookup.

## Picking cover art

`Find`, next to the artwork field in the edit sheet, opens a cover search
seeded with the series title — Playnite's shape, and the same job: you look at
a grid and click the one you want.

It asks **six catalogues at once**, in parallel, and merges them into one grid.

### Why it is six and not one

It was one: a DuckDuckGo image search, which needs no API key and is where the
vault's cover URLs came from originally. That source went away. Not gradually —
`i.js` began answering `403 Forbidden` to this address and kept answering it on
a fresh `vqd`, from both machines, an hour after the last request, on HTTP/1.1
and HTTP/2 alike. The HTML page that carries the token still returns 200, so
the picker got far enough to fail at the last step and put `HTTP Error 403:
Forbidden` under the search box.

Nothing here caused it and nothing here fixes it. DuckDuckGo's terms forbid
automated use and they have spent the year enforcing it; the `ddgs` library has
the same bug report filed against `images()` while `text()` keeps working.

So the picker stopped depending on any single source:

| source | how | notes |
|---|---|---|
| `anime-planet` | HTML, parsed | the biggest catalogue of the niche and the new, which is most of this shelf |
| `mangaupdates` | API | the breadth backstop — 25 hits where MangaDex and AniList each found 1 |
| `mangadex` | API | clean and mainstream, thin on the obscure |
| `anilist` | GraphQL | the highest-resolution art of the six |
| `kitsu` | JSON:API | covers both trackers |
| `duckduckgo` | `i.js` | last, and the only one that returns scan-site plates rather than official art |

Ordered as listed, and the order is the grid's order.

**A source that fails is skipped, not fatal.** That is the entire point of the
rewrite: with the fan-out, today's DuckDuckGo 403 produces five working
catalogues and a quiet note saying which one did not answer, instead of an
error box and no covers. Anything a source raises — blocked, rate-limited,
reshaped overnight — is caught per source.

### Anime-Planet is the one that can break

It is the only one of the six that is not an API. Cloudflare passes a plain
request with a browser User-Agent; there is no challenge and no token. An exact
title 302s to the series page, where the cover is the `og:image`; anything
fuzzier returns the card grid, and both shapes are handled. Full-size art is
the thumbnail URL with the `-190x273` taken back out — keeping the `?t=` stamp,
without which the CDN 403s the resized forms.

If they change their markup this stops working, silently, and no amount of care
here prevents it. It is in the list anyway, because it is the catalogue this
shelf was built out of.

### The query is the title, and only the title

The seed used to be the title plus a type word plus the literal word `cover`,
which was correct when the only source was a web image search. It is wrong for
five title-indexed catalogues: `One Coin Clear manhwa cover` matches nothing at
all in a title index. It also dragged in every unrelated picture with the word
*cover* on it, and most covers do not have "cover" in their filename anyway.

The box stays editable, and every alternative title recorded for the series
appears under it as a one-click query — see below.

### Paging is per source

Each source keeps its own place. The response carries the list of sources that
still had more to give; the client hands that list back with the next page, so
an exhausted catalogue is not asked again. When the list empties there is no
next page and **More** puts itself away.

That last part needed a bound. MangaUpdates matches on each word separately and
then reports `total_hits: 10000`, which is a sentinel rather than a count —
asked politely it will produce four hundred pages of things that share the word
*coin* with the title. Believing it meant More could never hide, which is the
one thing it was asked to do. No source is paged past four pages now: you are
looking for one picture, and if it is not in the first hundred a catalogue
offers, it is not in that catalogue.

### It ranks, it does not filter

The first version threw results away: under 200×280, or an aspect ratio outside
0.5–0.95, and it was gone. That is why the picker kept missing covers you could
find by typing the same title into DuckDuckGo yourself — **a bare title search
returned 96 results and showed you 19.** Every square thumbnail went, and a
square thumbnail is very often the right cover, cropped by whatever site was
displaying it.

Nothing is discarded now except images too small to be artwork at all — under
120px on their shorter side, which is an icon or a sprite. Everything else is
*sorted* into bands by shape, biggest first inside each:

| band | ratio | what it usually is |
|---|---|---|
| 0 | 0.60–0.75 | cover proportions |
| 1 | 0.45–0.60 | taller than a cover, still a poster |
| 2 | 0.75–1.05 | square, or nearly: a crop of the right art |
| 3 | under 0.45 | a strip |
| 4 | over 1.05 | landscape: a screenshot or a banner |

The old sort was the raw distance from 2:3, which put a 200px thumbnail that
happened to be exactly 0.667 above a 2000px cover that was 0.66.

This now orders DuckDuckGo's block and nothing else. The other five are
catalogues of cover art, where every result is already the right shape and
none of them says how big it is — so a tile is labelled with **which catalogue
it came from** instead of with its dimensions, which is the more useful fact
anyway. The size, when known, is in the tooltip along with the matched title.

### Bugs found along the way

**Paging skipped results.** The offset was `page * 100`, assumed rather than
read, and DuckDuckGo returns 95 or 97 or 80. It comes out of the response's own
`next` field now, and the grid de-duplicates by URL, because paging genuinely
repeats.

**The thumbnail proxy rejected a shard.** `/thumb` is locked by pattern so it
cannot be used as a relay, and the pattern required `.mm.` after an optional
`.explicit.` — matching `tse2.explicit.mm.bing.net`, a host DuckDuckGo does not
use. The one it does use is `tse2.explicit.bing.net`, so those thumbnails 404'd
and the grid's `onerror` handler silently deleted the tile.

**The picker's footer floated over the grid.** `.foot` is the shelf card's
footer — absolutely positioned over a cover, with a gradient behind it — and
the dialog borrowed the class name without meaning to borrow that. The note
saying how many results there were was being read off whatever thumbnail
happened to be behind it. It is a row in the column now, like the head it
mirrors.

**The alternative-names strip ignored `hidden`.** `display:flex` on an
id-qualified selector outranks the `display:none` a browser gives `[hidden]`,
so a series with no alternative names still got the empty strip and its border.

`/thumb` proxies the thumbnails rather than letting the browser load them for
two reasons: the tailnet reaches this over plain HTTP and a browser blocks
https images on an http page, and it keeps the picker from telling six
strangers what is being searched for from which address. Every host whose
pictures can appear in the grid is named in the allowlist, and the referer sent
with each is chosen from the host — Anime-Planet's CDN has no interest in
being told the request came from DuckDuckGo.

Picking fills the field; **Save** commits it, like every other field in the
sheet. From there the normal cache takes over and the image is kept forever.

## Alternative titles

A shelf of manhwa has a naming problem. The vault filed things under whichever
name the group that translated it used, which is variously the romanised
Korean, a literal translation of that, the official English release, and an
abbreviation nobody outside a Discord would recognise. `series.title` has to
stay one value — it is the natural key `seed.py` re-attaches on — so the other
names live in `series_alt`, as many per series as you care to type, in the
order you type them.

They are not decoration:

- **They are searchable.** Alternative titles ride in the FTS index's `title`
  column beside the real one, so typing the name you happen to remember finds
  the row.
- **They are queries.** The cover picker offers each one as a button. Which
  name a catalogue has filed the art under is not knowable in advance, so you
  try them by clicking — and for something niche that is the difference between
  finding a cover and not.

Edited under **Also known as** in the sheet, as plain text boxes rather than
chips-on-enter: these are long, they contain punctuation, and half of them are
romanisations you want to paste and then fix a letter of.

Blank rows are dropped on save. The server drops anything equal to the real
title, and anything that repeats another alternative in a different case, so
neither the index nor the picker ends up with the same name twice.

One subtlety worth recording, because it was wrong first: `GROUP_CONCAT`
written with an `ORDER BY` beside it in `v_series` orders nothing. The
aggregate has no `GROUP BY`, so there is one output row and the clause is
dropped, leaving the values in whatever order the scan produced — for a
`WITHOUT ROWID` table keyed on `(series_id, title)`, alphabetically. The names
came back sorted instead of in the order they were typed, and the rows in the
sheet rearranged themselves on save. Ordering the *subquery* and aggregating
its output is what actually holds.

## Cover artwork

The cover URLs came across from the vault as DuckDuckGo image-proxy links
pointing at a dozen hosts. Hotlinking 300 of them on every page load is slow,
leaks the shelf to whoever is on the other end, and breaks the day a host
disappears — so each is cached under `/var/lib/media-tracker/covers/`, keyed
by a hash of the URL. Change a series' cover and the key changes with it, so
there is no cache to bust.

Unlike `elden-ring-tracker`'s `icons/`, these are **not committed**: they are
artwork for whatever happens to be on this shelf, not a fixed reference set.

**180 of the 213 series with a cover resolve. 33 do not**, and are not worth
engineering around — those hosts are gone or have started refusing hotlinks.
Those cards draw a tinted plate with the title set on it, the tint derived from
the title so a book still looks like itself. The 87 series with no cover at all
get the same treatment.

Two cheap recoveries run before giving up: a `Referer` of `duckduckgo.com`
satisfies their proxy sometimes, and when the proxy returns 400 because its
`ipt` signature has expired, the real image URL is sitting in the `u=` query
parameter, so `unproxy()` fetches that instead. Expect the second to matter more
over time as signatures age out.

Failures are remembered for six hours so a dead host is not retried on every
page load. `media-tracker-covers.timer` warms the cache three minutes after
boot and weekly after, purely so the first page load is not the slow one.

## Where the data lives

`/var/lib/media-tracker/media.db` — a `StateDirectory`, so it survives
deploys and reboots. The covers beside it are a cache and cost one re-download
each.

Back it up with the export, which is keyed on title rather than row id and so
survives a rebuilt database:

```bash
curl -su tracker:PASSWORD 'http://personal-server:8778/api/export' > reading-backup.json
```

## Running it from a checkout

Paths fall back to beside the script, so a plain checkout works with no
arguments:

```bash
git clone https://github.com/neburion/media-tracker && cd media-tracker
python3 seed.py && python3 app.py --open   # 127.0.0.1:8778, db in this directory
python3 app.py --stats                     # print the shelf and exit
python3 app.py --warm-covers
```

Overrides: `MT_DB`, `MT_SEED`, `MT_SCHEMA`, `MT_UI`, `MT_FONTS`, `MT_CACHE`,
`MT_HOST`, `MT_PORT`, `MT_USERNAME`, `MT_PASSWORD`.

`media-tracker --stats` is also on `PATH` on the host.

## API

| method | path | body / query |
|---|---|---|
| GET | `/api/library` | both trackers in one array, plus vocabularies and history — no stats, see above |
| GET | `/api/search?q=…` | FTS5 prefix search over title, setting, genre, type |
| GET | `/api/images?q=…&p=…&kind=…&src=…` | six cover catalogues at once; `p` is the page, `kind` picks the tracker's sources, `src` is the comma-separated list of sources still worth asking |
| GET | `/api/history` | the last 200 progress changes |
| GET | `/api/export` | portable JSON keyed on title |
| POST | `/api/update` | `{id, fields}` — partial; returns which fields changed |
| POST | `/api/bump` | `{id, by, resume}` — one more chapter or episode, or marks a film watched; optionally un-shelves it |
| POST | `/api/bulk` | `{ids, fields, add, remove}` — one decision applied to many; `add`/`remove` are `{axis: [word…]}` and resolve per series |
| POST | `/api/create` | `{title, fields}` — `fields.kind` is the tracker, sent by the client because it is the door it is standing in |
| POST | `/api/delete` | `{id}` — cascades setting, genre and both logs |

A field whose value did not change is not written and does not appear in
`changed`, so the logs record real edits rather than every Save.

`/api/images` holds no per-search state on the server: which sources are still
live travels out in the response and back in the next request. Two browsers
cannot get in each other's way, and a restart mid-search costs nothing.

## Security

HTTP Basic Auth, on whenever a password is present — the systemd credential
`password` (the `media-tracker-password` sops secret in
`secrets/personal-server.yaml`) or `$MT_PASSWORD`. Without one the app refuses
to bind anything but loopback, so a misconfigured deploy fails to start rather
than putting a writable API on the network. The username is `tracker` and lives
in `app.json` as `MT_USERNAME`, since it is not a secret. Failed attempts are rate-limited to
20 per hour per client IP, read from `CF-Connecting-IP` so the tunnel does not
bucket the whole internet into one key.

A successful login also sets `mt_session`, a signed cookie good for 30 days and
re-issued whenever it drops under 21 days left, so the password is typed about
once a month instead of once per browser session — which on a phone was most
times the app was opened. The cookie is a signed expiry timestamp rather than a
session id, so there is no session table to keep and a service restart does not
log anyone out. The signing key is derived from the password: rotating the sops
secret invalidates every outstanding cookie, which is also the way to force a
logout everywhere. `Secure` is set only when the request arrived over HTTPS, so
the same cookie works on the plain-HTTP tailnet address.

Three layers gate `media.azuresalt.app`, and two of them are set by hand:

1. **Cloudflare Access policy** — dashboard only, *not* managed by cf-reconcile,
   so it can silently go missing.
2. **HTTP Basic Auth** in app.py, from the sops secret above.
3. **app.py refuses to bind a non-loopback address with no password**, so a
   credential failure is a restart loop and a 502 rather than an open service.

The `tailscale0`-scoped firewall rule stays as it is; cloudflared dials
127.0.0.1:8778 from inside the host and needs no rule of its own, so the LAN
still cannot reach the port.

**Set the Access policy.** This hostname deserves it more than
`eldenring.azuresalt.app` does: a wiped playthrough is re-seedable from
`seed.json`, whereas `POST /api/delete` drops a series and its chapter history
with no undo. The Basic Auth password is also only eight characters, which is
fine behind Access and thin without it.
