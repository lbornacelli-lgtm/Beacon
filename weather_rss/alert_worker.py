import os
from pymongo import MongoClient
from datetime import datetime
from email_utils import send_email

mongo = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))
db = mongo.weather_rss
col = db.feed_status

def send(subject, body):
    send_email(subject, body)

for feed in col.find():
    if feed["status"] == "ERROR" and not feed.get("alerted"):
        send("Weather Feed FAILED", str(feed))
        col.update_one({"_id": feed["_id"]}, {"$set": {"alerted": True}})

    if feed["status"] == "OK" and feed.get("alerted"):
        send("Weather Feed RECOVERED", feed["filename"])
        col.update_one({"_id": feed["_id"]}, {"$unset": {"alerted": ""}})
