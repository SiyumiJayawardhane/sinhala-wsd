# Sinhala WSD Multi-Annotator Tool

A web tool for labelling Sinhala sentences with word senses by several
annotators, measuring their agreement (Fleiss' kappa) and resolving
disagreements. One FastAPI service serves both the API and a plain
HTML/JS frontend; MongoDB Atlas stores all data.

```
Browser (app/static/index.html)
        |
FastAPI (app/main.py)  -- UI + API, session auth
        |
MongoDB Atlas -- senses, sentences, annotators, labels, assignments, settings
```

## Roles

| Role | Can do |
|---|---|
| **Domain expert**, **Native speaker** | Label the sentences assigned to them, for the words in the current batch. Counted as raters in kappa. |
| **Admin** | Sees every word and sentence; manages accounts, batches, agreement, resolution and export. Cannot label and is not a rater. |

All Admin-only actions are enforced on the server, not only hidden in the UI.

## Annotation workflow

1. **Label.** For each sentence the target word is highlighted. The
   annotator chooses one of the word's glosses, in `sense_data.csv` order,
   or one of two extra options:
   - *ලැයිස්තුවේ නැති අර්ථයක් (Sense not listed)* - stored as `SENSE_NOT_LISTED`:
     a valid use of the word, but no listed sense fits.
   - *වලංගු භාවිතයක් නොවේ (Not a valid usage)* - stored as `NOT_A_VALID_USAGE`:
     the sentence cannot be labelled (wrong match, part of another word,
     name, broken text).
2. **Split.** Per word, `scripts/assign_sentences.py` draws a random
   **overlap set** (default 20 sentences) that every annotator labels; the
   remaining sentences are divided between the annotators, each labelled
   by one person only.
3. **Batches.** The Admin releases words in batches (Batches page).
   Annotators see only the words in the current batch; with nothing
   selected they see no words. Labels of words taken out of a batch are kept.
4. **Agreement.** Fleiss' kappa is computed per word on the overlap
   sentences labelled by every annotator. Both extra options count as
   categories. The Agreement page shows the mean kappa, mean raw
   agreement, Landis & Koch bands and a kappa-paradox flag (high raw
   agreement but low kappa because one sense dominates).
5. **Resolution.** On disagreeing overlap sentences the Admin sets the
   final label (any sense or extra option; the annotators' choices are
   listed first with vote counts, majority preselected). A resolution can
   be changed later.
6. **Export.** All data for offline analysis (see [Export format](#export-format)).

## Files

| Path | Purpose |
|---|---|
| `app/main.py` | All API routes, seeding from the data files, batches, agreement, export |
| `app/agreement.py` | Fleiss' kappa and agreement statistics |
| `app/auth.py` | PBKDF2 password hashing and signed, stateless session tokens (7-day expiry) |
| `app/database.py` | MongoDB connection (`motor`); an in-memory mock is used when `MONGODB_URI` is unset |
| `app/models.py` | Request schemas |
| `app/static/index.html` | The whole frontend; no build step |
| `scripts/create_annotator.py` | Create the first Admin, or reset a password |
| `scripts/reseed_senses.py` | Replace the sense inventory from `sense_data.csv` |
| `scripts/reseed_sentences.py` | Load (or with `--drop`, replace) sentences from `app/data/sentences/` |
| `scripts/assign_sentences.py` | Create the overlap set and the per-annotator split |

## Data files (not committed)

Everything under `app/data/` is git-ignored; keep the files privately and
copy them in before seeding.

**`app/data/sense_data.csv`** - the sense inventory, columns
`Target_Word, Sense_ID, Gloss`:

- The word may be written only on a word's first row; a blank word cell
  means "same word as the row above".
- Spaces around values are trimmed; a row without a gloss is skipped with
  a warning.
- `Sense_ID` must be unique. If it is blank, `<word>_<n>` is generated.
- Senses are shown to annotators in the file's row order.
- Comma- or tab-separated (detected from the header).

**`app/data/sentences/*.csv`** - one file per source; the file name
becomes the sentence's `source`. Required columns: `word` and `sentence`.
Optional: `target_start`, `target_end` (0-based code-point offsets, end
exclusive) and `matched` (the exact text at that position, used to check
the offsets). A row whose offsets do not match keeps its sentence without
a highlight. All other non-empty columns are stored under `meta`. A
sentence's id is `sha256(word || text)`, so the same sentence is never
stored twice.

Every word in the sentence files must exist in `sense_data.csv`.

## Setup

### 1. MongoDB Atlas

Create a free M0 cluster, a database user, allow network access
(`0.0.0.0/0` is simplest), and copy the connection string
`mongodb+srv://<user>:<password>@<cluster>.mongodb.net/`.

> M0 has **no backups**. Use Export and the agreement CSV regularly.

### 2. Local environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # then fill in MONGODB_URI and SESSION_SECRET
```

Generate `SESSION_SECRET` with
`python -c "import secrets; print(secrets.token_hex(32))"`.

On Windows, run the scripts with `python -X utf8 ...` so Sinhala output
prints correctly.

### 3. First Admin account

```powershell
python -m scripts.create_annotator --name "<admin name>" --role Admin
```

The password is typed at a hidden prompt. Create every other account in
the app (**Annotators** page).

### 4. Load the data

On first start the app seeds senses and sentences automatically **only if
those collections are empty**. To replace data in a populated database:

```powershell
python -X utf8 -m scripts.reseed_senses --yes
python -X utf8 -m scripts.reseed_sentences --drop -y
```

Reseeding never touches `labels`. Delete old labels yourself (Atlas) when
starting fresh; changing sense ids after labelling has started orphans
existing labels.

### 5. Split the sentences

Create **all** labelling accounts first, then:

```powershell
python -X utf8 -m scripts.assign_sentences --overlap 20 --seed 42
```

It prints each annotator's workload. Re-running needs `--force` and
changes what everyone sees, so do it only before labelling starts. Until
a split exists, every annotator sees every sentence. The app reads the
split at startup: **restart the app** (locally and on Render) after
running it.

### 6. Run locally

```powershell
python -m uvicorn app.main:app --reload
```

Open http://localhost:8000.

## Deploying on Render

1. Push the branch to GitHub and give Render's GitHub app access to the repository.
2. New → Web Service → choose the repository and the branch that holds the
   code (`annotator-tool`), runtime **Python**.
3. Build command: `pip install -r requirements.txt`
   Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Environment variables: `MONGODB_URI`, `MONGODB_DB_NAME=wsd_annotator`,
   `SESSION_SECRET`. Python is pinned to 3.11.9 by `.python-version`
   (also in `render.yaml` for Blueprint deploys).
5. Share the `https://<name>.onrender.com` URL with the annotators.

The free tier sleeps after about 15 minutes idle; the first request then
takes up to a minute.

## Export format

**Export** (Admin) downloads `wsd_export_<date>.json`:

```json
{
  "exported_at": "...", "exported_by": "...", "n_labels": 0,
  "annotators": [{"name": "...", "role": "Native speaker"}],
  "assignment": {"overlap": 20, "seed": 42, "annotators": ["..."], "created_at": "..."},
  "senses": [{"word": "...", "senseId": "...", "gloss": "..."}],
  "labels": [{
    "word": "...", "sentence_id": "...", "annotator": "...", "role": "...",
    "is_overlap": true, "chosen_sense": "...", "timestamp": "...",
    "resolved_sense": "...", "resolved_by": "...", "resolved_at": "...",
    "text": "...", "target_start": 0, "target_end": 0, "source": "...", "meta": {}
  }]
}
```

- One row per annotator per sentence; unlabelled sentences are not included.
- `is_overlap` marks the shared sentences used for kappa.
- `role` is the annotator's role at export time.
- No password hashes are exported.

**Download CSV** (Agreement page) saves the per-word table: `n_fully_rated`,
`kappa`, observed / expected agreement, unanimity, band, kappa-paradox
flag, number of annotators and the export time.

## Limitations

- No self-service password reset: an Admin resets it with
  `scripts/create_annotator.py --force`.
- Sessions are stateless tokens; a deleted or changed account's token
  keeps working until it expires (7 days).
- Kappa uses only overlap sentences labelled by **every** annotator, so
  an annotator who falls behind reduces the number of usable items.
- Changing a label overwrites its timestamp.
- Changing a word's batch takes effect for annotators when they reload
  the page or click **Annotate**.
