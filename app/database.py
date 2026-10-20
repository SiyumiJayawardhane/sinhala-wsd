import os

MONGODB_URI = os.environ.get("MONGODB_URI", "")
DB_NAME = os.environ.get("MONGODB_DB_NAME", "wsd_annotator")

IS_MOCK_DB = not MONGODB_URI

if MONGODB_URI:
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGODB_URI)
else:
    from mongomock_motor import AsyncMongoMockClient
    client = AsyncMongoMockClient()

db = client[DB_NAME]

senses_collection = db["senses"]
sentences_collection = db["sentences"]
annotators_collection = db["annotators"]
labels_collection = db["labels"]
assignments_collection = db["assignments"]  # one doc per word: overlap + per-annotator split


async def ensure_indexes():
    await senses_collection.create_index("word")
    await sentences_collection.create_index([("word", 1), ("text", 1)], unique=True)
    await annotators_collection.create_index("name", unique=True)
    await labels_collection.create_index([("word", 1), ("sentence_id", 1)])
    await labels_collection.create_index([("word", 1), ("sentence_id", 1), ("annotator", 1)], unique=True)
