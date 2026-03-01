from pymongo import MongoClient
from bson import ObjectId
from config import MONGO_URI, DB_NAME, COLLECTION

_client = None

def get_collection():
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI)
    return _client[DB_NAME][COLLECTION]

def all_entries():
    col = get_collection()
    return list(col.find())

def get_entry(entry_id):
    col = get_collection()
    return col.find_one({"_id": ObjectId(entry_id)})

def insert_entries(docs):
    col = get_collection()
    result = col.insert_many(docs)
    return result.inserted_ids

def update_wav(entry_id, wav_path):
    col = get_collection()
    col.update_one({"_id": ObjectId(entry_id)}, {"$set": {"_wav_file": str(wav_path)}})

def delete_entry(entry_id):
    col = get_collection()
    col.delete_one({"_id": ObjectId(entry_id)})
