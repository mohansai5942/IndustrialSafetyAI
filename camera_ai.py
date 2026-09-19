"""Computer vision layer for the Industrial Safety AI SIH prototype.

The camera layer is deliberately conservative:
- YOLO11n detects people.
- A dedicated headwear model detects *hat/cap* (not helmet) when available.
- Fall detection uses temporal person geometry instead of a single-frame box ratio.
- Aerosol and fire use temporal visual heuristics with persistence.

All detections are decision-support signals, not certified safety instruments.
"""
import time, threading
import asyncio
import logging
import secrets
from contextlib import suppress
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer, VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame
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
        self.zone = np.array([(0.625*width, 0.22*height), (0.98*width, 0.22*height),
                              (0.98*width, 0.96*height), (0.625*width, 0.96*height)], np.int32)
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
        self.model = None
        self.process_lock = threading.Lock()
        self.last_processed = 0.0

    def set_demo(self, key, enabled):
        if key not in self.demo:
            return
        with self.demo_lock:
            self.demo[key] = bool(enabled)

    def _demo_state(self):
        with self.demo_lock:
            return dict(self.demo)

    def stop(self):
        # Serialize with inference so an old connection cannot overwrite a new one.
        with self.process_lock:
            self.prev_gray = None
            self.person_tracks = []
            self.aerosol_hits = self.fire_hits = 0
            self.latest_jpeg = None
            self.last_processed = 0.0
            self.fps = 0.0
            self.running = self.camera_ok = False
            with self.lock:
                self.state.update(camera_ok=False, running=False, workers=0,
                                  restricted=0, aerosol=False, fire=False, fall=False,
                                  helmet_not_verified=0, cap_not_verified=0,
                                  vision_confidence=0)

    def process_frame(self, frame):
        """Process one BGR frame received from WebRTC; never open a server webcam."""
        with self.process_lock:
            try:
                return self._process_frame(frame)
            except Exception:
                with self.lock:
                    self.state.update(camera_ok=False, running=False,
                                      camera_error="Vision processing failed. Check server logs/model files.")
                raise

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

    def _process_frame(self, frame):
        if self.model is None:
            if not Path(self.model_path).is_file():
                raise FileNotFoundError("Provision models/yolo11n.pt before starting the server")
            self.model = YOLO(self.model_path)
            if self.cap_model_path.is_file():
                try:
                    self.helmet_model = YOLO(str(self.cap_model_path))
                except Exception:
                    logging.exception("Optional cap model could not be loaded")
                    with self.lock:
                        self.state["cap_model_error"] = "Optional cap model unavailable"
        frame = cv2.resize(frame, (self.width, self.height))
        analysis_frame = frame.copy()
        now = time.monotonic()
        self.fps = 1 / max(.001, now - self.last_processed) if self.last_processed else 0
        self.last_processed = now
        demo = self._demo_state()
        people, headwear_detections, confs = [], [], []
        cap_not_verified = 0
        result = self.model.predict(frame, imgsz=416, conf=.35, device="cpu", verbose=False)[0]
        if result.boxes is not None:
            for b in result.boxes:
                if int(b.cls[0]) != 0:
                    continue
                conf = float(b.conf[0])
                confs.append(conf)
                people.append((*map(int, b.xyxy[0].tolist()), conf))
        fall = self._temporal_fall(people)
        if self.helmet_model is not None and people:
            result = self.helmet_model.predict(frame, imgsz=416, conf=.30, device="cpu", verbose=False)[0]
            if result.boxes is not None:
                for b in result.boxes:
                    cls_id = int(b.cls[0])
                    name = str(self.helmet_model.names[cls_id]).lower().replace("-", "_").replace(" ", "_")
                    headwear_detections.append((*map(int, b.xyxy[0].tolist()), name, float(b.conf[0])))
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

        aerosol = self._visual_aerosol(analysis_frame) or demo["aerosol"]
        fire = self._visual_fire(analysis_frame) or demo["fire"]
        fall = fall or demo["fall"]
        self.prev_gray = cv2.cvtColor(analysis_frame, cv2.COLOR_BGR2GRAY)

        overlay = frame.copy(); cv2.fillPoly(overlay, [self.zone], (40, 100, 220)); frame = cv2.addWeighted(overlay, .10, frame, .90, 0)
        cv2.polylines(frame, [self.zone], True, (40, 170, 255), 2)
        cv2.putText(frame, "RESTRICTED ZONE", (self.zone[0][0] + 10, self.zone[0][1] - 10), 0, .55, (40, 170, 255), 2)
        status = f"People {workers} | Restricted {restricted} | Aerosol {aerosol} | Fire {fire} | Fall {fall} | Cap unverified {cap_not_verified}"
        cv2.putText(frame, status, (12, 25), 0, .40, (255, 255, 255), 1)
        cv2.putText(frame, f"BROWSER CAMERA | AI FPS {self.fps:.1f}", (12, self.height-15), 0, .40, (255, 255, 255), 1)
        active_demo = [k.upper() for k, v in demo.items() if v]
        if active_demo:
            cv2.putText(frame, "DEMO: " + " | ".join(active_demo), (12, 52), 0, .50, (0, 220, 255), 2)

        with self.lock:
            self.state.update(workers=workers, restricted=restricted, aerosol=aerosol, fire=fire, fall=fall,
                              helmet_not_verified=cap_not_verified,
                              cap_not_verified=cap_not_verified,
                              vision_confidence=(sum(confs)/len(confs)*100 if confs else 0),
                              camera_ok=True, running=True, camera_error="")
        ok2, enc = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        if not ok2:
            raise RuntimeError('JPEG encoding failed')
        self.latest_jpeg = enc.tobytes()
        self.running = self.camera_ok = True
        return frame



class ProcessedVideoTrack(VideoStreamTrack):
    """Drain incoming video continuously; keep only the latest unprocessed frame."""
    def __init__(self, source, camera):
        super().__init__()
        self.source, self.camera = source, camera
        self.pending = asyncio.Queue(maxsize=1)
        self.last_received = time.monotonic()
        self.processing = None
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            while True:
                frame = await self.source.recv()
                self.last_received = time.monotonic()
                if self.pending.full():
                    self.pending.get_nowait()
                self.pending.put_nowait(frame)
        except (MediaStreamError, asyncio.CancelledError):
            pass
        finally:
            if self.pending.full():
                self.pending.get_nowait()
            self.pending.put_nowait(None)

    async def recv(self):
        frame = await self.pending.get()
        if frame is None:
            raise MediaStreamError
        try:
            self.processing = asyncio.create_task(asyncio.to_thread(
                self.camera.process_frame, frame.to_ndarray(format="bgr24")))
            processed = await asyncio.shield(self.processing)
        except Exception:
            logging.exception("Vision inference failed")
            self.stop()
            raise MediaStreamError
        result = VideoFrame.from_ndarray(processed, format="bgr24")
        result.pts, result.time_base = frame.pts, frame.time_base
        return result

    def stop(self):
        super().stop()
        self.reader.cancel()
        self.source.stop()


class CameraBusy(Exception):
    pass


class WebRTCService:
    """One publisher for the existing shared monitoring station.

    All peer operations run on one persistent asyncio loop, independent of Flask
    request threads. Deploy with one process/replica (no Gunicorn --preload).
    """
    def __init__(self, camera, ice_servers):
        self.camera = camera
        self.ice_servers = ice_servers
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.peer = None
        self.token = None
        self.output = None
        self.watchdog = None
        self.closing = False
        self.thread.start()

    def submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    async def offer(self, sdp):
        if self.peer is not None:
            raise CameraBusy("Another browser is already publishing to this station")
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[
            RTCIceServer(**item) for item in self.ice_servers]))
        self.peer = pc
        self.token = secrets.token_urlsafe(32)
        self.watchdog = asyncio.create_task(self._watch(pc))

        @pc.on("track")
        def on_track(track):
            if track.kind == "video" and self.output is None:
                self.output = ProcessedVideoTrack(track, self.camera)
                pc.addTrack(self.output)
            else:
                track.stop()

        @pc.on("connectionstatechange")
        async def state_changed():
            if pc.connectionState in ("failed", "closed"):
                await self.close(pc)

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
            if self.output is None:
                raise ValueError("Offer must contain a video track")
            await pc.setLocalDescription(await pc.createAnswer())
            return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type,
                    "token": self.token}
        except BaseException:
            await self.close(pc)
            raise

    async def _watch(self, pc):
        started = time.monotonic()
        while self.peer is pc:
            await asyncio.sleep(2)
            stale = self.output is not None and time.monotonic() - self.output.last_received > 30
            ended = self.output is not None and self.output.readyState == "ended"
            unconnected = pc.connectionState != "connected" and time.monotonic() - started > 45
            if stale or ended or unconnected:
                await self.close(pc)
                return

    async def stop(self, token):
        if not self.token or not secrets.compare_digest(token, self.token):
            return False
        await self.close(self.peer)
        return True

    async def close(self, pc):
        if pc is not self.peer or self.closing:
            return
        self.closing = True
        try:
            if self.watchdog and self.watchdog is not asyncio.current_task():
                self.watchdog.cancel()
            if self.output:
                self.output.stop()
                with suppress(asyncio.CancelledError):
                    await self.output.reader
                if self.output.processing:
                    with suppress(Exception, asyncio.CancelledError):
                        await self.output.processing
            await pc.close()
            # Wait for any in-flight inference before another publisher is admitted.
            await asyncio.to_thread(self.camera.stop)
        finally:
            self.peer = self.output = self.token = self.watchdog = None
            self.closing = False
