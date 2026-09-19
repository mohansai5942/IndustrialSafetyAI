from pathlib import Path
import threading, time
import os, json
from concurrent.futures import TimeoutError as FutureTimeout
from flask import Flask, jsonify, render_template, request, Response, send_from_directory, abort
from camera_ai import CameraAI, WebRTCService, CameraBusy
from risk_engine import calculate_risk
from incident_manager import IncidentManager
from nlp_routes import register_nlp_routes

BASE = Path(__file__).resolve().parent
MODEL = BASE / "models" / "yolo11n.pt"
MODEL.parent.mkdir(exist_ok=True)
incident_manager = IncidentManager(BASE)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
register_nlp_routes(app)
lock = threading.Lock()

STATE = {
    "running": False, "camera_ok": False, "camera_error": "",
    "risk": 0, "level": "LOW", "hazard": "Normal monitoring",
    "workers": 0, "restricted": 0, "aerosol": False, "fire": False,
    "fall": False, "helmet_not_verified": 0, "cap_not_verified": 0, "vision_confidence": 0,
    "gas": 10.0, "temperature": 40.0, "pressure": 5.0, "equipment": 0.0,
    "gas_rate": 0.0, "temperature_rate": 0.0, "pressure_rate": 0.0,
    "alarm": False, "ack_required": False, "acknowledged": False, "ack_deadline": None,
    "escalated": False, "escalated_at": None, "escalated_to": "",
    "last_event": "Normal monitoring", "last_incident_id": None,
    "demo_message": ""
}
DEMO = {"aerosol": False, "fire": False, "fall": False, "ppe": False}
ALARM_THRESHOLD = 95
HIGH_RISK_THRESHOLD = 71
ACK_SECONDS = 15
camera = CameraAI(MODEL, STATE, lock)
_prev_inputs = {"gas":10.0, "temperature":40.0, "pressure":5.0, "time":time.time()}
_last_incident_signature = ""
_last_incident_time = 0.0


def hazard_from_reasons(reasons):
    priority = [
        ("Fire-like visual", "Fire-like visual"),
        ("High gas reading", "Gas hazard"),
        ("High pressure", "Pressure abnormality"),
        ("High temperature", "Temperature hazard"),
        ("Possible fall", "Possible worker fall"),
        ("Restricted-zone entry", "Restricted-zone entry"),
        ("Equipment anomaly", "Equipment anomaly"),
        ("Aerosol/smoke-like event", "Aerosol / smoke-like event"),
        ("Helmet not verified", "Helmet not verified"),
    ]
    for key, name in priority:
        if key in reasons:
            return name
    return "Safety anomaly" if reasons else "Normal monitoring"


def apply_demo_to_state():
    """Make demo buttons deterministic immediately, even before the camera loop updates."""
    with lock:
        STATE["aerosol"] = DEMO["aerosol"] or STATE["aerosol"]
        STATE["fire"] = DEMO["fire"] or STATE["fire"]
        STATE["fall"] = DEMO["fall"] or STATE["fall"]
        if DEMO["ppe"]:
            STATE["helmet_not_verified"] = max(1, STATE["helmet_not_verified"])
        active = [name for name, on in DEMO.items() if on]
        STATE["demo_message"] = "Demo active: " + ", ".join(active) if active else ""


def evaluation_loop():
    global _prev_inputs, _last_incident_signature, _last_incident_time
    while True:
        time.sleep(0.5)
        with lock:
            # Keep deterministic demo signals alive even if the live detector temporarily misses a frame.
            d = dict(DEMO)
            gas, temp, pressure, equipment = STATE["gas"], STATE["temperature"], STATE["pressure"], STATE["equipment"]
            workers, restricted = STATE["workers"], STATE["restricted"]
            aerosol = STATE["aerosol"] or d["aerosol"]
            fire = STATE["fire"] or d["fire"]
            fall = STATE["fall"] or d["fall"]
            helmet = max(STATE["helmet_not_verified"], 1 if d["ppe"] else 0)
            now = time.time(); dt=max(0.5, now-_prev_inputs["time"])
            gr=(gas-_prev_inputs["gas"])/dt; tr=(temp-_prev_inputs["temperature"])/dt; pr=(pressure-_prev_inputs["pressure"])/dt
            _prev_inputs={"gas":gas,"temperature":temp,"pressure":pressure,"time":now}
            result=calculate_risk(restricted=restricted,aerosol=aerosol,fire=fire,fall=fall,
                                  helmet_not_verified=helmet,workers=workers,gas=gas,temperature=temp,
                                  pressure=pressure,equipment=equipment)
            STATE.update(aerosol=aerosol, fire=fire, fall=fall, helmet_not_verified=helmet, cap_not_verified=helmet,
                         risk=result.score,level=result.level,hazard=hazard_from_reasons(result.reasons),
                         gas_rate=round(gr,2),temperature_rate=round(tr,2),pressure_rate=round(pr,2),
                         last_event=" • ".join(result.reasons[:4]) if result.reasons else "Normal monitoring")

            # Emergency alarm: HIGH/CRITICAL risk requires acknowledgement.
            # The alarm remains active continuously until somebody acknowledges it.
            # After 15 seconds the escalation notification is sent, but the original
            # acknowledgement control remains available until it is clicked.
            alarm_condition = result.score >= HIGH_RISK_THRESHOLD
            if alarm_condition and not STATE["alarm"] and not STATE["acknowledged"]:
                STATE["ack_required"] = True
                STATE["acknowledged"] = False
                STATE["ack_deadline"] = time.time() + ACK_SECONDS
                STATE["escalated"] = False
                STATE["escalated_at"] = None
                STATE["escalated_to"] = ""
                STATE["alarm"] = True
            elif not alarm_condition:
                # Once the hazardous condition clears, re-arm the acknowledgement state
                # so a later HIGH/CRITICAL event can create a fresh alarm.
                STATE["alarm"] = False
                STATE["ack_required"] = False
                STATE["acknowledged"] = False
                STATE["ack_deadline"] = None
                STATE["escalated"] = False
                STATE["escalated_at"] = None
                STATE["escalated_to"] = ""
                _last_incident_signature = ""

            # Escalation is a local prototype notification state. In a deployed system
            # this is where an SMS/email/radio/plant notification connector is called.
            if (STATE["alarm"] and STATE["ack_required"] and STATE["ack_deadline"]
                    and time.time() >= STATE["ack_deadline"] and not STATE["escalated"]):
                STATE["escalated"] = True
                STATE["escalated_at"] = time.time()
                STATE["escalated_to"] = "Next Responsible Person / Area In-Charge"
                incident_manager.escalate(STATE.get("last_incident_id"))
            critical = result.level == "CRITICAL"

            # Store CCTV incidents only when the operational risk is HIGH or CRITICAL.
            # Evidence images are captured only for those high/critical records.
            reasons = list(result.reasons)
            if reasons and result.score >= HIGH_RISK_THRESHOLD:
                # One record per distinct active high-risk event. Do not flood the
                # incident database with the same camera condition every few seconds.
                signature=f'{STATE["hazard"]}|{restricted}|{aerosol}|{fire}|{fall}|{helmet}'
                should_save=(signature != _last_incident_signature)
                if should_save:
                    evidence=camera.latest_jpeg
                    frame=None
                    if critical and evidence:
                        import numpy as np, cv2
                        frame=cv2.imdecode(np.frombuffer(evidence,np.uint8),cv2.IMREAD_COLOR)
                    response=("Critical risk detected. Acknowledge and verify the scene."
                              if critical else "High-risk event recorded. Acknowledge and verify the scene.")
                    incident_id=incident_manager.save_incident({
                        "incident_type":STATE["hazard"], "workers":workers, "risk":result.score,
                        "severity":result.level, "confidence":min(99.0,max(50.0,STATE["vision_confidence"] or 75)),
                        "gas":gas, "temperature":temp, "pressure":pressure, "equipment":equipment,
                        "incident_description": f'{STATE["hazard"]} was detected in the monitored area. Workers detected: {workers}. '
                                                 f'Restricted-zone count: {restricted}. Observed conditions include risk score {result.score}/100 '
                                                 f'and {STATE["hazard"].lower()}. The event requires human verification and corrective action.',
                        "response":response,
                        "source":"CCTV / AI Vision"
                    }, frame)
                    incident_manager.make_pdf(incident_id)
                    STATE["last_incident_id"]=incident_id
                    _last_incident_signature=signature
                    _last_incident_time=time.time()


@app.route("/")
def dashboard(): return render_template("dashboard.html")

@app.route("/cctv")
def cctv_operations(): return render_template("cctv.html")

@app.route("/manual-analysis")
def manual_analysis(): return render_template("manual.html")

@app.route("/demo")
def public_demo(): return render_template("dashboard.html")

@app.route("/sif-analyzer")
def sif_analyzer(): return render_template("nlp.html")

@app.route("/video_feed")
def video_feed():
    return jsonify(error="Open /cctv and start your browser camera"), 410

@app.get("/api/state")
def api_state():
    with lock:
        s=dict(STATE)
        s["camera_fps"]=round(camera.fps,1)
        s["demo"]=dict(DEMO)
        s["ack_seconds"]=max(0,int(s["ack_deadline"]-time.time())) if s["ack_deadline"] else 0
        s["escalated_to"] = s.get("escalated_to") or ""
    return jsonify(s)

@app.get("/api/incidents")
def api_incidents(): return jsonify(incident_manager.list_incidents())

@app.get("/api/analytics")
def api_analytics(): return jsonify(incident_manager.analytics())

@app.post("/api/nlp/save")
def nlp_save():
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(ok=False, error="Report text is required"), 400
    from sif_engine import analyze_report
    r = analyze_report(text)
    risk = int(max(0, min(100, r.score)))
    level = r.level if r.is_sif else ("LOW" if risk < 30 else "MEDIUM" if risk < 55 else "HIGH")
    incident_type = r.category or "Safety observation"
    incident_id = incident_manager.save_incident({
        "incident_type": incident_type, "workers": 0, "risk": risk, "severity": level,
        "confidence": r.model_confidence, "incident_description": text,
        "response": "AI analysis completed. Human verification and corrective action required.",
        "source": "Manual UA / UC / Near-Miss Report"
    })
    incident_manager.make_pdf(incident_id)
    return jsonify(ok=True, incident_id=incident_id, score=risk, level=level, is_sif=r.is_sif)

@app.post("/api/nlp/batch")
def nlp_batch():
    data = request.get_json(force=True) or {}
    reports = data.get("reports") or []
    reports = [str(x).strip() for x in reports if str(x).strip()]
    if not reports:
        return jsonify(ok=False, error="At least one report is required"), 400
    if len(reports) > 50:
        return jsonify(ok=False, error="Maximum 50 reports can be analyzed at once"), 400
    from sif_engine import analyze_report
    out = []
    for text in reports:
        r = analyze_report(text)
        out.append({
            "is_sif": r.is_sif, "score": int(max(0, min(100, r.score))),
            "level": r.level if r.is_sif else ("LOW" if r.score < 30 else "MEDIUM" if r.score < 55 else "HIGH"),
            "category": r.category or "Safety observation",
            "model_confidence": r.model_confidence, "model_label": r.model_label,
            "precursors": r.precursors
        })
    return jsonify(ok=True, results=out, count=len(out))

@app.post("/api/nlp/batch-save")
def nlp_batch_save():
    data = request.get_json(force=True) or {}
    reports = data.get("reports") or []
    results = data.get("results") or []
    if not reports or not results or len(reports) != len(results) or len(reports) > 50:
        return jsonify(ok=False, error="A valid set of up to 50 analyzed reports is required"), 400
    saved = 0
    for text, r in zip(reports, results):
        risk = int(max(0, min(100, r.get("score", 0))))
        level = r.get("level", "LOW")
        incident_id = incident_manager.save_incident({
            "incident_type": r.get("category") or "Safety observation",
            "workers": 0, "risk": risk, "severity": level,
            "confidence": float(r.get("model_confidence", 0) or 0),
            "incident_description": str(text),
            "response": "Report-set NLP/SIF screening completed. Human verification and corrective action required.",
            "source": "Manual UA / UC / Near-Miss Report"
        })
        incident_manager.make_pdf(incident_id)
        saved += 1
    return jsonify(ok=True, saved=saved)

@app.post("/api/inputs")
def api_inputs():
    data=request.get_json(force=True) or {}
    limits={"gas":(0,150),"temperature":(0,150),"pressure":(0,15),"equipment":(0,100)}
    with lock:
        for k,(lo,hi) in limits.items():
            if k in data:
                STATE[k]=max(lo,min(hi,float(data[k])))
    return jsonify(ok=True)

@app.post("/api/demo")
def api_demo():
    key=(request.get_json(force=True) or {}).get("key")
    if key not in DEMO: return jsonify(ok=False,error="Unknown demo event"),400
    DEMO[key]=not DEMO[key]
    camera.set_demo(key, DEMO[key])
    # Explicitly set/clear state so the UI responds immediately.
    with lock:
        if key == "aerosol": STATE["aerosol"] = DEMO[key]
        elif key == "fire": STATE["fire"] = DEMO[key]
        elif key == "fall": STATE["fall"] = DEMO[key]
        elif key == "ppe": STATE["helmet_not_verified"] = 1 if DEMO[key] else 0
        STATE["demo_message"] = "Demo active: " + ", ".join(k for k,v in DEMO.items() if v) if any(DEMO.values()) else ""
    return jsonify(ok=True,enabled=DEMO[key],demo=dict(DEMO))

@app.post("/api/ack")
def api_ack():
    with lock:
        if not STATE.get("alarm"):
            return jsonify(ok=False, error="No active alarm"), 409
        STATE["ack_required"]=False
        STATE["acknowledged"]=True
        STATE["ack_deadline"]=None
        STATE["escalated"]=False
        STATE["escalated_at"]=None
        STATE["escalated_to"]=""
        # Stop the active alarm immediately after acknowledgement.
        STATE["alarm"]=False
        current=STATE.get("last_incident_id")
    incident_manager.acknowledge(current)
    return jsonify(ok=True, alarm=False, acknowledged=True)

@app.post("/api/reset")
def api_reset():
    global _last_incident_signature, _last_incident_time
    with lock:
        STATE.update(gas=10.0,temperature=40.0,pressure=5.0,equipment=0.0,
                     aerosol=False,fire=False,fall=False,helmet_not_verified=0,cap_not_verified=0,
                     risk=0,level="LOW",hazard="Normal monitoring",last_event="Normal monitoring",
                     alarm=False,ack_required=False,acknowledged=False,ack_deadline=None,
                     escalated=False,escalated_at=None,escalated_to="",demo_message="")
    for k in DEMO:
        DEMO[k]=False
        camera.set_demo(k,False)
    _last_incident_signature=""; _last_incident_time=0.0
    return jsonify(ok=True)

@app.get("/api/report/<int:incident_id>")
def api_report(incident_id):
    path=incident_manager.make_pdf(incident_id)
    if path is None: abort(404)
    return send_from_directory(path.parent,path.name,as_attachment=True)

@app.get("/evidence/<path:name>")
def evidence(name): return send_from_directory(incident_manager.evidence_dir,name)

# Empty ICE_SERVERS works on localhost/LAN only. For Internet deployment configure
# STUN and TURN. This JSON is intentionally sent to browsers; use short-lived TURN
# credentials behind authenticated access in production.
ICE_SERVERS = json.loads(os.environ.get("ICE_SERVERS", "[]"))
rtc = None
_services_lock = threading.Lock()

@app.before_request
def ensure_services():
    global rtc
    # Starts inside the WSGI worker, including when launched as gunicorn app:app.
    with _services_lock:
        if rtc is None:
            rtc = WebRTCService(camera, ICE_SERVERS)
            threading.Thread(target=evaluation_loop, daemon=True).start()

@app.get("/api/webrtc/config")
def webrtc_config():
    response = jsonify(iceServers=ICE_SERVERS)
    response.headers["Cache-Control"] = "no-store"
    return response

@app.post("/api/webrtc/offer")
def webrtc_offer():
    data = request.get_json(silent=True)
    if (not isinstance(data, dict) or data.get("type") != "offer"
            or not isinstance(data.get("sdp"), str) or not data["sdp"].startswith("v=0")):
        return jsonify(error="A valid WebRTC video offer is required"), 400
    future = rtc.submit(rtc.offer(data["sdp"]))
    try:
        response = jsonify(future.result(timeout=30))
        response.headers["Cache-Control"] = "no-store"
        return response
    except CameraBusy as exc:
        return jsonify(error=str(exc)), 409
    except FutureTimeout:
        future.cancel()
        return jsonify(error="WebRTC setup timed out. Check ICE/TURN configuration."), 504
    except Exception:
        app.logger.exception("WebRTC negotiation failed")
        return jsonify(error="Could not negotiate video connection"), 400

@app.post("/api/webrtc/stop")
def webrtc_stop():
    data = request.get_json(silent=True)
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token or len(token) > 128:
        return jsonify(error="Connection token is required"), 400
    future = rtc.submit(rtc.stop(token))
    try:
        stopped = future.result(timeout=10)
        return jsonify(ok=stopped), 200 if stopped else 404
    except FutureTimeout:
        # Cleanup continues; do not cancel it while inference is finishing.
        return jsonify(ok=True, stopping=True), 202


def main():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
            debug=False, threaded=True)

if __name__ == "__main__":
    main()
