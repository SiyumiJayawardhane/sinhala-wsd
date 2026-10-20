# Sinhala WSD Multi-Annotator Tool

An independently-deployable annotation tool for multi-annotator Sinhala
Word Sense Disambiguation labeling. An Admin creates each annotator's
username/password account, annotators log in and label sentences against
a word's full sense inventory, and the tool computes Fleiss' Kappa on a
shared overlap set.

## Architecture

One FastAPI service serves both the API and the frontend (plain HTML/JS,
no separate frontend framework or deployment). MongoDB Atlas holds all
persistent data, decoupled from wherever the app itself is hosted.

```
FastAPI (app/main.py) -- serves UI + API
        |
MongoDB Atlas -- senses, sentences, annotators, labels
```

## What's included

- `app/main.py` - all API routes (login, admin/create-annotator, next-task,
  list/delete sentences, submit-label, progress, agreement, resolve,
  export) plus the startup seeding of senses and candidate sentences from
  bundled files. Every route except `/api/login` requires a valid session
  token; Admin-only routes are enforced server-side, not just hidden in
  the UI.
- `app/auth.py` - password hashing (PBKDF2-HMAC-SHA256) and signed,
  stateless session tokens (HMAC, keyed by `SESSION_SECRET`). Stdlib only.
- `app/database.py` - MongoDB connection (via `motor`, async driver)
- `app/models.py` - request/response schemas
- `app/agreement.py` - Fleiss' Kappa implementation, unit-tested
- `app/static/index.html` - the frontend: a login screen, an Admin-only
  "Register annotator" screen, the two-pane annotate view, and the
  Admin-only Agreement dashboard. No build step.
- `app/data/sense_data.csv` - the word / sense / gloss inventory as a
  spreadsheet-friendly table. This is the preferred seed file: if present
  it is used instead of the JSON. Details:
  - Header row required. Column names are matched case/space/underscore-
    insensitively: word = `word` / `Target_Word` / `headword` / `lemma`;
    gloss = `gloss` / `definition` / `meaning`; sense id (optional) =
    `senseId` / `Sense_ID` / `id`.
  - Comma- or tab-separated - the delimiter of the header row is detected
    automatically (Excel/Sheets often save "CSV" as TSV).
  - The sense-id column is optional; leave it blank or drop it and the app
    generates `<word>_1`, `<word>_2`, ... per word on seed.
  - A repeated sense id is disambiguated on seed (`id#2`, `id#3`, ...) and
    logged, since labels are keyed on sense id and must be unique.
- `app/data/sense_data.json` - the original 50-word, 265-sense inventory
  in JSON. Used as a fallback only when `sense_data.csv` is absent.
- `app/data/sentences/<source>.txt` - bundled candidate sentences, one
  file per extraction source (`culturax.txt`, `nsina.txt`, ...). The file
  stem becomes the `source` label on each sentence. Block format:
  `s_word:` line + `sentences:` line (semicolon-separated), blocks
  separated by a blank line. See `app/data/sentences/README.md`.
- `scripts/reseed_senses.py`, `scripts/reseed_sentences.py` - re-import
  the seed files into an already-populated database (startup only seeds
  when the collection is empty).
- `scripts/assign_sentences.py` - per word, draw N sentences at random as
  the shared overlap set (Fleiss' Kappa is computed on these) and split
  the rest round-robin across the labelling annotators (each sees only
  their slice). Writes the `assignments` collection. Admin-role annotators
  are excluded from the split and see every sentence with label counts.
  Run once words/sentences are final:

      python -m scripts.assign_sentences --overlap 20 --seed 42

  then restart the app or `POST /api/assignment/reload`. Until it is run,
  every annotator sees every sentence and the first 5 per word are the
  overlap set.

## Local setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env`, fill in your MongoDB connection string
   (see "Setting up MongoDB Atlas" below), and set `SESSION_SECRET` to a
   random string (`python -c "import secrets; print(secrets.token_hex(32))"`).
3. Run locally:
   ```
   export $(cat .env | xargs)
   uvicorn app.main:app --reload
   ```
4. Create the first account (must be Admin - there is no public
   self-registration, so nobody can log in until one exists):
   ```
   python -m scripts.create_annotator --name "yourname" --role Admin
   ```
   It prompts for a password on the terminal. Log in with it, then use the
   in-app "Annotators" screen (Admin only) to create everyone else.
5. Open `http://localhost:8000` in your browser and log in.

On first startup, the app automatically seeds both the sense inventory and
the candidate sentences into MongoDB if they aren't already there — no
manual import step. Senses come from `app/data/sense_data.csv` (preferred)
or `app/data/sense_data.json`; sentences come from every
`app/data/sentences/*.txt` file (and legacy
`app/data/candidate_sentences.txt` if present).

Seeding runs **only when the `senses` collection is empty**. To apply an
edited CSV to an already-seeded database, clear the `senses` collection
(Atlas UI, or drop it) and restart. Note that existing labels reference
`senseId` values, so renumbering senses of already-annotated words will
orphan those labels.

## Setting up MongoDB Atlas (one-time, ~10 minutes)

1. Go to https://www.mongodb.com/cloud/atlas and create a free account.
2. Create a free-tier (M0) cluster.
3. Under Database Access, create a database user with a username/password
   (this is separate from your Atlas login).
4. Under Network Access, add `0.0.0.0/0` to allow connections from anywhere
   (simplest for a small research project; can be tightened later).
5. Click "Connect" on your cluster, choose "Drivers", copy the connection
   string. It looks like:
   `mongodb+srv://<username>:<password>@<cluster>.mongodb.net/`
6. Put this string in `MONGODB_URI` in your `.env` file (or your hosting
   platform's environment variable settings when deploying).

## Deploying so annotators can access it independently

This is required if you don't personally know all your annotators or can't
walk each of them through local setup — the deployed version gives you one
shareable URL that works from any browser.

### Using Render (recommended, has a free tier)

1. Push this project to a GitHub repository.
2. Go to https://render.com, sign in with GitHub.
3. Click "New +" -> "Web Service", select this repository.
4. Render should auto-detect `render.yaml`. If not, set manually:
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Under Environment, add `MONGODB_URI` (paste your Atlas connection string)
   and `MONGODB_DB_NAME` (e.g. `wsd_annotator`); `SESSION_SECRET` is
   auto-generated by `render.yaml`.
6. Deploy. Render gives you a URL like `https://sinhala-wsd-annotator.onrender.com`
   -- this is the link to share with annotators.
7. Run `python -m scripts.create_annotator --name "..." --role Admin`
   locally, pointed at the same `MONGODB_URI`, to create the first Admin
   account (there's no way to do this through the deployed UI, since
   nobody can log in yet).

Note: Render's free tier sleeps after inactivity; the first request after
idle time takes a few extra seconds to wake up. This is normal, not a bug.

### Using Railway (alternative, similar free-tier setup)

Same idea: connect your GitHub repo, set the same two environment variables,
Railway builds and deploys automatically from the `requirements.txt` and
start command.

## How annotators use it

1. An Admin creates their account first (in-app "Annotators" screen: name,
   role, initial password) and shares the credentials with them directly.
   There is no self-service registration or password reset yet.
2. The annotator opens the shared URL and logs in with that username/password.
3. Pick a word from the left panel. The right side shows the annotation
   panel (target word, sentence, sense options) and a click-to-load list
   of every candidate sentence for that word.
4. Label the sentence against the full sense list (or "not a valid
   usage"). Submitting auto-advances to the next unlabelled sentence; the
   Prev / Next buttons move through sentences without labelling. The
   shared overlap set (badged "overlap") is what makes Fleiss' Kappa
   computable later.
5. Only Admins see the Agreement tab, the delete icon on candidate
   sentences, and Export -- all enforced server-side (not just hidden in
   the UI), so a Domain expert / Native speaker account genuinely cannot
   reach them even by calling the API directly.

Admin: use the "Annotators" nav item to create every other account (role +
an initial password you choose and share with them). Check the Agreement
tab for live Fleiss' Kappa and to resolve disagreements on overlap
sentences. Use Export any time to download all raw annotation data as JSON.

Candidate sentences are bundled with the deployment (see "What's
included" and `app/data/sentences/README.md`), not uploaded through the
UI -- there is no upload box. Add or refresh a source by committing a
`.txt` file and running `python -m scripts.reseed_sentences`.

## Design notes / limitations

- `GET /api/progress/{word}?annotator=` returns two different shapes by
  role: Admin gets `{mode:"resolved", senses:[...]}` - the bounded sampling
  view (10 floor / 25 ceiling per sense), counting only *resolved* labels.
  Domain expert / Native speaker get `{mode:"individual", total_assigned,
  total_labelled, rejected, senses:[{senseId, gloss, my_count}]}` - their
  own labelling progress within their own assigned sentence set, no
  floor/ceiling (it isn't a target, just a personal tally).
- Accounts are created by an Admin only; there is no self-service password
  reset. Resetting a forgotten password means an Admin re-running
  `python -m scripts.create_annotator --name "..." --role ... --force`.
- Sessions are stateless signed tokens with a 7-day expiry
  (`app/auth.py`); there is no server-side revocation list, so a deleted
  or role-changed account's old tokens keep working until they expire
  naturally.
- Fleiss' Kappa is computed per word over that word's shared overlap set
  (the random draw from `assign_sentences.py`; the first 5 by id until it
  is run), restricted to sentences rated by *every* labelling annotator.
  Admin-role users are excluded from the rater set. Registering more
  labelling annotators partway through drops earlier overlap sentences
  from the calculation until the newcomer catches up.
- The Agreement tab shows a **summary across every word** (unweighted mean
  kappa over the computable words, mean raw/observed agreement, Landis &
  Koch band counts) plus, per word, kappa **with** raw agreement and the
  chance level. When one sense dominates, chance agreement is high and
  kappa is deflated even at 90%+ raw agreement (the "kappa paradox") -
  such words are flagged and you should report the raw agreement.
  `GET /api/agreement-summary` returns the same numbers (cached ~30s).
- Sentence seeding reads each bundled `.txt` fully into memory and does
  one `insert_many` per source -- fine for files in the tens of MB
  (matches NSINA/CulturaX extraction output sizes), not designed for
  arbitrarily large files.

## Next steps after annotation

Use the `/api/export` endpoint (or the Export button) to pull all raw
annotation + resolution data as JSON. From there, build your full N-way
context-gloss training pairs the same way `data_collection_pipeline.ipynb`
does for Binuka's original dataset, but keyed off the `resolved_sense`
field as your gold label per sentence.
