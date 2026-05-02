import os, sqlite3, requests, time, re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google import genai

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
TOKEN = os.getenv("SCIENTIST_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DB_PATH = os.path.expanduser("~/developer/agency/rahat/vault/rahat.db")

client = genai.Client(api_key=API_KEY)

def get_active_model():
    """Bypasses 404s by finding the specific Flash version currently active."""
    try:
        available = [m.name for m in client.models.list()]
        flash = [m for m in available if 'flash' in m.lower()]
        return sorted(flash)[-1] if flash else "gemini-1.5-flash"
    except: return "gemini-1.5-flash"

MODEL_ID = get_active_model()

def sync_weight(val):
    """Manual override from Telegram: Clears all and sets the new anchor."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM raw_vitals WHERE metric_type = 'weight'")
        cur.execute("INSERT INTO raw_vitals (metric_type, value, timestamp) VALUES ('weight', ?, ?)", 
                    (val, datetime.now().isoformat()))
        conn.commit()
    finally: conn.close()

def get_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        # Get the latest single weight anchor
        cur.execute("SELECT value FROM raw_vitals WHERE metric_type = 'weight' ORDER BY timestamp DESC LIMIT 1")
        w_res = cur.fetchone()
        w = w_res[0] if w_res else 198.0
        
        # SUM calories from last Sunday (Weekly Progress)
        last_sun = (datetime.now() - timedelta(days=(datetime.now().weekday() + 1) % 7)).replace(hour=0, minute=0, second=0).isoformat()
        cur.execute("SELECT SUM(value) FROM raw_vitals WHERE metric_type = 'active_calories' AND timestamp >= ?", (last_sun,))
        b = cur.fetchone()[0] or 0.0
        
        # Get target from campaign
        cur.execute("SELECT target_active_calories FROM weekly_campaigns ORDER BY week_start DESC LIMIT 1")
        t_res = cur.fetchone()
        t = t_res[0] if t_res else 5750.0
        
        return {"w": w, "b": b, "t": t}
    finally: conn.close()

def run_session(msg):
    # Check for "wt 195" or "weight 195"
    weight_match = re.search(r"(?:weight|wt):\s*(\d+\.?\d*)", msg.lower())
    if weight_match:
        val = float(weight_match.group(1))
        sync_weight(val)
        return f"✅ Weight anchored at {val} LBS. All historical noise purged."

    d = get_data()
    days = (6 - datetime.now().weekday()) % 7 + 1
    rem = max(d['t'] - d['b'], 0)
    
    prompt = (f"Athlete: Venkat. Weight: {d['w']} LBS. Burned this week: {d['b']:.0f}/{d['t']:.0f} kcal. "
              f"Remaining: {rem:.0f} over {days} days. Goal: 185 lbs. "
              f"User Message: {msg}. Instructions: Be a data-driven CrossFit coach. Use LBS only.")
    
    try:
        res = client.models.generate_content(model=MODEL_ID, contents=prompt)
        return res.text
    except Exception as e:
        return f"❌ Connection Error: {str(e)}"

def start():
    requests.get(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook")
    print(f"🔬 Scientist Live | Model: {MODEL_ID} | Tracking: Override Mode")
    last_id = 0
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_id+1}&timeout=10").json()
            for up in r.get("result", []):
                last_id = up["update_id"]
                txt = up.get("message", {}).get("text")
                if txt:
                    reply = run_session(txt)
                    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                                  json={"chat_id": CHAT_ID, "text": reply, "parse_mode": "Markdown"})
            time.sleep(1)
        except: time.sleep(5)

if __name__ == "__main__":
    start()