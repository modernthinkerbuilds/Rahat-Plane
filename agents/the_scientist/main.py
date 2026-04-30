import os
import sqlite3
import requests
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

# Configuration
API_KEY = os.getenv("GEMINI_API_KEY")
TOKEN = os.getenv("SCIENTIST_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DB_PATH = os.path.expanduser("~/developer/agency/rahat/vault/rahat.db")

# SDK Setup - Kills the 404 error
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def get_scientist_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        # Weight Lookback (Fixed Column: metric_type)
        cur.execute("SELECT value FROM raw_vitals WHERE metric_type = 'weight' AND timestamp >= date('now', '-30 days') ORDER BY timestamp DESC LIMIT 1")
        w_row = cur.fetchone()
        curr_weight = w_row[0] if w_row else 198.0

        # Burn Calculation (Sunday-Sunday)
        last_sunday = (datetime.now() - timedelta(days=(datetime.now().weekday() + 1) % 7)).replace(hour=0, minute=0, second=0)
        cur.execute("SELECT SUM(value) FROM raw_vitals WHERE metric_type = 'active_calories' AND timestamp >= ?", (last_sunday.isoformat(),))
        raw_burn = cur.fetchone()[0] or 0.0
        
        # Scaling correction for 10x Shortcut error
        weekly_burn = raw_burn / 10.0 if raw_burn > 10000 else raw_burn
        
        cur.execute("SELECT target_active_calories FROM weekly_campaigns ORDER BY week_start DESC LIMIT 1")
        target_active = cur.fetchone()[0] or 5750.0

        return {"weight": curr_weight, "weekly_burn": weekly_burn, "target": target_active}
    finally:
        conn.close()

def run_coaching_session(user_msg=None):
    data = get_scientist_data()
    remaining = max(data['target'] - data['weekly_burn'], 0)
    days_left = (6 - datetime.now().weekday()) % 7 + 1
    daily_avg = remaining / max(days_left, 1)

    # Forced grounding to prevent KG hallucinations
    prompt = f"""
    You are Venkat's Elite Sports Scientist.
    DATA: Weight {data['weight']} LBS (NOT KG), Weekly Burn: {data['weekly_burn']:.0f} kcal, Target: {data['target']:.0f} kcal.
    Remaining: {remaining:.0f} kcal over {days_left} days.
    Goal: 84kg (~185 lbs).
    USER: {user_msg if user_msg else 'Status update.'}
    INSTRUCTIONS: Be concise. Use LBS only. Provide a plan for the next {days_left} days.
    """

    try:
        response = model.generate_content(prompt)
        return response.text if response.candidates else "🔬 Lab blocked by safety filters."
    except Exception as e:
        return f"❌ Connection Error: {str(e)}"

def start_bot():
    last_id = 0
    print("🔬 Scientist Online...")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_id+1}&timeout=30"
            res = requests.get(url).json()
            for up in res.get("result", []):
                last_id = up["update_id"]
                msg_text = up.get("message", {}).get("text")
                if msg_text:
                    reply = run_coaching_session(msg_text)
                    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                                  json={"chat_id": CHAT_ID, "text": reply, "parse_mode": "Markdown"})
            time.sleep(1)
        except Exception as e:
            time.sleep(5)

if __name__ == "__main__":
    start_bot()