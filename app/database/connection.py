from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()

# Use environment variable or default to localhost
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
db = client["resume_ai"]  # Fixed database name

def get_db():
    return db
