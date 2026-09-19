# Browser camera deployment

Replace app.py, camera_ai.py, templates/cctv.html and requirements.txt with the supplied files. Other application files remain required. These are complete replacement files based on the repository's main branch, not a standalone application.

## How it works

The browser requests camera permission with getUserMedia (no microphone), shows a local preview, and sends video through RTCPeerConnection. Flask exchanges the SDP offer/answer with aiortc. OpenCV/YOLO process received frames and aiortc returns annotated video. This is actual WebRTC media transport, not JPEG polling. Existing state polling, simulation controls, alarms and incident reports remain.

Only the newest waiting frame is retained while inference runs. Inference runs off the WebRTC event loop. Stop, navigation, hidden tabs, failed connections, and stale streams release the connection and clear camera state. A second publisher receives HTTP 409 because the original application's risk state and incident store describe one shared monitoring station.

## Install and run

Use Python 3.11 or 3.12 and a clean virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Provision your trusted YOLO11n weights as `models/yolo11n.pt`. The latest repository includes these weights; keep them in the deployment image. Optionally provision `models/cap_hat_best.pt` for the existing cap/hat detector. A cap is not a verified safety helmet.

For localhost testing:

```bash
python app.py
```

Open http://localhost:8080/cctv and press Start camera. For deployment, terminate HTTPS at your host/reverse proxy and run:

```bash
gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT:-5000} app:app
```

Use exactly one worker and one replica. Do not use --preload. The app starts its evaluation thread and persistent WebRTC loop on the first request inside the worker. More workers/replicas require external shared state and media-session routing; this change does not add those facilities.

## Internet connectivity

Camera permission requires HTTPS, except for localhost. Accessing a remote server over ordinary HTTP will not work.

Set the ICE_SERVERS environment variable to a JSON array containing your real STUN/TURN configuration, for example:

```json
[
  {"urls": "stun:YOUR_TURN_HOST:3478"},
  {"urls": "turn:YOUR_TURN_HOST:3478?transport=tcp", "username": "YOUR_TURN_USERNAME", "credential": "YOUR_TURN_PASSWORD"}
]
```

The example host/credentials must be replaced. Without ICE_SERVERS, only local/LAN testing is intended. STUN alone does not cover restrictive NAT/firewalls; provision a reachable TURN relay. Your host must permit the outbound connections required by your TURN service, or reachable WebRTC UDP media for direct connections. A host exposing HTTPS alone does not automatically support WebRTC. Use TURN TLS on port 443 if required by your network and supported by your relay, with a `turns:` URL and TCP transport.

/api/webrtc/config sends ICE credentials to the browser by design. Use protected access and short-lived TURN credentials for production. The current environment variable is a deployment configuration, not a credential-issuing service.

## Existing application limitations

This remains one shared station: risk metrics, controls, reports and critical evidence are shared. Only the active publishing browser receives the returned WebRTC video. Separate independent users/cameras need isolated application state, incident storage and authenticated ownership. Existing dashboard/report/control routes have no authentication; restrict access to authorized operators before publishing real camera data. Critical evidence and SQLite/PDF files need persistent disk if they must survive redeployments.

The existing visual heuristics are prototype signals, not certified detection. Validate temporal detection thresholds against the effective inference frame rate and your real camera scenes before relying on them.

## Verification performed

- Python syntax compilation and JavaScript syntax check passed.
- Real Flask signaling and aiortc loopback video round-trip passed, including OpenCV annotations. The sandbox test used loopback explicitly because network-interface enumeration is restricted.
- Request validation, duplicate publisher rejection, token validation, stop/reset and reconnect passed.
- Missing weights produce an explicit error rather than successful empty detections.
- YOLO was substituted with a deterministic empty detector in transport tests. Real YOLO inference, browser permission UI, cloud HTTPS, and Internet TURN connectivity have not been tested here.

References:
- https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia
- https://aiortc.readthedocs.io/en/latest/api.html

## Railway native-library crash fix

Nixpacks reads nixpacks.toml; Railway's current default Railpack reads railpack.json. Both configurations are included. Railpack installs the system packages in BOTH build and runtime layers. No dependency on apt.txt is required.

Use only opencv-python from requirements.txt, matching Ultralytics' declared dependency. Do not also install opencv-python-headless or either opencv-contrib package: they share the cv2 namespace. The image build checks native-library loading, OpenCV/YOLO/WebRTC imports, single OpenCV distribution and JPEG encoding through scripts/check_runtime.py.

Redeploy with a clean build after this change (Railway NO_CACHE=1 for one build, then remove it). Verify the build log names the builder and shows the runtime-check success message. Remove conflicting custom install/build/start commands or package override variables from Railway settings. Service root must be the repository root. Keep one replica and one Gunicorn worker.

This targets the missing native-library failure. A successful Railway build/deploy still requires verification in Railway; no remote Railway deployment was executed during this edit. For browser video on Railway, configure a reachable TURN relay; the HTTPS web endpoint alone does not carry the WebRTC media.
