import sqlite3
from datetime import datetime
import os

# Absolute path for Ledger stability
DB_PATH = os.path.expanduser("~/developer/agency/rahat/vault/rahat.db")

def run_scientist_audit():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM vitality_telemetry WHERE metric_type = 'weight' ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        current_weight = row[0] if row else 85.0
    except:
        current_weight = 85.0

    target_weight = 80.0
    deadline = datetime(2026, 7, 1)
    days_left = (deadline - datetime.now()).days
    
    if days_left <= 0: return

    total_deficit = (current_weight - target_weight) * 7700
    daily_target = total_deficit / days_left

    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO work_orders (requester_id, executor_id, payload_json) VALUES ('Scientist', 'Miya', ?)", 
                 (f'{{"daily_target": {round(daily_target, 2)}}}',))
    conn.commit()
    conn.close()
    print(f"Audit Complete: {current_weight}kg. Target: {round(daily_target, 2)} kcal/day.")

if __name__ == "__main__":
    run_scientist_audit()