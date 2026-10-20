"""Create one annotator account directly in MongoDB.

There is no public self-registration in the app - an Admin creates every
account from the in-app "Register annotator" panel. This script is the
one-time bootstrap for the *first* Admin account (nobody can be logged in
to use that panel until one exists). After that, use the app for everyone
else; only reach for this script again to reset a password or create an
account without going through the UI.

Usage (from the project root, with .env present):

    python -m scripts.create_annotator --name "siyumi" --role Admin
    python -m scripts.create_annotator --name "Kasun" --role "Domain expert"
    python -m scripts.create_annotator --name "siyumi" --role Admin --force   # reset password

The password is entered interactively (hidden input) rather than as a
command-line argument, so it never ends up in shell history or process
listings.
"""
import argparse
import asyncio
import getpass
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from app.database import annotators_collection, IS_MOCK_DB  # noqa: E402
from app.auth import hash_password  # noqa: E402

VALID_ROLES = {"Domain expert", "Native speaker", "Admin"}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="username the annotator will log in with")
    ap.add_argument("--role", required=True, choices=sorted(VALID_ROLES))
    ap.add_argument("--force", action="store_true", help="overwrite an existing account with this name")
    args = ap.parse_args()

    if IS_MOCK_DB:
        print("MONGODB_URI is not set - nothing to create. Set it in .env first.")
        return 1

    existing = await annotators_collection.find_one({"name": args.name})
    if existing and not args.force:
        print(f"'{args.name}' already exists (role: {existing.get('role')}). "
              f"Re-run with --force to reset its password/role.")
        return 1

    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Passwords did not match.")
        return 1
    if len(pw1) < 6:
        print("Password must be at least 6 characters.")
        return 1

    update = {
        "name": args.name,
        "role": args.role,
        "password_hash": hash_password(pw1),
        "created_at": datetime.utcnow(),
        "created_by": "create_annotator.py",
    }
    if existing:
        await annotators_collection.update_one({"name": args.name}, {"$set": update})
        print(f"Updated '{args.name}' (role: {args.role}). They can log in with the new password now.")
    else:
        await annotators_collection.insert_one(update)
        print(f"Created '{args.name}' (role: {args.role}). They can log in now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
