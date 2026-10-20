from dotenv import load_dotenv
load_dotenv()

import csv
import json
import hashlib
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import BulkWriteError

from .database import (
    senses_collection,
    sentences_collection,
    annotators_collection,
    labels_collection,
    assignments_collection,
    ensure_indexes,
    IS_MOCK_DB,
)
from .models import LoginRequest, CreateAnnotatorRequest, SubmitLabelRequest, ResolveRequest
from .agreement import build_agreement_matrix, agreement_stats
from .auth import hash_password, verify_password, make_token, verify_token

LABELLING_ROLES = {"Domain expert", "Native speaker"}  # count as Fleiss' Kappa raters
ALL_ROLES = LABELLING_ROLES | {"Admin"}


async def get_current_annotator(authorization: str = Header(default="")):
    """FastAPI dependency: require a valid ``Authorization: Bearer <token>``
    header, return its verified {"name", "role", "exp"}. This is the only
    source of truth for who is calling - nothing else (query params, body
    fields) is trusted for identity."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not logged in")
    payload = verify_token(authorization[len("Bearer "):])
    if not payload:
        raise HTTPException(status_code=401, detail="Session expired or invalid - please log in again")
    return payload


async def require_admin(current: dict = Depends(get_current_annotator)):
    if current.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return current

app = FastAPI(title="Sinhala WSD Multi-Annotator Tool")

BASE_DIR = Path(__file__).resolve().parent
OVERLAP_SIZE = 5        # fallback only, used until scripts/assign_sentences.py has run
SENSE_FLOOR = 10
SENSE_CEILING = 25

# Loaded from the `assignments` collection at startup (see load_assignment).
#   _OVERLAP_BY_WORD[word]            -> set of sentence_ids everyone labels
#   _PARTITION_BY_WORD[word][name]    -> set of sentence_ids only that annotator labels
_OVERLAP_BY_WORD: dict = {}
_PARTITION_BY_WORD: dict = {}
_ASSIGNMENT_READY = False

# short cache for the (whole-collection-scanning) agreement summary
_SUMMARY_CACHE: dict = {"at": 0.0, "data": None}
_SUMMARY_TTL = 30.0  # seconds


async def load_assignment():
    """(Re)load the per-word overlap + annotator split into memory."""
    global _ASSIGNMENT_READY
    _OVERLAP_BY_WORD.clear()
    _PARTITION_BY_WORD.clear()
    async for doc in assignments_collection.find({}):
        word = doc["_id"]
        _OVERLAP_BY_WORD[word] = set(doc.get("overlap_ids", []))
        _PARTITION_BY_WORD[word] = {
            name: set(ids) for name, ids in (doc.get("partitions") or {}).items()
        }
    _ASSIGNMENT_READY = bool(_OVERLAP_BY_WORD)
    return _ASSIGNMENT_READY


def _visible_ids(word: str, annotator: str) -> set:
    """Sentence ids a non-admin annotator may see for a word: the shared
    overlap plus their private slice of the remainder."""
    return _OVERLAP_BY_WORD.get(word, set()) | _PARTITION_BY_WORD.get(word, {}).get(annotator, set())


def sentence_id(word: str, text: str) -> str:
    return hashlib.sha256((word + "||" + text).encode("utf-8")).hexdigest()[:16]


def _norm_header(name: str) -> str:
    return (name or "").strip().lstrip("﻿").lower().replace(" ", "").replace("_", "")


def load_seed_senses():
    """Load the sense inventory to seed into MongoDB.

    Prefers ``data/sense_data.csv`` if present, otherwise falls back to
    ``data/sense_data.json``. The CSV must have a header row with columns
    for the word and the gloss; a sense-id column is optional and is
    generated as ``<word>_<n>`` (numbered per word) when absent or blank.

    Accepted header names (case/space/underscore-insensitive):
      word  -> "word", "target_word", "targetword", "headword", "lemma"
      gloss -> "gloss", "glosses", "definition", "meaning"
      senseId (optional) -> "sense_id", "senseid", "sense", "id"
    """
    csv_path = BASE_DIR / "data" / "sense_data.csv"
    if csv_path.exists():
        return _load_seed_senses_csv(csv_path)

    json_path = BASE_DIR / "data" / "sense_data.json"
    if json_path.exists():
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)

    return []


def _load_seed_senses_csv(csv_path: Path):
    word_keys = {"word", "targetword", "headword", "lemma"}
    gloss_keys = {"gloss", "glosses", "definition", "meaning"}
    sense_keys = {"senseid", "sense", "id"}

    senses = []
    per_word_counter = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        first_line = f.readline()
        f.seek(0)
        # Accept either comma- or tab-separated exports (Excel/Sheets often
        # save "CSV" as TSV). Pick whichever delimiter the header row uses.
        delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","
        reader = csv.DictReader(f, delimiter=delimiter)
        if not reader.fieldnames:
            return []
        colmap = {_norm_header(h): h for h in reader.fieldnames}
        word_col = next((colmap[k] for k in word_keys if k in colmap), None)
        gloss_col = next((colmap[k] for k in gloss_keys if k in colmap), None)
        sense_col = next((colmap[k] for k in sense_keys if k in colmap), None)
        if word_col is None or gloss_col is None:
            raise ValueError(
                f"{csv_path.name} needs a word column and a gloss column; "
                f"found headers {reader.fieldnames}"
            )

        seen_ids = set()
        for row in reader:
            word = (row.get(word_col) or "").strip()
            gloss = (row.get(gloss_col) or "").strip()
            if not word or not gloss:
                continue
            n = per_word_counter.get(word, 0) + 1
            per_word_counter[word] = n
            sense_id_val = (row.get(sense_col) or "").strip() if sense_col else ""
            if not sense_id_val:
                sense_id_val = f"{word}_{n}"
            if sense_id_val in seen_ids:
                # senseId must be unique - labels are keyed on it. Disambiguate
                # a repeated id rather than silently merging two senses.
                base = sense_id_val
                suffix = 2
                while f"{base}#{suffix}" in seen_ids:
                    suffix += 1
                sense_id_val = f"{base}#{suffix}"
                print(f"[seed] duplicate senseId {base!r} -> {sense_id_val!r}")
            seen_ids.add(sense_id_val)
            senses.append({"word": word, "senseId": sense_id_val, "gloss": gloss})

    return senses


@app.on_event("startup")
async def startup():
    await ensure_indexes()
    count = await senses_collection.count_documents({})
    if count == 0:
        senses = load_seed_senses()
        if senses:
            await senses_collection.insert_many(senses)

    if not IS_MOCK_DB:
        sentence_count = await sentences_collection.count_documents({})
        if sentence_count == 0:
            for source, parsed in load_seed_sentence_files():
                if parsed:
                    result = await bulk_import_sentences(parsed, source=source)
                    print(f"[seed] sentences from {source!r}: {result}")

    ready = await load_assignment()
    print(f"[assignment] loaded for {len(_OVERLAP_BY_WORD)} words"
          if ready else
          "[assignment] none found - run scripts/assign_sentences.py "
          "(annotators currently see every sentence)")


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/")
async def index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


# ---------- Auth / Annotators ----------
# There is no public self-registration. An Admin creates every account
# (POST /api/admin/create-annotator) with a name, role and initial
# password, and shares the password with that person directly. The very
# first Admin account is created outside the web app, on the server, via
# `python -m scripts.create_annotator --role Admin` (see that script).

@app.post("/api/login")
async def login(req: LoginRequest):
    doc = await annotators_collection.find_one({"name": req.name})
    if not doc or not verify_password(req.password, doc.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = make_token(doc["name"], doc["role"])
    return {"name": doc["name"], "role": doc["role"], "token": token}


@app.post("/api/admin/create-annotator")
async def create_annotator(req: CreateAnnotatorRequest, current: dict = Depends(require_admin)):
    if req.role not in ALL_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {sorted(ALL_ROLES)}")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if await annotators_collection.find_one({"name": req.name}):
        raise HTTPException(status_code=409, detail="That name is already registered")
    await annotators_collection.insert_one({
        "name": req.name,
        "role": req.role,
        "password_hash": hash_password(req.password),
        "created_at": datetime.utcnow(),
        "created_by": current["name"],
    })
    return {"status": "created", "name": req.name, "role": req.role}


@app.get("/api/annotators")
async def get_annotators(current: dict = Depends(require_admin)):
    cursor = annotators_collection.find({}, {"_id": 0, "password_hash": 0})
    return [doc async for doc in cursor]


# ---------- Senses ----------

@app.get("/api/words")
async def get_words(current: dict = Depends(get_current_annotator)):
    words = await senses_collection.distinct("word")
    return sorted(words)


@app.get("/api/senses/{word}")
async def get_senses(word: str, current: dict = Depends(get_current_annotator)):
    cursor = senses_collection.find({"word": word}, {"_id": 0})
    senses = [doc async for doc in cursor]
    if not senses:
        raise HTTPException(status_code=404, detail="Word not found")
    return senses


# ---------- Import candidate sentences ----------

def load_seed_sentence_files():
    """Return ``[(source, parsed_dict), ...]`` for every bundled candidate-
    sentence file, ready to hand to :func:`bulk_import_sentences`.

    Looked for, in order:
      * ``data/candidate_sentences.txt``  -> source ``"bundled_seed"``
      * ``data/sentences/*.txt``          -> source = file stem
        (e.g. ``data/sentences/culturax.txt`` -> source ``"culturax"``)

    Each file uses the extraction block format::

        s_word: <word>
        sentences: <s1>; <s2>; <s3>

        s_word: <next word>
        sentences: ...

    Adding a new source is just dropping another ``.txt`` in
    ``data/sentences/`` and reseeding.
    """
    out = []
    legacy = BASE_DIR / "data" / "candidate_sentences.txt"
    if legacy.exists():
        out.append(("bundled_seed", _read_sentence_blocks(legacy)))
    folder = BASE_DIR / "data" / "sentences"
    if folder.is_dir():
        for path in sorted(folder.glob("*.txt")):
            out.append((path.stem, _read_sentence_blocks(path)))
    return out


def _read_sentence_blocks(path: Path):
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return parse_candidate_blocks(text)


def parse_candidate_blocks(text: str):
    blocks = text.strip().split("\n\n")
    result = {}
    for block in blocks:
        lines = block.split("\n")
        word = None
        sentences: List[str] = []
        for line in lines:
            if line.startswith("s_word:"):
                word = line.replace("s_word:", "").strip()
            elif line.startswith("sentences:"):
                raw = line.replace("sentences:", "").strip()
                sentences = [s.strip() for s in raw.split("; ") if s.strip()]
        if word:
            result.setdefault(word, [])
            result[word].extend(sentences)
    return result


async def bulk_import_sentences(parsed: dict, source: str) -> dict:
    docs = []
    seen_ids = set()
    for word, sentences in parsed.items():
        for text in dict.fromkeys(sentences):  # de-dupe within this word/file
            sid = sentence_id(word, text)
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            docs.append({"_id": sid, "word": word, "text": text, "source": source})

    inserted = 0
    if docs:
        try:
            result = await sentences_collection.insert_many(docs, ordered=False)
            inserted = len(result.inserted_ids)
        except BulkWriteError as e:
            inserted = e.details.get("nInserted", 0)

    return {
        "words_seen": len(parsed),
        "sentences_inserted": inserted,
        "sentences_skipped_duplicate": len(docs) - inserted,
    }


# Candidate sentences are seeded from bundled files only (see
# load_seed_sentence_files / scripts/reseed_sentences.py). There is no
# upload endpoint.


# ---------- Task assignment ----------

@app.get("/api/next-task")
async def next_task(word: str, current: dict = Depends(get_current_annotator)):
    annotator = current["name"]
    all_sentences_cursor = sentences_collection.find({"word": word}).sort("_id", 1)
    all_sentences = [doc async for doc in all_sentences_cursor]
    if not all_sentences:
        return {"sentence": None, "overlap_remaining": 0, "extra_remaining": 0}

    if _ASSIGNMENT_READY:
        overlap_ids = _OVERLAP_BY_WORD.get(word, set())
        visible = _visible_ids(word, annotator)
        overlap = [s for s in all_sentences if s["_id"] in overlap_ids]
        extra = [s for s in all_sentences if s["_id"] in visible and s["_id"] not in overlap_ids]
    else:
        overlap = all_sentences[:OVERLAP_SIZE]
        extra = all_sentences[OVERLAP_SIZE:]

    labeled_cursor = labels_collection.find({"word": word, "annotator": annotator}, {"sentence_id": 1})
    labeled_ids = {doc["sentence_id"] async for doc in labeled_cursor}

    unlabeled_overlap = [s for s in overlap if s["_id"] not in labeled_ids]
    unlabeled_extra = [s for s in extra if s["_id"] not in labeled_ids]

    chosen = unlabeled_overlap[0] if unlabeled_overlap else (unlabeled_extra[0] if unlabeled_extra else None)

    return {
        "sentence": {"sentence_id": chosen["_id"], "text": chosen["text"]} if chosen else None,
        "is_overlap": chosen in overlap if chosen else False,
        "overlap_remaining": len(unlabeled_overlap),
        "extra_remaining": len(unlabeled_extra),
    }


@app.get("/api/sentences/{word}")
async def list_sentences(word: str, current: dict = Depends(get_current_annotator)):
    """Candidate sentences for a word.

    - A labelling annotator sees only their share: the shared overlap set
      plus their private slice of the remainder (from `assignments`).
    - An Admin sees every sentence, each tagged with how many annotators
      have labelled it (`label_count`) out of `label_total` - which is the
      full labelling roster for an overlap sentence, but just 1 for a
      private sentence (only one annotator is ever assigned to see it).
    - Before `scripts/assign_sentences.py` has been run, everyone sees
      every sentence and the first `OVERLAP_SIZE` are marked as overlap.
    """
    annotator = current["name"]
    role = current["role"]

    cursor = sentences_collection.find({"word": word}).sort("_id", 1)
    docs = [doc async for doc in cursor]

    labeled_cursor = labels_collection.find(
        {"word": word, "annotator": annotator}, {"sentence_id": 1, "chosen_sense": 1}
    )
    my_labels = {d["sentence_id"]: d.get("chosen_sense") async for d in labeled_cursor}

    overlap_ids = _OVERLAP_BY_WORD.get(word, set())

    if role == "Admin":
        counts_cur = labels_collection.aggregate([
            {"$match": {"word": word}},
            {"$group": {"_id": "$sentence_id", "n": {"$sum": 1}}},
        ])
        label_counts = {d["_id"]: d["n"] async for d in counts_cur}
        n_labelling = len(await _labelling_annotator_names())

        # Who a private (non-overlap) sentence is assigned to, if anyone -
        # each sentence belongs to at most one annotator's partition.
        owner_of = {}
        for name, ids in _PARTITION_BY_WORD.get(word, {}).items():
            for sid in ids:
                owner_of[sid] = name

        def _expected(sid):
            if not _ASSIGNMENT_READY:
                return n_labelling, None
            if sid in overlap_ids:
                return n_labelling, None
            owner = owner_of.get(sid)
            return (1, owner) if owner else (0, None)

        rows = []
        for i, d in enumerate(docs):
            sid = d["_id"]
            total, owner = _expected(sid)
            rows.append({
                "sentence_id": sid,
                "text": d["text"],
                "is_overlap": (sid in overlap_ids) if _ASSIGNMENT_READY else (i < OVERLAP_SIZE),
                "my_label": my_labels.get(sid),
                "label_count": label_counts.get(sid, 0),
                "label_total": total,
                "assigned_to": owner,   # only set for a private sentence
            })
        return rows

    if _ASSIGNMENT_READY:
        visible = _visible_ids(word, annotator)
        rows = [(i, d) for i, d in enumerate(docs) if d["_id"] in visible]
    else:
        rows = list(enumerate(docs))

    return [
        {
            "sentence_id": d["_id"],
            "text": d["text"],
            "is_overlap": (d["_id"] in overlap_ids) if _ASSIGNMENT_READY else (i < OVERLAP_SIZE),
            "my_label": my_labels.get(d["_id"]),
        }
        for i, d in rows
    ]


@app.get("/api/sentence-votes/{word}/{sentence_id}")
async def sentence_votes(word: str, sentence_id: str, current: dict = Depends(require_admin)):
    """Every annotator's raw label for one sentence, plus any resolution.
    Used by the Admin candidate view."""
    votes = {}
    resolved = None
    resolved_by = None
    async for lab in labels_collection.find({"word": word, "sentence_id": sentence_id}):
        votes[lab["annotator"]] = lab["chosen_sense"]
        if lab.get("resolved_sense"):
            resolved = lab["resolved_sense"]
            resolved_by = lab.get("resolved_by")
    return {
        "sentence_id": sentence_id,
        "votes": votes,
        "disagreement": len(set(votes.values())) > 1,
        "resolved_sense": resolved,
        "resolved_by": resolved_by,
    }


@app.post("/api/assignment/reload")
async def assignment_reload(current: dict = Depends(require_admin)):
    """Re-read the `assignments` collection into memory (after running
    scripts/assign_sentences.py) without restarting the server."""
    ready = await load_assignment()
    return {"ready": ready, "words": len(_OVERLAP_BY_WORD)}


@app.delete("/api/sentences/{sentence_id}")
async def delete_sentence(sentence_id: str, current: dict = Depends(require_admin)):
    """Remove one candidate sentence and any labels attached to it.

    The sentence _id is a global hash of (word, text), so it is enough on
    its own. This only touches the database - the bundled seed .txt files
    are not modified, so a later `reseed_sentences --drop` would bring the
    sentence back unless it is also removed from the source file.
    """
    result = await sentences_collection.delete_one({"_id": sentence_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Sentence not found")
    labels_removed = (
        await labels_collection.delete_many({"sentence_id": sentence_id})
    ).deleted_count
    return {"status": "deleted", "labels_removed": labels_removed}


# ---------- Labels ----------

@app.post("/api/submit-label")
async def submit_label(req: SubmitLabelRequest, current: dict = Depends(get_current_annotator)):
    await labels_collection.update_one(
        {"word": req.word, "sentence_id": req.sentence_id, "annotator": current["name"]},
        {"$set": {
            "chosen_sense": req.chosen_sense,
            "timestamp": datetime.utcnow(),
        }},
        upsert=True,
    )
    _SUMMARY_CACHE["data"] = None
    return {"status": "saved"}


@app.post("/api/resolve")
async def resolve(req: ResolveRequest, current: dict = Depends(require_admin)):
    await labels_collection.update_many(
        {"word": req.word, "sentence_id": req.sentence_id},
        {"$set": {
            "resolved_sense": req.final_sense,
            "resolved_by": current["name"],
            "resolved_note": req.note,
            "resolved_at": datetime.utcnow(),
        }},
    )
    _SUMMARY_CACHE["data"] = None
    return {"status": "resolved"}


# ---------- Progress ----------

@app.get("/api/progress/{word}")
async def progress(word: str, current: dict = Depends(get_current_annotator)):
    """Two different views, by role:

    - Admin: unchanged - per sense, how many labels have been formally
      *resolved* to it (dataset-wide sampling target, floor/ceiling).
    - Domain expert / Native speaker: their own labelling progress - how
      many of the sentences assigned to them they have personally
      labelled, and how those labels break down by sense. There is no
      floor/ceiling here; it is not a target, just a personal tally.
    """
    annotator = current["name"]
    role = current["role"]
    senses = [doc async for doc in senses_collection.find({"word": word}, {"_id": 0})]

    if role == "Admin":
        rows = []
        for s in senses:
            confirmed = await labels_collection.count_documents({
                "word": word,
                "resolved_sense": s["senseId"],
            })
            rows.append({"senseId": s["senseId"], "gloss": s["gloss"], "confirmed_count": confirmed,
                         "floor": SENSE_FLOOR, "ceiling": SENSE_CEILING})
        return {"mode": "resolved", "senses": rows}

    if _ASSIGNMENT_READY:
        total_assigned = len(_visible_ids(word, annotator))
    else:
        total_assigned = await sentences_collection.count_documents({"word": word})

    tally = {}
    total_labelled = 0
    async for lab in labels_collection.find(
        {"word": word, "annotator": annotator}, {"chosen_sense": 1}
    ):
        total_labelled += 1
        cs = lab.get("chosen_sense")
        if cs:
            tally[cs] = tally.get(cs, 0) + 1

    rows = [
        {"senseId": s["senseId"], "gloss": s["gloss"], "my_count": tally.get(s["senseId"], 0)}
        for s in senses
    ]
    return {
        "mode": "individual",
        "total_assigned": total_assigned,
        "total_labelled": total_labelled,
        "rejected": tally.get("REJECT", 0),
        "senses": rows,
    }


# ---------- Agreement dashboard ----------

async def _labelling_annotator_names() -> list:
    annotators = [doc async for doc in annotators_collection.find({}, {"_id": 0, "name": 1, "role": 1})]
    return [a["name"] for a in annotators if a.get("role") in LABELLING_ROLES]


async def _word_overlap_sentences(word: str) -> list:
    """This word's share of the shared overlap set (random, from
    assign_sentences.py); before that has run, the first OVERLAP_SIZE by id."""
    if _ASSIGNMENT_READY:
        ids = list(_OVERLAP_BY_WORD.get(word, set()))
        return [
            doc async for doc in
            sentences_collection.find({"word": word, "_id": {"$in": ids}}).sort("_id", 1)
        ]
    return [
        doc async for doc in
        sentences_collection.find({"word": word}).sort("_id", 1).limit(OVERLAP_SIZE)
    ]


async def _word_agreement(word: str, annotator_names: list):
    """Build the Fleiss matrix + stats for one word. Returns
    (overlap_sentences, votes_by_sentence, resolution_by_sentence, stats)."""
    senses = [doc async for doc in senses_collection.find({"word": word}, {"_id": 0})]
    categories = [s["senseId"] for s in senses] + ["REJECT"]

    overlap_sentences = await _word_overlap_sentences(word)
    overlap_ids = [s["_id"] for s in overlap_sentences]

    votes_by_sentence = {sid: {} for sid in overlap_ids}
    resolution_by_sentence = {sid: {"resolved_sense": None, "resolved_by": None} for sid in overlap_ids}
    if overlap_ids:
        async for lab in labels_collection.find(
            {"word": word, "sentence_id": {"$in": overlap_ids}}
        ):
            sid = lab["sentence_id"]
            votes_by_sentence[sid][lab["annotator"]] = lab["chosen_sense"]
            if lab.get("resolved_sense"):
                resolution_by_sentence[sid] = {
                    "resolved_sense": lab["resolved_sense"],
                    "resolved_by": lab.get("resolved_by"),
                }

    matrix = build_agreement_matrix(votes_by_sentence, annotator_names, categories)
    stats = agreement_stats(matrix)
    return overlap_sentences, votes_by_sentence, resolution_by_sentence, stats


@app.get("/api/agreement/{word}")
async def agreement(word: str, current: dict = Depends(require_admin)):
    annotator_names = await _labelling_annotator_names()
    overlap_sentences, votes_by_sentence, resolution_by_sentence, stats = \
        await _word_agreement(word, annotator_names)

    detail = []
    for s in overlap_sentences:
        votes = votes_by_sentence[s["_id"]]
        detail.append({
            "sentence_id": s["_id"],
            "text": s["text"],
            "votes": votes,
            "disagreement": len(set(votes.values())) > 1,
            "resolution": resolution_by_sentence[s["_id"]],
        })

    return {
        "word": word,
        "kappa": stats["kappa"],
        "observed_agreement": stats["observed"],   # raw % agreement - report next to kappa
        "expected_agreement": stats["expected"],   # chance level; high => kappa deflated
        "unanimity": stats["unanimity"],
        "n_fully_rated": stats["n_items"],
        "n_overlap_total": len(overlap_sentences),
        "n_annotators": len(annotator_names),
        "detail": detail,
    }


def _band(k):
    """Landis & Koch label for a kappa value."""
    if k is None:
        return "n/a"
    if k < 0.0:
        return "poor"
    if k < 0.20:
        return "slight"
    if k < 0.40:
        return "fair"
    if k < 0.60:
        return "moderate"
    if k < 0.80:
        return "substantial"
    return "almost perfect"


async def _overlap_ids_by_word() -> dict:
    """{word: [overlap sentence_ids]} for every word, in one pass."""
    if _ASSIGNMENT_READY:
        return {w: list(ids) for w, ids in _OVERLAP_BY_WORD.items()}
    out = {}
    async for d in sentences_collection.find({}, {"_id": 1, "word": 1}).sort([("word", 1), ("_id", 1)]):
        bucket = out.setdefault(d["word"], [])
        if len(bucket) < OVERLAP_SIZE:
            bucket.append(d["_id"])
    return out


@app.get("/api/agreement-summary")
async def agreement_summary(current: dict = Depends(require_admin)):
    """Per-word kappa + raw agreement for every word, plus the aggregate
    numbers you report in the paper: the unweighted mean kappa over the
    words where it is computable, the mean raw (observed) agreement, and
    the Landis & Koch band breakdown. Cached briefly - it scans all labels."""
    now = time.time()
    if _SUMMARY_CACHE["data"] is not None and now - _SUMMARY_CACHE["at"] < _SUMMARY_TTL:
        return _SUMMARY_CACHE["data"]

    annotator_names = await _labelling_annotator_names()

    senses_by_word = {}
    async for s in senses_collection.find({}, {"_id": 0, "word": 1, "senseId": 1}):
        senses_by_word.setdefault(s["word"], []).append(s["senseId"])

    overlap_by_word = await _overlap_ids_by_word()

    # one scan of the labels collection -> votes[(word, sid)][annotator] = sense
    overlap_pairs = {(w, sid) for w, ids in overlap_by_word.items() for sid in ids}
    votes = {}
    async for lab in labels_collection.find(
        {}, {"_id": 0, "word": 1, "sentence_id": 1, "annotator": 1, "chosen_sense": 1}
    ):
        key = (lab["word"], lab["sentence_id"])
        if key in overlap_pairs:
            votes.setdefault(key, {})[lab["annotator"]] = lab["chosen_sense"]

    rows = []
    for word in sorted(senses_by_word):
        categories = senses_by_word[word] + ["REJECT"]
        ids = overlap_by_word.get(word, [])
        votes_by_sentence = {sid: votes.get((word, sid), {}) for sid in ids}
        matrix = build_agreement_matrix(votes_by_sentence, annotator_names, categories)
        stats = agreement_stats(matrix)
        rows.append({
            "word": word,
            "kappa": stats["kappa"],
            "observed_agreement": stats["observed"],
            "expected_agreement": stats["expected"],
            "unanimity": stats["unanimity"],
            "n_fully_rated": stats["n_items"],
            "n_overlap_total": len(ids),
            "band": _band(stats["kappa"]),
            "paradox": (
                stats["kappa"] is not None and stats["observed"] is not None
                and stats["expected"] is not None
                and stats["expected"] >= 0.80
                and (stats["observed"] - stats["kappa"]) >= 0.30
            ),
        })

    computable = [r for r in rows if r["kappa"] is not None]
    with_obs = [r for r in rows if r["observed_agreement"] is not None]
    bands = {}
    for r in computable:
        bands[r["band"]] = bands.get(r["band"], 0) + 1

    def _mean(xs):
        return (sum(xs) / len(xs)) if xs else None

    data = {
        "n_annotators": len(annotator_names),
        "n_words": len(rows),
        "n_computable": len(computable),
        "mean_kappa": _mean([r["kappa"] for r in computable]),
        "mean_observed_agreement": _mean([r["observed_agreement"] for r in with_obs]),
        "bands": bands,
        "n_paradox_words": sum(1 for r in rows if r["paradox"]),
        "rows": rows,
    }
    _SUMMARY_CACHE.update(at=now, data=data)
    return data


# ---------- Export ----------

@app.get("/api/export")
async def export_all(current: dict = Depends(require_admin)):
    cursor = labels_collection.find({}, {"_id": 0})
    labels = [doc async for doc in cursor]
    for doc in labels:
        for k, v in list(doc.items()):
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
    return JSONResponse(content=labels)
