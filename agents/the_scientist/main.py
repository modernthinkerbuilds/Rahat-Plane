import os
import sqlite3
import requests
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
DB_PATH = os.path.expanduser("~/developer/agency/rahat/vault/rahat.db")
TOKEN = os.getenv("SCIENTIST_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def get_scientist_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # 1. Weight (185lbs / 84kg goal)
    cur.execute("SELECT value FROM raw_vitals WHERE metric_type = 'weight' ORDER BY timestamp DESC LIMIT 1")
    w_row = cur.fetchone()
    curr_weight = w_row[0] if w_row else 88.8 

    # 2. Weekly Burn (Sunday to Sunday)
    today = datetime.now()
    # Find the upcoming Sunday at 23:59:59
    days_until_sunday = (6 - today.weekday()) % 7
    campaign_end = (today + timedelta(days=days_until_sunday)).replace(hour=23, minute=59, second=59)
    
    # Fixed Math: Days left including today
    days_left = days_until_sunday + 1 
    
    # Calculate start of this week (Last Sunday 00:00:00)
    last_sunday = (today - timedelta(days=(today.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0)
    
    cur.execute("SELECT SUM(value) FROM raw_vitals WHERE metric_type = 'active_calories' AND timestamp >= ?", (last_sunday.isoformat(),))
    b_row = cur.fetchone()
    weekly_burn = b_row[0] if b_row and b_row[0] else 0
    
    cur.execute("SELECT target_active_calories FROM weekly_campaigns ORDER BY week_start DESC LIMIT 1")
    t_row = cur.fetchone()
    target_active = t_row[0] if t_row else 5750

    conn.close()
    return {
        "weight": curr_weight, 
        "weekly_burn": weekly_burn, 
        "target": target_active, 
        "now": today, 
        "days_left": days_left,
        "end_date": campaign_end.strftime("%A, %b %d")
    }

def get_chat_context(limit=6):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS chat_history (role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("SELECT role, content FROM chat_history ORDER BY timestamp DESC LIMIT ?", (limit,))
    rows = cur.fetchall()[::-1]
    conn.close()
    return "\n".join([f"{r[0]}: {r[1]}" for r in rows])

def run_coaching_session(user_msg=None):
    data = get_scientist_data()
    history = get_chat_context()
    remaining = max(data['target'] - data['weekly_burn'], 0)
    daily_push = remaining / data['days_left']

    # The Conversational Prompt
    prompt = f"""
    ROLE: Collaborative Sports Scientist & Weight Loss Coach.
    ATHLETE: Venkat (6'1"). GOAL: 84kg (185 lbs) by Oct 15.
    PHILOSOPHY: Volume > Intensity (Efficiency Paradox). 200g Protein Floor.
    
    GROUNDED CALENDAR:
    - Today: {data['now'].strftime('%A, %b %d, 2026')}
    - Campaign Ends: {data['end_date']} (Sunday Night)
    - DAYS REMAINING: {data['days_left']} (Including today)
    
    CURRENT STATS:
    - Weight: {data['weight']}kg
    - Weekly Burn: {data['weekly_burn']} / {data['target']} kcal
    - REMAINING GAP: {remaining} kcal
    - RECOMMENDED DAILY PUSH: {daily_push:.0f} kcal/day
    
    CHAT HISTORY:
    {history}

    VENKAT SAYS: "{user_msg if user_msg else 'Give me a status update.'}"

    INSTRUCTIONS:
    1. Be conversational. If Venkat says he can't work out, don't be a robot—adjust. Suggest he spreads the {remaining} kcal gap over the remaining days or adds a long walk tomorrow.
    2. If he wants to "go hard," emphasize DURATION (90+ mins) to beat his training efficiency.
    3. If he's tired, prioritize the 200g protein floor and recovery.
    4. Provide a clear, negotiated 'Work Order' at the end.
    """

    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    
    # Save the interaction
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if user_msg: cur.execute("INSERT INTO chat_history (role, content) VALUES ('Venkat', ?)", (user_msg,))
    cur.execute("INSERT INTO chat_history (role, content) VALUES ('Scientist', ?)", (response.text,))
    conn.commit()
    conn.close()
    
    return response.text

# --- Telegram Polling Loop ---
def start_bot():
    last_id = 0
    print("🔬 Scientist Bot is live and listening...")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_id+1}&timeout=30"
            res = requests.get(url).json()
            for up in res.get("result", []):
                last_id = up["update_id"]
                msg = up.get("message", {}).get("text")
                if msg:
                    reply = run_coaching_session(msg)
                    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                                 json={"chat_id": CHAT_ID, "text": reply, "parse_mode": "Markdown"})
            time.sleep(1)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    start_bot()