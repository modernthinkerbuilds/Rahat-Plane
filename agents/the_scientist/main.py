import os
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
DB_PATH = os.path.expanduser("~/developer/agency/rahat/vault/rahat.db")

def get_campaign_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # 1. Capture the weight row once
    cur.execute("SELECT value FROM raw_vitals WHERE metric_type = 'weight' ORDER BY timestamp DESC LIMIT 1")
    weight_row = cur.fetchone() 
    curr_weight = weight_row[0] if weight_row else 88.8 

    # 2. Capture the burn total
    today = datetime.now()
    last_sunday = (today - timedelta(days=(today.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0)
    
    cur.execute("SELECT SUM(value) FROM raw_vitals WHERE metric_type = 'active_calories' AND timestamp >= ?", (last_sunday.isoformat(),))
    burn_row = cur.fetchone()
    weekly_burn = burn_row[0] if burn_row and burn_row[0] else 0
    
    # 3. Capture the Campaign targets
    cur.execute("SELECT target_active_calories, daily_protein_target, daily_base_calories FROM weekly_campaigns ORDER BY week_start DESC LIMIT 1")
    campaign_row = cur.fetchone()
    targets = campaign_row if campaign_row else (5750, 200, 1950)

    conn.close()
    return {
        "weight": curr_weight,
        "weekly_burn": weekly_burn,
        "targets": targets,
        "days_remaining": 7 - ((today - last_sunday).days + 1),
        "today": today.strftime("%A, %B %d, 2026"),
        "now": today
    }
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Get current weight and stats
    cur.execute("SELECT value FROM raw_vitals WHERE metric_type = 'weight' ORDER BY timestamp DESC LIMIT 1")
    curr_weight = cur.fetchone()[0] if cur.fetchone() else 88.8 # Your July 2026 status

    # Calculate Sunday-to-Sunday burn
    today = datetime.now()
    last_sunday = (today - timedelta(days=(today.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0)
    
    cur.execute("SELECT SUM(value) FROM raw_vitals WHERE metric_type = 'active_calories' AND timestamp >= ?", (last_sunday.isoformat(),))
    weekly_burn = cur.fetchone()[0] or 0
    
    # Get Weekly Targets
    cur.execute("SELECT target_active_calories, daily_protein_target, daily_base_calories FROM weekly_campaigns ORDER BY week_start DESC LIMIT 1")
    targets = cur.fetchone() or (5750, 200, 1950)

    conn.close()
    return {
        "weight": curr_weight,
        "weekly_burn": weekly_burn,
        "targets": targets,
        "days_remaining": 7 - ((today - last_sunday).days + 1),
        "today": today.strftime("%A, %B %d, 2026")
    }

def run_audit():
    data = get_campaign_data()
    t_active, t_protein, t_base = data['targets']
    remaining_calories = t_active - data['weekly_burn']
    
    # Daily push required to hit the weekly target
    daily_required = remaining_calories / max(data['days_remaining'], 1)

    prompt = f"""
    ROLE: Aggressive Weight Loss Scientist (Venkat's Personal Coach).
    CONTEXT: 6'1" Male | Athlete | CrossFit & Running background.
    
    CORE PHILOSOPHY: 
    1. Training Efficiency Paradox: Venkat burns fewer calories than expected for high heart rates. 
    2. Focus on VOLUME and DURATION over pure intensity.
    3. Protein is non-negotiable for muscle preservation (Target: {t_protein}g).

    CURRENT CAMPAIGN STATUS ({data['today']}):
    - Current Weight: {data['weight']}kg (Goal: 84kg).
    - Weekly Active Calorie Target: {t_active} kcal.
    - Burned so far: {data['weekly_burn']} kcal.
    - Days remaining in week: {data['days_remaining']}.
    
    THE MATH:
    - Remaining weekly deficit needed: {remaining_calories} kcal.
    - Required Daily Active Burn: {daily_required:.0f} kcal/day.

    TASK:
    1. Update the daily active calorie target based on remaining days.
    2. If {data['weekly_burn']} is low, recommend "Volume Extenders" (walking, longer sessions) rather than just "working harder."
    3. Remind him of the 1,900-2,000 intake limit and {t_protein}g protein floor.
    4. Provide a 1-sentence "Daily Work Order."
    """

    print("🔬 Scientist is optimizing calorie trajectory...")
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    
    print("\n" + "="*40 + "\n" + response.text + "\n" + "="*40)

if __name__ == "__main__":
    run_audit()