/* Browser camera + WebRTC media. Flask handles signaling; no WebSocket required. */
(() => {
  'use strict';
  function init() {
    const el = id => document.getElementById(id);
    const start = el('startCamera'), stop = el('stopCamera');
    const local = el('localVideo'), remote = el('processedVideo');
    if (!start || !stop || !local || !remote || !el('cameraStatus')) {
      console.error('[camera] Required camera controls are missing.');
      return;
    }
    if (start.dataset.bound === 'true') return;
    start.dataset.bound = 'true';
    let session = null;

    function message(text, error = false) {
      el('cameraStatus').textContent = text;
      el('cameraStatus').className = error ? 'bad' : '';
      console[error ? 'error' : 'info']('[camera]', text);
    }
    function badge(text, style = 'medium') {
      el('camera').textContent = text;
      el('camera').className = 'big ' + style;
    }
    function preview(mode) {
      local.hidden = mode !== 'local';
      remote.hidden = mode !== 'remote';
      el('previewLabel').textContent = mode === 'remote'
        ? 'AI processed video' : 'Local camera preview — AI results pending';
    }
    function active(s) { return session === s && !s.cancelled; }
    function timer(s, fn, ms) {
      const id = setTimeout(() => { s.timers.delete(id); if (active(s)) fn(); }, ms);
      s.timers.add(id);
      return id;
    }
    function clearTimer(s, id) { clearTimeout(id); s.timers.delete(id); }
    function release(token) {
      if (!token) return;
      fetch('/api/webrtc/stop', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token}), keepalive: true
      }).catch(error => console.warn('[camera] Server cleanup will use its timeout.', error));
    }
    function closeConnection(s) {
      for (const id of s.timers) clearTimeout(id);
      s.timers.clear();
      if (s.pc) { s.pc.close(); s.pc = null; }
      release(s.token); s.token = null;
      remote.srcObject = null;
    }
    function stopCamera(text = 'Camera stopped.') {
      const s = session; session = null;
      if (s) {
        s.cancelled = true;
        // Abort config fetches, but let an in-flight offer return its cleanup token.
        for (const controller of s.requests) controller.abort();
        closeConnection(s);
        if (s.stream) s.stream.getTracks().forEach(track => track.stop());
      }
      local.srcObject = null; remote.srcObject = null;
      preview('local'); start.disabled = false; start.textContent = 'Start camera';
      stop.disabled = true; badge('OFFLINE', 'bad'); message(text);
    }
    function fail(s, error) {
      if (!active(s) || s.failed) return;
      s.failed = true;
      console.error('[camera] Startup/stream failure:', error);
      closeConnection(s);
      const descriptions = {
        NotAllowedError: 'Camera permission blocked. Allow Camera in browser site settings, then retry. Open this page directly, not in an embedded preview.',
        NotFoundError: 'No camera was found on this device.',
        NotReadableError: 'Camera is unavailable. Close other apps using it and retry.',
        OverconstrainedError: 'This camera cannot satisfy the requested video settings.',
        SecurityError: 'Camera access is disabled by browser or page permissions.'
      };
      preview('local');
      badge(s.stream ? 'LOCAL ONLY' : 'OFFLINE', s.stream ? 'medium' : 'bad');
      message(descriptions[error.name] || error.message || 'Camera startup failed.', true);
      start.disabled = false; start.textContent = 'Retry camera';
      stop.disabled = !s.stream;
    }
    async function jsonRequest(s, url, options = {}, timeout = 10000, keepOffer = false) {
      const controller = new AbortController();
      if (!keepOffer) s.requests.add(controller);
      const id = setTimeout(() => controller.abort(), timeout);
      try {
        const response = await fetch(url, {...options, signal: controller.signal, cache: 'no-store'});
        const body = await response.json().catch(() => null);
        if (!response.ok) throw new Error((body && body.error) || `Server returned HTTP ${response.status} for ${url}.`);
        if (!body) throw new Error(`Invalid server response from ${url}. Check the deployed backend version.`);
        return body;
      } catch (error) {
        if (error.name === 'AbortError') throw new Error(`Request timed out or was cancelled: ${url}`);
        throw error;
      } finally { clearTimeout(id); s.requests.delete(controller); }
    }
    function gatherIce(pc) {
      return new Promise((resolve, reject) => {
        const id = setTimeout(() => done(new Error('ICE gathering timed out. Check STUN/TURN configuration.')), 15000);
        function done(error) {
          clearTimeout(id);
          pc.removeEventListener('icegatheringstatechange', check);
          pc.removeEventListener('connectionstatechange', check);
          error ? reject(error) : resolve();
        }
        function check() {
          if (pc.connectionState === 'closed') done(new Error('Connection was cancelled.'));
          else if (pc.iceGatheringState === 'complete') done();
        }
        pc.addEventListener('icegatheringstatechange', check);
        pc.addEventListener('connectionstatechange', check);
        check();
      });
    }
    async function startCamera() {
      console.info('[camera] Start button clicked');
      if (session && !session.failed) return;
      if (session) stopCamera();
      const s = {cancelled: false, failed: false, stream: null, pc: null, token: null,
        timers: new Set(), requests: new Set(), remotePlaying: false, backendError: ''};
      session = s;
      start.disabled = true; stop.disabled = false;
      badge('PERMISSION'); message('Allow camera access in your browser…');
      try {
        if (!window.isSecureContext) throw new Error('Camera access requires HTTPS or localhost.');
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)
          throw new Error('This browser does not support camera access. Open the HTTPS page in Chrome, Edge, Firefox or Safari.');
        // Run directly from the click handler, before any network request.
        const pendingStream = navigator.mediaDevices.getUserMedia({audio: false, video: {
          width: {ideal: 640}, height: {ideal: 360}, frameRate: {ideal: 10}, facingMode: {ideal: 'user'}
        }});
        const permissionHint = timer(s, () => message('Still waiting for camera permission. Check the camera icon beside the address bar, or press Stop.'), 12000);
        const stream = await pendingStream;
        clearTimer(s, permissionHint);
        if (!active(s)) { stream.getTracks().forEach(track => track.stop()); return; }
        s.stream = stream;
        local.srcObject = stream; local.muted = true; preview('local');
        local.play().catch(error => { if (active(s)) message('Camera opened. Tap the preview to play video.'); console.warn('[camera] Local playback:', error); });
        badge('LOCAL ON'); message('Camera opened. Connecting to the AI server…');
        stream.getVideoTracks().forEach(track => track.addEventListener('ended', () => {
          if (active(s)) stopCamera('Camera access ended. Press Start to retry.');
        }));
        if (!window.RTCPeerConnection) throw new Error('WebRTC is unavailable in this browser. Local preview only.');
        const config = await jsonRequest(s, '/api/webrtc/config');
        if (!active(s)) return;
        if (!Array.isArray(config.iceServers)) throw new Error('The server returned invalid ICE settings.');
        if (!config.iceServers.length) console.warn('[camera] No ICE servers configured. Railway connections usually require a TURN relay.');
        const pc = new RTCPeerConnection({iceServers: config.iceServers}); s.pc = pc;
        pc.addEventListener('iceconnectionstatechange', () => console.info('[camera] ICE:', pc.iceConnectionState));
        pc.addEventListener('icecandidateerror', event => console.warn('[camera] ICE server:', event.errorCode, event.errorText));
        let disconnectedTimer = null;
        pc.addEventListener('connectionstatechange', () => {
          if (!active(s) || s.failed) return;
          console.info('[camera] WebRTC:', pc.connectionState);
          if (pc.connectionState === 'connected') {
            if (disconnectedTimer) clearTimer(s, disconnectedTimer);
            if (!s.remotePlaying) { badge('AI PENDING'); message('Video connection established. Waiting for the first AI frame…'); }
          } else if (pc.connectionState === 'failed') {
            fail(s, new Error('WebRTC connection failed. Your camera is working locally; configure a reachable TURN relay on Railway.'));
          } else if (pc.connectionState === 'disconnected') {
            if (disconnectedTimer) clearTimer(s, disconnectedTimer);
            disconnectedTimer = timer(s, () => fail(s, new Error('Video connection was lost. Press Retry camera.')), 10000);
          }
        });
        pc.addEventListener('track', event => {
          if (!active(s) || s.failed || event.track.kind !== 'video') return;
          remote.srcObject = event.streams[0] || new MediaStream([event.track]);
          remote.muted = true;
          // Reveal only after a decoded frame arrives; local preview remains visible meanwhile.
          remote.play().catch(error => {
            if (active(s)) { preview('remote'); message('Tap the video to play AI results.'); }
            console.warn('[camera] Remote playback:', error);
          });
        });
        stream.getTracks().forEach(track => pc.addTrack(track, stream));
        badge('CONNECTING');
        await pc.setLocalDescription(await pc.createOffer());
        await gatherIce(pc);
        if (!active(s)) return;
        message('Sending camera connection request to Flask…');
        const answer = await jsonRequest(s, '/api/webrtc/offer', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({type: pc.localDescription.type, sdp: pc.localDescription.sdp})
        }, 35000, true);
        if (!active(s) || s.failed) { release(answer.token); return; }
        s.token = answer.token;
        if (answer.type !== 'answer' || typeof answer.sdp !== 'string') throw new Error('Flask returned an invalid WebRTC answer.');
        await pc.setRemoteDescription({type: answer.type, sdp: answer.sdp});
        if (!active(s) || s.failed) return;
        timer(s, () => {
          if (!s.remotePlaying) fail(s, new Error(s.backendError ||
            'No AI video received. Local camera works; check Railway logs, model loading and TURN connectivity.'));
        }, 60000);
      } catch (error) { fail(s, error); }
    }
    remote.addEventListener('playing', () => {
      if (!session || session.failed || !remote.srcObject || remote.videoWidth === 0) return;
      session.remotePlaying = true; preview('remote'); badge('ONLINE', 'low'); message('Live AI results.');
    });
    for (const video of [local, remote]) video.addEventListener('click', () => {
      video.play().catch(error => message('Video playback failed: ' + error.message, true));
    });
    start.addEventListener('click', startCamera);
    stop.addEventListener('click', () => stopCamera());
    window.addEventListener('pagehide', () => stopCamera());
    // Permission dialogs/mobile browsers can change visibility: never cancel startup for that.
    window.IndustrialCamera = {
      updateServerState(state) {
        if (!session || session.failed) return;
        session.backendError = state.camera_error || '';
        if (session.backendError) message('AI server: ' + session.backendError, true);
      }
    };
    start.disabled = false; stop.disabled = true;
    message('Camera controls ready. Click Start camera.');
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
})();
