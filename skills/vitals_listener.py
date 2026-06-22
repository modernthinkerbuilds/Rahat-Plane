import os, sqlite3
from pathlib import Path
from flask import Flask, request, jsonify

app = Flask(__name__)

# DB path resolution mirrors core/io.py:
#   1. RAHAT_DB_PATH env var wins if set (lets ops point at a custom DB),
#   2. otherwise resolve repo-relative to vault/rahat.db.
# This file lives at skills/vitals_listener.py so the repo root is parent.parent.
_REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("RAHAT_DB_PATH") or str(_REPO_ROOT / "vault" / "rahat.db")

@app.route('/vitals', methods=['POST'])
def ingest_vitals():
    data = request.json
    ts = data.get('timestamp')
    
    if not ts:
        print("⚠️ Received request with no timestamp.")
        return jsonify({"error": "Missing timestamp"}), 400

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        # 1. Process Weight (Global Override: Only one record ever)
        if 'weight' in data and data['weight']:
            cur.execute("DELETE FROM raw_vitals WHERE metric_type = 'weight'")
            cur.execute("INSERT INTO raw_vitals (metric_type, value, timestamp) VALUES ('weight', ?, ?)", 
                        (data['weight'], ts))
            print(f"⚖️ Weight Synced: {data['weight']} LBS")

        # 2. Process Calories (Daily Override: Clears only TODAY's entry before inserting)
        # We look for any key containing 'active_calo' to handle truncation in Shortcut UI
        cal_key = next((k for k in data if 'active_calo' in k.lower()), None)
        if cal_key and data[cal_key]:
            # Extract date part (YYYY-MM-DD)
            date_str = ts.replace('T', ' ').split(' ')[0]
            cur.execute("DELETE FROM raw_vitals WHERE metric_type = 'active_calories' AND timestamp LIKE ?", (f"{date_str}%",))
            cur.execute("INSERT INTO raw_vitals (metric_type, value, timestamp) VALUES ('active_calories', ?, ?)", 
                        (data[cal_key], ts))
            print(f"🔥 Calories Synced: {data[cal_key]} kcal")

        conn.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"❌ Database Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    # Listen on 0.0.0.0 to allow iPhone connection
    app.run(host='0.0.0.0', port=5000)