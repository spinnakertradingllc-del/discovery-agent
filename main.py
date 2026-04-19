import os
import json
import requests
from datetime import datetime
from flask import Flask, request, Response, jsonify

app = Flask(__name__)

# ── Credentials from environment variables ────────────────────────────────────
GEMINI_API_KEY       = os.environ.get("GEMINI_API_KEY", "")
TELNYX_API_KEY       = os.environ.get("TELNYX_API_KEY", "")
TELNYX_FROM_NUMBER   = os.environ.get("TELNYX_FROM_NUMBER", "")
TELNYX_CONNECTION_ID = os.environ.get("TELNYX_CONNECTION_ID", "")
WEBHOOK_BASE_URL     = os.environ.get("WEBHOOK_BASE_URL", "").rstrip("/")

# ── In-memory stores ──────────────────────────────────────────────────────────
call_sessions: dict = {}
leads_log:     list = []

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""You are Alex, a professional B2B sales agent for CleanPro Chemicals.
We supply industrial cleaning chemicals for food production machinery and dairy factories.

CALL SEQUENCE:
1. GREET warmly. Confirm you reached the right person.
2. QUALIFY: operation type (dairy/food production/beverage), current supplier, monthly volume.
3. PITCH CleanPro products:
   - CIP alkaline & acid solutions (Clean-In-Place)
   - Caustic foam cleaners for external surfaces
   - Acid descalers for mineral/scale removal
   - Food-grade no-rinse sanitizers
   - Enzyme degreasers for conveyors & machinery
   Highlight: food-grade certified, cost-effective, full technical support.
4. CAPTURE: name, company, best email address.
5. BOOK: follow-up call OR free product sample delivery.

RULES:
- MAX 2 short sentences per reply — live phone call.
- Sound natural and human. Never robotic.
- Answer chemistry questions confidently.
- Never quote prices — say "I will send you our current price list".
- If prospect declines → thank them warmly and end gracefully.
- Today: {datetime.now().strftime("%B %d, %Y")}"""


# ── Gemini AI ─────────────────────────────────────────────────────────────────
def get_ai_response(call_id: str, user_input: str) -> str:
    if call_id not in call_sessions:
        call_sessions[call_id] = []

    call_sessions[call_id].append({"role": "user", "parts": [{"text": user_input}]})

    # Build contents array with system instruction prepended
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={
                "system_instruction": {
                    "parts": [{"text": SYSTEM_PROMPT}]
                },
                "contents": call_sessions[call_id],
                "generationConfig": {
                    "maxOutputTokens": 120,
                    "temperature": 0.7
                }
            },
            timeout=8
        )
        resp.raise_for_status()
        ai_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"[AI ERROR] {e}")
        ai_text = "I'm sorry, I had a brief technical issue. Just one moment please."

    call_sessions[call_id].append({"role": "model", "parts": [{"text": ai_text}]})
    return ai_text


# ── TeXML builder ─────────────────────────────────────────────────────────────
def texml(text: str, gather: bool = True, call_id: str = "") -> Response:
    safe = (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))

    gather_url = f"{WEBHOOK_BASE_URL}/gather?call_id={call_id}"

    if gather:
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Gather input="speech" timeout="4" speechTimeout="auto"
          action="{gather_url}" method="POST" language="en-US">
    <Say voice="en-US-Neural2-F">{safe}</Say>
  </Gather>
  <Redirect method="POST">{gather_url}&amp;reprompt=1</Redirect>
</Response>"""
    else:
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="en-US-Neural2-F">{safe}</Say>
  <Hangup/>
</Response>"""

    return Response(body, mimetype="application/xml")


# ── Lead logger ───────────────────────────────────────────────────────────────
def log_lead(call_id: str, outcome: str) -> None:
    leads_log.append({
        "call_id":    call_id,
        "timestamp":  datetime.now().isoformat(),
        "outcome":    outcome,
        "transcript": call_sessions.get(call_id, [])
    })
    call_sessions.pop(call_id, None)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    data       = request.get_json(silent=True) or {}
    event_type = data.get("data", {}).get("event_type", "")
    call_id    = data.get("data", {}).get("payload", {}).get("call_control_id", "unknown")

    print(f"[WEBHOOK] {event_type} | {call_id}")

    if event_type == "call.initiated":
        return jsonify({"status": "ok"})

    if event_type == "call.answered":
        greeting = get_ai_response(call_id, "Call connected. Deliver your opening greeting.")
        return texml(greeting, gather=True, call_id=call_id)

    if event_type in ("call.hangup", "call.machine.detection.ended"):
        log_lead(call_id, "hung_up")

    return jsonify({"status": "ok"})


@app.route("/gather", methods=["POST"])
def gather():
    call_id  = request.args.get("call_id", "unknown")
    reprompt = request.args.get("reprompt", "0")

    json_body = request.get_json(silent=True) or {}
    speech = (
        request.form.get("SpeechResult")
        or request.form.get("speech_result")
        or json_body.get("SpeechResult")
        or json_body.get("speech_result")
        or ""
    ).strip()

    print(f"[GATHER] call_id={call_id} reprompt={reprompt} speech='{speech}'")

    if not speech:
        if reprompt == "1":
            log_lead(call_id, "no_response")
            return texml("I'll let you go for now. Feel free to reach us anytime. Goodbye!", gather=False)
        return texml("Sorry, I didn't quite catch that — could you repeat?", gather=True, call_id=call_id)

    end_signals = [
        "goodbye", "bye", "not interested", "stop calling",
        "remove me", "don't call", "no thank you", "no thanks",
        "not now", "busy"
    ]
    if any(s in speech.lower() for s in end_signals):
        farewell = get_ai_response(call_id, f"Prospect said: '{speech}'. Close the call warmly.")
        log_lead(call_id, "declined")
        return texml(farewell, gather=False)

    reply = get_ai_response(call_id, speech)
    return texml(reply, gather=True, call_id=call_id)


@app.route("/outbound", methods=["POST"])
def trigger_outbound():
    body      = request.get_json(silent=True) or {}
    to_number = body.get("to", "").strip()

    if not to_number:
        return jsonify({"error": "'to' phone number required"}), 400
    if not TELNYX_API_KEY:
        return jsonify({"error": "TELNYX_API_KEY not configured"}), 500

    try:
        resp = requests.post(
            "https://api.telnyx.com/v2/calls",
            headers={
                "Authorization": f"Bearer {TELNYX_API_KEY}",
                "Content-Type":  "application/json"
            },
            json={
                "connection_id": TELNYX_CONNECTION_ID,
                "to":            to_number,
                "from":          TELNYX_FROM_NUMBER,
                "webhook_url":   f"{WEBHOOK_BASE_URL}/webhook"
            },
            timeout=10
        )
        print(f"[OUTBOUND] to={to_number} status={resp.status_code}")
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/leads", methods=["GET"])
def get_leads():
    return jsonify({"count": len(leads_log), "leads": leads_log})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":           "ok",
        "agent":            "CleanPro Discovery Agent",
        "time":             datetime.now().isoformat(),
        "gemini_key_set":   bool(GEMINI_API_KEY),
        "telnyx_key_set":   bool(TELNYX_API_KEY),
        "webhook_url":      WEBHOOK_BASE_URL or "NOT SET"
    })


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "name":   "CleanPro Discovery Agent",
        "status": "running",
        "routes": {
            "POST /webhook":  "Telnyx event receiver",
            "POST /gather":   "Speech input handler",
            "POST /outbound": "Trigger outbound call { to: '+254...' }",
            "GET  /leads":    "View captured leads",
            "GET  /health":   "Health check + config status"
        }
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
