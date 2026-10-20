"""Reload the sense inventory into MongoDB from app/data/sense_data.csv
(or sense_data.json if no CSV).

The app only seeds senses when the collection is empty, so an edited
seed file has no effect on an already-populated database. Run this to
drop and re-import the `senses` collection.

Usage (from the project root, with .env present):

    python -m scripts.reseed_senses          # asks before wiping
    python -m scripts.reseed_senses --yes    # no prompt

This only touches the `senses` collection. `sentences`, `annotators`
and `labels` are left alone -- but note that existing labels reference
senseId values, so labels whose senseId no longer exists after reseed
are effectively orphaned.
"""
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from app.database import senses_collection, ensure_indexes, IS_MOCK_DB  # noqa: E402
from app.main import load_seed_senses  # noqa: E402


async def main() -> int:
    if IS_MOCK_DB:
        print("MONGODB_URI is not set -- this would only reseed an in-memory "
              "mock DB and do nothing useful. Set it in .env first.")
        return 1

    senses = load_seed_senses()
    if not senses:
        print("No senses found in the seed file. Nothing to do.")
        return 1

    existing = await senses_collection.count_documents({})
    words = sorted({s["word"] for s in senses})
    print(f"Seed file: {len(senses)} senses across {len(words)} words.")
    print(f"Database currently holds {existing} sense documents.")

    if "--yes" not in sys.argv and "-y" not in sys.argv:
        reply = input("Drop the `senses` collection and re-import? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 1

    await senses_collection.delete_many({})
    await ensure_indexes()
    await senses_collection.insert_many(senses)
    final = await senses_collection.count_documents({})
    print(f"Done. `senses` now holds {final} documents.")
    print("Restart the app (or just refresh) to see the new word list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
