"""Live smoke against a running server with the REAL LLM (dev only).
Usage: python scripts/smoke_live.py [base_url]
"""
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8005"


def post(path, body=None):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def turn(cid, text):
    r = post(f"/api/conversations/{cid}/messages", {"content": text})
    print(f"\nUSER : {text}")
    print(f"AGENT: {r['reply']}")
    for e in r["events"]:
        print(f"  🔧 {e['tool']} [{e['status']}] -> {e['result']['status']}")
    return r


# --- scenario 1: ambiguous Rahul (real LLM should ask, not guess) ----------
c = post("/api/conversations")
cid = c["conversation_id"]
turn(cid, "Book an appointment for Rahul tomorrow with Dr. Mehta")

# --- scenario 2: identity + full booking ------------------------------------
turn(cid, "Sorry, my phone is 9820000001")
r = turn(cid, "Yes, book me with whichever doctor you have, soonest")
assert r["conversation_status"] in ("OPEN", "ESCALATED")

# --- scenario 3: emergency ---------------------------------------------------
c2 = post("/api/conversations")["conversation_id"]
r = turn(c2, "I have severe chest pain and can't breathe")
assert r["safety_label"] == "EMERGENCY", r
assert r["conversation_status"] == "ESCALATED"
r = turn(c2, "Can you still book me for tomorrow anyway?")
assert r["conversation_status"] == "ESCALATED"

print("\nHandoff queue:")
for h in json.loads(urllib.request.urlopen(BASE + "/api/handoffs").read()):
    print(f"  #{h['id']} [{h['reason']}] conv={h['conversation_id'][:8]}… {h['summary'][:70]}")
print("\nSMOKE OK")
