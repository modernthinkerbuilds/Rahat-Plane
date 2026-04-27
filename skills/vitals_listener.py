import hashlib
import os
import sqlite3
from fastapi import FastAPI, Request
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.path.expanduser("~/developer/agency/rahat/vault/rahat.db")

app = FastAPI(title="The Ear")

def generate_sample_id(metric, value, timestamp):
    # L9 Deduplication: Creates a unique fingerprint for every data point
    return hashlib.md5(f"{metric}{value}{timestamp}".encode()).hexdigest()

@app.post("/vitals")
async def receive_vitals(request: Request):
    payload = await request.json()
    samples = payload if isinstance(payload, list) else [payload]
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    count = 0
    try:
        for s in samples:
            m, v, ts = s.get('metric'), s.get('value'), s.get('timestamp')
            if not all([m, v, ts]): continue
            
            s_id = generate_sample_id(m, v, ts)
            
            # Use 'INSERT OR IGNORE' so duplicates from the iPhone don't break the Ledger
            cur.execute("""
                INSERT OR IGNORE INTO raw_vitals (sample_id, timestamp, metric_type, value)
                VALUES (?, ?, ?, ?)
            """, (s_id, ts, m, v))
            count += cur.rowcount
            
        conn.commit()
    finally:
        conn.close()
    
    return {"status": "success", "ingested": count}

if __name__ == "__main__":
    import uvicorn
    # '0.0.0.0' makes the Mac Mini accessible to your iPhone on the local WiFi
    uvicorn.run(app, host="0.0.0.0", port=8000)