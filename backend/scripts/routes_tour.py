"""Live tour of every backend route, in contract order.

Prints the real response body for each endpoint so the REST surface can be
inspected without reading the README. Mutates state only by creating one
conversation and resolving the handoff its emergency turn generates.

    python scripts/routes_tour.py [base_url]        # default http://127.0.0.1:8005
"""
import json
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8005"


def call(method, path, payload=None):
    res = httpx.request(method, BASE + path, json=payload, timeout=90)
    try:
        body = res.json()
    except ValueError:
        body = {"raw": res.text[:120]}
    return res.status_code, body


def show(label, status, body, limit=1400):
    print(f"\n{'=' * 74}\n{status}  {label}")
    print(json.dumps(body, indent=2, ensure_ascii=False)[:limit])


# 1 — health
show("GET  /api/health", *call("GET", "/api/health"))

# 2 — open a call
status, body = call("POST", "/api/conversations")
show("POST /api/conversations", status, body)
cid = body["conversation_id"]

# 3 — read-only dashboard helper
show("GET  /api/slots/open", *call("GET", "/api/slots/open"))

# 4 — the turn that matters: the deterministic gate escalates before any
#     model call, so this reply is template-driven and provably safe
status, body = call("POST", f"/api/conversations/{cid}/messages",
                    {"content": "I have severe chest pain right now"})
show(f"POST /api/conversations/{cid}/messages  (emergency)", status, body)
handoff_id = (body.get("handoff") or {}).get("id")

# 5 — transcript + audit trail + inline tool events
show(f"GET  /api/conversations/{cid}", *call("GET", f"/api/conversations/{cid}"))

# 6 — recent conversations
show("GET  /api/conversations?limit=3", *call("GET", "/api/conversations?limit=3"))

# 7 — handoff queue (status=ALL to include resolved rows)
status, body = call("GET", "/api/handoffs?status=OPEN")
show("GET  /api/handoffs?status=OPEN", status, body[:2] if isinstance(body, list) else body)

# 8 — resolve, then resolve again: the second call must be a no-op
if handoff_id:
    show(f"POST /api/handoffs/{handoff_id}/resolve",
         *call("POST", f"/api/handoffs/{handoff_id}/resolve"))
    show(f"POST /api/handoffs/{handoff_id}/resolve  (again, idempotent)",
         *call("POST", f"/api/handoffs/{handoff_id}/resolve"))

# 9 — the error contract: unknown ids and invalid input stay machine-readable
for method, path, payload in [
    ("GET", "/api/conversations/does-not-exist", None),
    ("POST", "/api/handoffs/999999/resolve", None),
    ("POST", f"/api/conversations/{cid}/messages", {"content": ""}),
    ("GET", "/api/nope/not/a/route", None),
]:
    show(f"{method} {path}   <-- error case", *call(method, path, payload))
