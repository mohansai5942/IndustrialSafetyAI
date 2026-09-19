"""Computer vision layer for the Industrial Safety AI SIH prototype.

The camera layer is deliberately conservative:
- YOLO11n detects people.
- A dedicated headwear model detects *hat/cap* (not helmet) when available.
- Fall detection uses temporal person geometry instead of a single-frame box ratio.
- Aerosol and fire use temporal visual heuristics with persistence.

All detections are decision-support signals, not certified safety instruments.
"""
import time, threading
from pathlib import Path
import cv2
import numpy as np
from ultralytics import YOLO

try:
    from huggingface_hub import hf_hub_download
except Exception:
    hf_hub_download = None


class CameraAI:
    def __init__(self, model_path: Path, state, lock, width=640, height=360):
        self.model_path = str(model_path)
        self.state = state
        self.lock = lock
        self.width, self.height = width, height
        self.zone = np.array([(600, 120), (940, 120), (940, 520), (600, 520)], np.int32)
        self.running = False
        self.camera_ok = False
        self.fps = 0.0
        self.latest_jpeg = None
        self.thread = None
        self.stop_event = threading.Event()
        self.prev_gray = None
        self.aerosol_hits = 0
        self.fire_hits = 0
        self.helmet_model = None
        self.cap_model_path = Path(model_path).parent / "cap_hat_best.pt"
        self.demo = {"aerosol": False, "fire": False, "fall": False, "ppe": False}
        self.demo_lock = threading.Lock()
        self.person_tracks = []
        self.track_counter = 0

    def set_demo(self, key, enabled):
        if key not in self.demo:
            return
        with self.demo_lock:
            self.demo[key] = bool(enabled)

    def _demo_state(self):
        with self.demo_lock:
            return dict(self.demo)

    def start(self):
        if self.running:
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _download_cap_model(self):
        """Download the hardhat/hat model once; continue safely if unavailable.

        The model explicitly has a `hat` class, which is what this project uses
        because the demonstrator wears caps rather than safety helmets.
        """
        if self.cap_model_path.exists():
            return str(self.cap_model_path)
        if hf_hub_download is None:
            return None
        try:
            p = hf_hub_download(
                repo_id="luisarizmendi/hardhat-or-hat",
                filename="v2/model/pytorch/best.pt",
                local_dir=str(self.cap_model_path.parent),
            )
            p = Path(p)
            if p != self.cap_model_path and p.exists():
                p.replace(self.cap_model_path)
            return str(self.cap_model_path)
        except Exception as exc:
            with self.lock:
                self.state["cap_model_error"] = str(exc)
            return None

    @staticmethod
    def _associate_headwear(person_box, detections):
        x1, y1, x2, y2 = person_box
        best = None
        ph = max(1, y2 - y1)
        for bx1, by1, bx2, by2, cls_name, conf in detections:
            cx = (bx1 + bx2) / 2
            cy = (by1 + by2) / 2
            # A cap/hat must lie in the upper 38% of the person box and overlap it.
            if x1 <= cx <= x2 and y1 <= cy <= y1 + 0.38 * ph:
                overlap_w = max(0, min(x2, bx2) - max(x1, bx1))
                if overlap_w <= 0:
                    continue
                score = conf - 0.001 * max(0, cy - y1)
                if best is None or score > best[0]:
                    best = (score, cls_name, conf)
        return best

    def _temporal_fall(self, people):
        """Temporal fall detector: combines posture + sudden vertical change + persistence."""
        current = []
        fall_now = False
        for x1, y1, x2, y2, conf in people:
            w = max(1, x2 - x1); h = max(1, y2 - y1)
            cx = (x1 + x2) / 2; cy = (y1 + y2) / 2
            ratio = w / h
            best = None; best_dist = 1e9
            for tr in self.person_tracks:
                dist = ((cx - tr["cx"]) ** 2 + (cy - tr["cy"]) ** 2) ** 0.5 / max(20, tr["h"])
                if dist < best_dist and dist < 1.25:
                    best_dist = dist; best = tr

            posture = ratio >= 1.55
            sudden_drop = False
            if best is not None:
                drop = (cy - best["cy"]) / max(20, best["h"])
                height_loss = h / max(20, best["h"])
                sudden_drop = drop > 0.35 and height_loss < 0.82
                if posture or sudden_drop:
                    best["fall_hits"] = min(6, best.get("fall_hits", 0) + 1)
                else:
                    best["fall_hits"] = max(0, best.get("fall_hits", 0) - 1)
                fall_now = fall_now or best["fall_hits"] >= 5
                tr = best
            else:
                tr = {"id": self.track_counter, "cx": cx, "cy": cy, "h": h, "w": w, "fall_hits": 1 if posture else 0}
                self.track_counter += 1
            tr.update(cx=cx, cy=cy, h=h, w=w, last=time.time())
            current.append(tr)

        # Keep recent tracks briefly so one missed YOLO frame does not reset the fall state.
        now = time.time()
        self.person_tracks = [t for t in self.person_tracks + current if now - t.get("last", now) < 0.8]
        # Deduplicate by track id.
        dedup = {}
        for t in self.person_tracks:
            dedup[t["id"]] = t
        self.person_tracks = list(dedup.values())
        return fall_now

    def _visual_aerosol(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self.prev_gray is None:
            self.prev_gray = gray
            return False
        diff = cv2.absdiff(gray, self.prev_gray)
        motion = (diff > 14).astype(np.uint8) * 255
        motion = cv2.GaussianBlur(motion, (9, 9), 0)
        motion = (motion > 30).astype(np.uint8) * 255
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        _, s, v = cv2.split(hsv)
        mist = ((s < 105) & (v > 135) & (v < 255)).astype(np.uint8) * 255
        combined = cv2.bitwise_and(mist, motion)
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(combined, 8)
        largest = max((int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)), default=0)
        ratio = largest / float(frame.shape[0] * frame.shape[1])
        hit = ratio > 0.002
        self.aerosol_hits = min(12, self.aerosol_hits + 1) if hit else max(0, self.aerosol_hits - 1)
        return self.aerosol_hits >= 3

    def _visual_fire(self, frame):
        """More sensitive fire-like detector using warm/bright pixels + flicker persistence."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self.prev_gray is None:
            return False
        diff = cv2.absdiff(gray, self.prev_gray)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h, s, v = cv2.split(hsv)
        # Flame-like orange/yellow/red ranges. Require high saturation and brightness.
        warm = (((h <= 28) | (h >= 170)) & (s >= 120) & (v >= 165)).astype(np.uint8) * 255
        # Motion OR local brightness fluctuation helps catch a flickering flame.
        motion = (diff > 12).astype(np.uint8) * 255
        bright = ((v > 185) & (s > 105)).astype(np.uint8) * 255
        candidate = cv2.bitwise_and(warm, cv2.bitwise_or(motion, bright))
        candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
        good = 0
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            ww = int(stats[i, cv2.CC_STAT_WIDTH]); hh = int(stats[i, cv2.CC_STAT_HEIGHT])
            fill = area / max(1, ww * hh)
            if area >= 500 and ww >= 15 and hh >= 15 and fill > 0.18:
                good = max(good, area)
        ratio = good / float(frame.shape[0] * frame.shape[1])
        hit = ratio > 0.0008
        self.fire_hits = min(12, self.fire_hits + 1) if hit else max(0, self.fire_hits - 1)
        return self.fire_hits >= 2

    def _run(self):
        # Open the webcam FIRST so the live picture appears immediately.
        # Model loading/inference happens only after the camera is already live.
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release(); cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        try:
            cap.set(cv2.CAP_PROP_FPS, 30)
        except Exception:
            pass
        if not cap.isOpened():
            with self.lock:
                self.state["camera_error"] = "Camera unavailable. Check Windows camera permissions."
                self.state["camera_ok"] = False; self.state["running"] = False
            return

        self.running = True; self.camera_ok = True
        with self.lock:
            self.state["camera_ok"] = True
            self.state["running"] = True
            self.state["camera_error"] = ""

        # The camera is now visible even if AI models take a few seconds to load.
        try:
            model = YOLO(self.model_path)
        except Exception as exc:
            with self.lock:
                self.state["camera_error"] = f"Model load failed: {exc}"
            model = None

        # Optional cap/hat model: use a local model if present. Do NOT block camera
        # startup with an internet download; missing optional model is safe.
        cap_path = str(self.cap_model_path) if self.cap_model_path.exists() else None
        if cap_path:
            try:
                self.helmet_model = YOLO(cap_path)
            except Exception as exc:
                with self.lock:
                    self.state["cap_model_error"] = f"Cap/hat model load failed: {exc}"

        last = time.time()
        frame_count = 0
        last_inference = 0.0
        inference_interval = 0.12  # about 8 AI updates/sec on CPU
        last_people = []
        last_headwear = []
        last_fall = False
        last_aerosol = False
        last_fire = False

        while not self.stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01); continue

            frame = cv2.resize(frame, (self.width, self.height))
            analysis_frame = frame.copy()
            now = time.time()
            dt = max(0.001, now - last); last = now
            self.fps = 1.0 / dt
            frame_count += 1

            workers = restricted = cap_not_verified = 0
            fall = last_fall
            confs = []
            people = last_people
            headwear_detections = last_headwear
            demo = self._demo_state()

            # Keep the display running at camera speed. AI inference is throttled
            # separately so a slow CPU model never freezes the live video.
            if model is not None and (now - last_inference >= inference_interval):
                last_inference = now
                people = []
                headwear_detections = []
                try:
                    result = model.predict(frame, imgsz=416, conf=0.35, device="cpu", verbose=False)[0]
                    if result.boxes is not None:
                        for b in result.boxes:
                            if int(b.cls[0]) != 0: continue
                            conf = float(b.conf[0]); confs.append(conf)
                            x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
                            people.append((x1, y1, x2, y2, conf))

                    last_fall = self._temporal_fall(people)
                    fall = last_fall

                    if self.helmet_model is not None and people:
                        hr = self.helmet_model.predict(frame, imgsz=416, conf=0.30, device="cpu", verbose=False)[0]
                        names = self.helmet_model.names
                        if hr.boxes is not None:
                            for b in hr.boxes:
                                cls_id = int(b.cls[0])
                                name = str(names.get(cls_id, cls_id)).lower().replace("-", "_").replace(" ", "_")
                                bx1, by1, bx2, by2 = map(int, b.xyxy[0].tolist())
                                headwear_detections.append((bx1, by1, bx2, by2, name, float(b.conf[0])))
                    last_people = people
                    last_headwear = headwear_detections
                except Exception as exc:
                    with self.lock:
                        self.state["camera_error"] = f"Inference warning: {exc}"

            workers = len(people)
            restricted = 0
            for x1, y1, x2, y2, conf in people:
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                restricted += int(cv2.pointPolygonTest(self.zone, (cx, cy), False) >= 0)

                inside = cv2.pointPolygonTest(self.zone, (cx, cy), False) >= 0
                possible_fall = fall
                assoc = self._associate_headwear((x1, y1, x2, y2), headwear_detections)
                if self.helmet_model is not None:
                    cap_good = bool(assoc and ("hat" in assoc[1] or "cap" in assoc[1]) and "no_" not in assoc[1] and "nohat" not in assoc[1])
                    cap_bad = (not cap_good) or demo["ppe"]
                else:
                    cap_good = False
                    cap_bad = demo["ppe"]
                cap_not_verified += int(cap_bad)
                color = (0, 0, 255) if inside or possible_fall or cap_bad else (0, 220, 0)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                label = "PERSON"
                if inside: label += " | RESTRICTED"
                if possible_fall: label += " | POSSIBLE FALL"
                label += " | CAP OK" if cap_good and not demo["ppe"] else " | CAP NOT VERIFIED"
                cv2.putText(frame, label, (x1, max(18, y1-8)), cv2.FONT_HERSHEY_SIMPLEX, .42, color, 2)

            if demo["ppe"] and workers == 0:
                cap_not_verified = 1

            # Visual heuristics are also throttled, then their last state is reused.
            if now - last_inference < 0.03:
                last_aerosol = self._visual_aerosol(analysis_frame) or demo["aerosol"]
                last_fire = self._visual_fire(analysis_frame) or demo["fire"]
            aerosol = last_aerosol or demo["aerosol"]
            fire = last_fire or demo["fire"]
            fall = fall or demo["fall"]
            self.prev_gray = cv2.cvtColor(analysis_frame, cv2.COLOR_BGR2GRAY)

            overlay = frame.copy(); cv2.fillPoly(overlay, [self.zone], (40, 100, 220)); frame = cv2.addWeighted(overlay, .10, frame, .90, 0)
            cv2.polylines(frame, [self.zone], True, (40, 170, 255), 2)
            cv2.putText(frame, "RESTRICTED ZONE", (self.zone[0][0] + 10, self.zone[0][1] - 10), 0, .55, (40, 170, 255), 2)
            status = f"People {workers} | Restricted {restricted} | Aerosol {aerosol} | Fire {fire} | Fall {fall} | Cap unverified {cap_not_verified}"
            cv2.putText(frame, status, (12, 25), 0, .40, (255, 255, 255), 1)
            cv2.putText(frame, f"FAST LIVE CAMERA | AI FPS {1.0/max(0.001, inference_interval):.0f}", (12, self.height-15), 0, .40, (255, 255, 255), 1)
            active_demo = [k.upper() for k, v in demo.items() if v]
            if active_demo:
                cv2.putText(frame, "DEMO: " + " | ".join(active_demo), (12, 52), 0, .50, (0, 220, 255), 2)

            with self.lock:
                self.state.update(workers=workers, restricted=restricted, aerosol=aerosol, fire=fire, fall=fall,
                                  helmet_not_verified=cap_not_verified,
                                  cap_not_verified=cap_not_verified,
                                  vision_confidence=(sum(confs)/len(confs)*100 if confs else self.state.get("vision_confidence", 0)),
                                  camera_ok=True, running=True)
            ok2, enc = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
            if ok2: self.latest_jpeg = enc.tobytes()

        cap.release(); self.running = False; self.camera_ok = False

    def mjpeg(self):
        placeholder = None
        while True:
            if self.latest_jpeg is not None:
                payload = self.latest_jpeg
            else:
                if placeholder is None:
                    canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
                    cv2.putText(canvas, "Camera starting...", (35, self.height//2), 0, 1.0, (220,220,220), 2)
                    ok, enc = cv2.imencode(".jpg", canvas); placeholder = enc.tobytes() if ok else b""
                payload = placeholder
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + payload + b"\r\n"
            time.sleep(.05)
