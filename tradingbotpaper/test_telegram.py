import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

print("TOKEN:", TOKEN)
print("CHAT_ID:", CHAT_ID)

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

r = requests.post(
    url,
    json={
        "chat_id": CHAT_ID,
        "text": "✅ TEST Telegram dal bot trading"
    }
)

print("Status code:", r.status_code)
print("Response:", r.text)