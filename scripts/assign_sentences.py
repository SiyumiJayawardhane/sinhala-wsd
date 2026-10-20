"""Assign candidate sentences to annotators, per word.

For every word:
  * draw OVERLAP sentences at random  -> the shared overlap set
    (every annotator labels these; this is what Fleiss' Kappa is computed on)
  * split the remaining sentences round-robin across the annotators
    -> each annotator's private slice, seen by nobody else

The result is written to the `assignments` collection (one doc per word):

    { "_id": "<word>",
      "overlap_ids": [...],
      "partitions": { "<annotator>": [...], ... },
      "n_total": <int>, "n_overlap": <int>,
      "annotators": [...], "overlap": <int>, "seed": <int|null>,
      "created_at": <datetime>, "created_by": "assign_sentences.py" }

Admin-role annotators are NOT included in the split (they see everything).

Usage (from the project root, with .env present):

    python -m scripts.assign_sentences --overlap 20 --seed 42
    python -m scripts.assign_sentences --annotators alice bob carol dave --overlap 20
    python -m scripts.assign_sentences --overlap 20 --force        # overwrite existing

With no --annotators, every registered annotator whose role is not "Admin"
is used. --seed makes the draw reproducible. --force overwrites an existing
assignment (refused otherwise, and always warned if labels already exist).

After it runs, restart the app or POST /api/assignment/reload so the new
split takes effect.
"""
import argparse
import asyncio
import random
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from app.database import (  # noqa: E402
    sentences_collection,
    annotators_collection,
    labels_collection,
    assignments_collection,
    IS_MOCK_DB,
)

ADMIN_ROLE = "Admin"
THIN_WARN = 3  # warn about words whose unique-per-annotator share is below this


async def resolve_annotators(names):
    if names:
        docs = [a async for a in annotators_collection.find({"name": {"$in": names}})]
        found = {d["name"] for d in docs}
        missing = [n for n in names if n not in found]
        if missing:
            print(f"! not registered: {', '.join(missing)} (register them first)")
            return None
        admins = [d["name"] for d in docs if d.get("role") == ADMIN_ROLE]
        if admins:
            print(f"! these are Admin and cannot be assigned sentences: {', '.join(admins)}")
            return None
        return sorted(names)
    docs = [a async for a in annotators_collection.find({})]
    picked = sorted(d["name"] for d in docs if d.get("role") != ADMIN_ROLE)
    skipped = sorted(d["name"] for d in docs if d.get("role") == ADMIN_ROLE)
    if skipped:
        print(f"  (skipping Admin: {', '.join(skipped)})")
    return picked


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--annotators", nargs="+", metavar="NAME",
                    help="annotator names to split among (default: all non-Admin)")
    ap.add_argument("--overlap", type=int, default=20,
                    help="shared overlap sentences per word (default: 20)")
    ap.add_argument("--seed", type=int, default=None, help="random seed for a reproducible draw")
    ap.add_argument("--force", action="store_true", help="overwrite an existing assignment")
    args = ap.parse_args()

    if IS_MOCK_DB:
        print("MONGODB_URI is not set - nothing to assign. Set it in .env first.")
        return 1
    if args.overlap < 2:
        print("--overlap must be at least 2 for agreement to mean anything.")
        return 1

    annotators = await resolve_annotators(args.annotators)
    if not annotators:
        print("No annotators to assign to.")
        return 1

    existing = await assignments_collection.count_documents({})
    if existing and not args.force:
        print(f"An assignment already exists ({existing} words). Re-run with --force to replace it.")
        return 1

    n_labels = await labels_collection.count_documents({})
    if n_labels:
        print(f"! WARNING: {n_labels} labels already exist. Re-assigning changes what each "
              f"annotator sees; only do this if you know labelling has not really started.")
        if not args.force:
            print("  Re-run with --force to proceed anyway.")
            return 1

    rng = random.Random(args.seed)

    words = sorted(await sentences_collection.distinct("word"))
    if not words:
        print("No sentences in the database. Import them first.")
        return 1

    print(f"Annotators ({len(annotators)}): {', '.join(annotators)}")
    print(f"Overlap per word: {args.overlap}   seed: {args.seed}   words: {len(words)}")

    docs = []
    per_annotator_total = {a: 0 for a in annotators}
    per_annotator_overlap_total = 0
    thin_words = []
    all_overlap_words = []

    for word in words:
        ids = [d["_id"] async for d in sentences_collection.find({"word": word}, {"_id": 1})]
        rng.shuffle(ids)

        k = min(args.overlap, len(ids))
        overlap_ids = ids[:k]
        remainder = ids[k:]
        per_annotator_overlap_total += k
        if not remainder:
            all_overlap_words.append((word, len(ids)))

        partitions = {a: [] for a in annotators}
        for i, sid in enumerate(remainder):
            partitions[annotators[i % len(annotators)]].append(sid)
        for a, lst in partitions.items():
            per_annotator_total[a] += len(lst) + k
        smallest = min((len(lst) for lst in partitions.values()), default=0)
        if remainder and smallest < THIN_WARN:
            thin_words.append((word, len(ids), smallest))

        docs.append({
            "_id": word,
            "overlap_ids": overlap_ids,
            "partitions": partitions,
            "n_total": len(ids),
            "n_overlap": k,
            "annotators": annotators,
            "overlap": args.overlap,
            "seed": args.seed,
            "created_at": datetime.utcnow(),
            "created_by": "assign_sentences.py",
        })

    await assignments_collection.delete_many({})
    await assignments_collection.insert_many(docs)

    total_sentences = sum(d["n_total"] for d in docs)
    print()
    print(f"Wrote assignment for {len(docs)} words ({total_sentences} sentences).")
    print(f"Shared overlap total: {per_annotator_overlap_total} sentence-slots "
          f"(each annotator labels all of them).")
    print("Per-annotator workload (overlap + private):")
    for a in annotators:
        print(f"  {a:20} {per_annotator_total[a]}")

    if all_overlap_words:
        print(f"\n{len(all_overlap_words)} word(s) have <= {args.overlap} sentences, so ALL of "
              f"them are overlap and no private sentences exist for them:")
        for w, n in all_overlap_words[:20]:
            print(f"  {w}  ({n})")
        if len(all_overlap_words) > 20:
            print(f"  ... and {len(all_overlap_words) - 20} more")

    if thin_words:
        print(f"\n{len(thin_words)} word(s) give some annotator fewer than {THIN_WARN} private "
              f"sentences (their per-word coverage is thin):")
        for w, n, s in thin_words[:20]:
            print(f"  {w}  (total {n}, smallest slice {s})")
        if len(thin_words) > 20:
            print(f"  ... and {len(thin_words) - 20} more")

    print("\nDone. Restart the app or POST /api/assignment/reload to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
