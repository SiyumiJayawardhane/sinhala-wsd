"""Load candidate sentences into MongoDB from the bundled files
(app/data/candidate_sentences.txt and app/data/sentences/*.txt).

The app only seeds sentences when the collection is empty, so this is
how you load a source you added later (e.g. a new nsina.txt).

Usage (from the project root, with .env present):

    python -m scripts.reseed_sentences           # add new, keep existing
    python -m scripts.reseed_sentences --drop     # wipe first, then load
    python -m scripts.reseed_sentences --drop -y   # no confirmation prompt

Idempotent: each sentence's _id is sha256(word + "||" + text), so a
plain run inserts only sentences not already present. `--drop` clears
the `sentences` collection first -- use it if you removed sentences from
a file and want them gone from the DB too.

Only the `sentences` collection is touched. Existing `labels` reference
sentence ids; dropping sentences that have already been labelled leaves
those labels dangling.
"""
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from app.database import sentences_collection, ensure_indexes, IS_MOCK_DB  # noqa: E402
from app.main import load_seed_sentence_files, bulk_import_sentences  # noqa: E402


async def main() -> int:
    if IS_MOCK_DB:
        print("MONGODB_URI is not set -- nothing to reseed. Set it in .env first.")
        return 1

    files = load_seed_sentence_files()
    if not files:
        print("No sentence files found. Add app/data/sentences/<source>.txt first.")
        return 1

    total_words = {w for _, parsed in files for w in parsed}
    total_sents = sum(len(v) for _, parsed in files for v in parsed.values())
    existing = await sentences_collection.count_documents({})
    print(f"Files: {', '.join(src for src, _ in files)}")
    print(f"Parsed: ~{total_sents} sentences across {len(total_words)} words.")
    print(f"Database currently holds {existing} sentence documents.")

    drop = "--drop" in sys.argv
    if drop and "-y" not in sys.argv and "--yes" not in sys.argv:
        reply = input("Drop the `sentences` collection first? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 1

    if drop:
        deleted = (await sentences_collection.delete_many({})).deleted_count
        print(f"Dropped {deleted} existing sentence documents.")

    await ensure_indexes()
    for source, parsed in files:
        if not parsed:
            print(f"  {source}: empty / unparseable, skipped")
            continue
        result = await bulk_import_sentences(parsed, source=source)
        print(f"  {source}: {result}")

    final = await sentences_collection.count_documents({})
    print(f"Done. `sentences` now holds {final} documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
