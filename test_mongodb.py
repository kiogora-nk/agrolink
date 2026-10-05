import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "agrolink")

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not configured in .env")

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)

try:
    client.admin.command("ping")
    print("================================")
    print("MongoDB connection successful!")
    print("Database:", MONGODB_DB)
    print("================================")
except Exception as e:
    print("MongoDB connection failed:")
    print(e)
finally:
    client.close()
    