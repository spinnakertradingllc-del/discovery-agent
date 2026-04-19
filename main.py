import os
import requests
from datetime import datetime
from flask import Flask, request, Response, jsonify

app = Flask(__name__)

# ── Credentials from environment variables ────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TELNYX_API_KEY       = os.environ.get("TELNYX_API_KEY", "")
TELNYX_FROM_NUMBER   = os.environ.get("TELNYX_FROM_NUMBER", "")
TELNYX_CONNECTION_ID = os.environ.get("TELNYX_CONNECTION_ID", "")
WEBHOOK_BASE_URL     = os.environ.get("WEBHOOK_BASE_URL", "").rstrip("/")

# ── In-memory stores ──────────────────────────────────────────────────────────
call_sessions: dict = {}  # call_id → [{role, content}, ...]
leads_log:     list = []  # completed call records

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""You are Alex, a professional B2B sales agent for CleanPro Chemicals.
We supply industrial cleaning chemicals for food production machinery and dairy factories.

CALL SEQUENCE — work through this naturally:
1. GREET warmly. Confirm you have the right person or department.
2. QUALIFY — ask about: operation type (dairy/food production/beverage),
   current cleaning chemical supplier, monthly usage volume.
3. PITCH relevant CleanPro products:
   - CIP alkaline & acid solutions (Clean-In-Place)
   - Caustic foam cleaners for external surfaces
   - Acid descalers for mineral/scale removal
   - Food-grade no-rinse sanitizers
   - Enzyme degreasers for conveyors & machinery
   Highlight: food-grade certified, cost-effective, full technical support included.
4. CAPTURE: their name, company, and best email address.
5. BOOK: offer a follow-up call OR free product sample delivery.

RULES:
- MAX 2 short sentences per reply — this is a live phone call.
- Sound natural and human. Never robotic.
- Answer chemistry questions confidently (industrial chemistry background).
- Never quote prices — say "I will send you our current price list".
- If prospect declines or says goodbye → thank them warmly and end gracefully.
- Today: {datetime.now().strftime("%B %d, %Y")}"""


# ── Groq AI ───────────────────────────────────────────────────────────────────
def get_ai_response(call_id: str, user_input: str) -> str:
    if call_id not in call_sessions:
        call_sessions[call_id] = []

    call_sessions[call_id].append({"role": "user", "content": user_input})

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model":       "llama3-8b-8192",
                "max_tokens":  120,
                "temperature": 0.7,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *call_sessions[call_id]
                ]
            },
            timeout=8
        )
        resp.raise_for_status()
        ai_text = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[AI ERROR] {e}")
        ai_text = "I'm sorry, I had a brief technical issue. Just one moment please."

    call_sessions[call_id].append({"role": "assistant", "content": ai_text})
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
    call_sessions.pop(call_id, None)  # free memory


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

    # Telnyx can send speech in form data or JSON — handle both
    json_body = request.get_json(silent=True) or {}
    speech = (
        request.form.get("SpeechResult")
        or request.form.get("speech_result")
        or json_body.get("SpeechResult")
        or json_body.get("speech_result")
        or ""
    ).strip()

    print(f"[GATHER] call_id={call_id} reprompt={reprompt} speech='{speech}'")

    # Nothing heard
    if not speech:
        if reprompt == "1":
            log_lead(call_id, "no_response")
            return texml(
                "I'll let you go for now. Feel free to reach us anytime. Goodbye!",
                gather=False
            )
        return texml(
            "Sorry, I didn't quite catch that — could you repeat?",
            gather=True, call_id=call_id
        )

    # End-of-call signals
    end_signals = [
        "goodbye", "bye", "not interested", "stop calling",
        "remove me", "don't call", "no thank you", "no thanks",
        "not now", "busy"
    ]
    if any(s in speech.lower() for s in end_signals):
        farewell = get_ai_response(
            call_id, f"Prospect said: '{speech}'. Close the call warmly."
        )
        log_lead(call_id, "declined")
        return texml(farewell, gather=False)

    # Normal conversation turn
    reply = get_ai_response(call_id, speech)
    return texml(reply, gather=True, call_id=call_id)


@app.route("/outbound", methods=["POST"])
def trigger_outbound():
    """Trigger an outbound call. Body: { "to": "+254XXXXXXXXX" }"""
    body      = request.get_json(silent=True) or {}
    to_number = body.get("to", "").strip()

    if not to_number:
        return jsonify({"error": "'to' phone number required"}), 400
    if not TELNYX_API_KEY:
        return jsonify({"error": "TELNYX_API_KEY not configured"}), 500
    if not TELNYX_CONNECTION_ID:
        return jsonify({"error": "TELNYX_CONNECTION_ID not configured"}), 500

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
        "status":          "ok",
        "agent":           "CleanPro Discovery Agent",
        "time":            datetime.now().isoformat(),
        "groq_key_set":    bool(GROQ_API_KEY),
        "telnyx_key_set":  bool(TELNYX_API_KEY),
        "webhook_url":     WEBHOOK_BASE_URL or "NOT SET — add to env vars"
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
            "GET  /leads":    "View all captured leads",
            "GET  /health":   "Health check + config status"
        }
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
