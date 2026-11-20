#!/usr/bin/env python3
"""Apply the Toynik AR fixes to every AR page in new/pages and old/pages.

  1. Model size: scale 0.5 -> 1 on ABC, Hindi and Numbers pages (animals and birds unchanged).
  2. Jitter: MindAR One Euro filter settings + a frame-rate independent smoothing
     container that follows the card, so the model glides instead of shaking.
  3. iOS camera start: autoStart off, a "Scan the <Name>" tap-to-start screen that
     starts the camera from the tap, clear camera error messages, inline muted
     camera video, a "Scan the <Name>" hint while the card is not in view, and
     the camera released when leaving the page.

Usage (from anywhere):
    python3 tools/ar-fix/patch.py            # patch all pages in place
    python3 tools/ar-fix/patch.py --check    # report what would change, write nothing

The script is idempotent: a page that already has the fix is skipped.
"""
import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MARK = "<!-- toynik-ar-fix v1 -->"

BIG_CATEGORIES = {"abc", "hindi", "numbers123"}   # fix 1 applies only here
NEW_SCALE = "1 1 1"

# Fix 2: MindAR tracking config (https://hiukim.github.io/mind-ar-js-doc/quick-start/tracking-config/)
FILTER_MIN_CF = os.environ.get("AR_FILTER_MIN_CF", "-1")   # -1 = MindAR default
FILTER_BETA = os.environ.get("AR_FILTER_BETA", "-1")
WARMUP = os.environ.get("AR_WARMUP", "5")
MISS = os.environ.get("AR_MISS", "10")
# Adaptive smoothing container (One Euro filter on direction + log-distance).
SMOOTH = os.environ.get("AR_SMOOTH", "1")                 # 0 disables the container
SIDE_MIN = os.environ.get("AR_SIDE_MIN", "0.25")           # Hz, sideways smoothing when still
SIDE_BETA = os.environ.get("AR_SIDE_BETA", "80")           # how fast it loosens when moving
DEPTH_MIN = os.environ.get("AR_DEPTH_MIN", "0.3")          # Hz, depth smoothing when still
DEPTH_BETA = os.environ.get("AR_DEPTH_BETA", "5")

PRETTY = {
    "chimpanzee": "Chimpanzee", "cock": "Cock",
}


def display_name(category, stem):
    if category == "abc":
        return f"Letter {stem.upper()}"
    if category == "numbers123":
        return f"Number {stem}"
    if category == "hindi":
        return f"Letter {stem}"
    return PRETTY.get(stem, stem.capitalize())


CSS = """
    /* ---- toynik-ar-fix: tap-to-start screen + scan hint ---- */
    .back-button { z-index: 300; top: calc(20px + env(safe-area-inset-top)); }
    #startOverlay {
      position: fixed; inset: 0; z-index: 200;
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      gap: 16px; padding: 24px; box-sizing: border-box;
      background: #000; color: #fff; text-align: center;
      font-family: Arial, sans-serif;
    }
    #startTitle { font-size: 26px; font-weight: bold; }
    #startBtn {
      padding: 16px 36px; border: none; border-radius: 30px;
      background: #ffcc00; color: #000; font-size: 18px; font-weight: bold;
      cursor: pointer; -webkit-tap-highlight-color: transparent;
    }
    #startBtn:disabled { opacity: 0.6; }
    #startMsg { font-size: 14px; opacity: 0.85; max-width: 320px; line-height: 1.4; }
    #scanLabel {
      position: fixed; z-index: 100; left: 50%; transform: translateX(-50%);
      bottom: calc(40px + env(safe-area-inset-bottom));
      max-width: calc(100vw - 32px); box-sizing: border-box;
      padding: 12px 22px; border-radius: 24px;
      background: rgba(0, 0, 0, 0.75); color: #fff;
      font: bold 18px Arial, sans-serif; text-align: center;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      pointer-events: none; display: none;
    }
"""

BODY_TOP = """
  <!-- toynik-ar-fix: iOS needs the camera to start from a tap -->
  <div id="startOverlay">
    <div id="startTitle">{scan}</div>
    <button id="startBtn" type="button" disabled>Loading...</button>
    <div id="startMsg">Tap Start, then point your camera at the {name} card</div>
  </div>
  <div id="scanLabel" role="status" aria-live="polite">{scan}</div>
"""

SCRIPT = """
  <!-- toynik-ar-fix: camera start, scan hint, smoothing -->
  <script>
    document.addEventListener('DOMContentLoaded', () => {
      const sceneEl = document.querySelector('a-scene');
      const target = document.querySelector('#target-entity');
      const follower = document.querySelector('#smooth-container');
      const overlay = document.querySelector('#startOverlay');
      const startBtn = document.querySelector('#startBtn');
      const startMsg = document.querySelector('#startMsg');
      const scanLabel = document.querySelector('#scanLabel');

      let arStarted = false;
      let targetVisible = false;
      let snap = true;   // first frame after the card is found: jump, don't glide

      // ---------- camera start (from a tap, for iOS) ----------
      function explain(err) {
        const name = (err && err.name) || '';
        if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          return 'The camera needs a secure https:// link. If you opened this inside WhatsApp or Instagram, open it in Safari or Chrome instead.';
        }
        if (name === 'NotAllowedError' || name === 'SecurityError') {
          return 'Camera permission was blocked. Allow the camera for this site (iPhone: Settings > Safari > Camera), then tap Try Again.';
        }
        if (name === 'NotReadableError' || name === 'AbortError') {
          return 'The camera is busy in another app. Close it and tap Try Again.';
        }
        if (name === 'NotFoundError' || name === 'OverconstrainedError') {
          return 'No camera was found on this device.';
        }
        return 'Could not start the camera' + (err && err.message ? ': ' + err.message : '.') + ' Tap Try Again.';
      }

      function fail(err) {
        console.error('AR start failed:', err);
        startBtn.disabled = false;
        startBtn.innerText = 'Try Again';
        startMsg.innerText = explain(err);
      }

      // MindAR creates the camera <video>; make sure iOS plays it inline and muted
      function fixCameraVideo() {
        document.querySelectorAll('video').forEach((v) => {
          v.muted = true;
          v.playsInline = true;
          v.setAttribute('muted', '');
          v.setAttribute('playsinline', '');
          v.setAttribute('webkit-playsinline', '');
          if (v.paused) v.play().catch(() => {});
        });
      }

      // Unlock the page's sound on iOS while we have a tap
      function unlockAudio() {
        document.querySelectorAll('audio').forEach((a) => {
          const wasMuted = a.muted;
          a.muted = true;
          const p = a.play();
          const reset = () => { a.pause(); a.currentTime = 0; a.muted = wasMuted; };
          if (p && p.then) p.then(reset, reset); else reset();
        });
      }

      async function startAR() {
        startBtn.disabled = true;
        startBtn.innerText = 'Starting...';
        startMsg.innerText = 'Allow camera access when asked';
        unlockAudio();

        if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          fail(null);
          return;
        }
        try {
          // Ask for the camera inside the tap so iOS shows the prompt and gives a clear error
          const probe = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: 'environment' } });
          probe.getTracks().forEach((t) => t.stop());
        } catch (err) {
          fail(err);
          return;
        }

        const onReady = () => {
          sceneEl.removeEventListener('arError', onError);
          arStarted = true;
          fixCameraVideo();
          overlay.remove();
          if (!targetVisible) scanLabel.style.display = 'block';
        };
        const onError = () => {
          sceneEl.removeEventListener('arReady', onReady);
          const v = document.querySelector('video');
          if (v && !v.srcObject) v.remove();
          fail({ name: 'NotReadableError' });
        };
        sceneEl.addEventListener('arReady', onReady, { once: true });
        sceneEl.addEventListener('arError', onError, { once: true });

        sceneEl.systems['mindar-image-system'].start();
        const video = document.querySelector('video');
        if (video) {
          fixCameraVideo();
          video.addEventListener('loadedmetadata', fixCameraVideo);
        }
      }

      startBtn.addEventListener('click', startAR);
      const enableStart = () => { startBtn.disabled = false; startBtn.innerText = 'Start'; };
      if (sceneEl.hasLoaded) enableStart(); else sceneEl.addEventListener('loaded', enableStart);

      // Release the camera when leaving the page so the next page can use it
      window.addEventListener('pagehide', () => {
        if (!arStarted) return;
        try { sceneEl.systems['mindar-image-system'].stop(); } catch (e) {}
      });
      // Coming back via the browser Back button: reload to start cleanly
      window.addEventListener('pageshow', (e) => { if (e.persisted) location.reload(); });

      // ---------- scan hint ----------
      target.addEventListener('targetFound', () => {
        targetVisible = true;
        scanLabel.style.display = 'none';
        if (follower) { snap = true; follower.setAttribute('visible', true); }
      });
      target.addEventListener('targetLost', () => {
        targetVisible = false;
        if (arStarted) scanLabel.style.display = 'block';
        if (follower) follower.setAttribute('visible', false);
      });

      // ---------- smoothing: the model follows the card's pose gently ----------
      // One Euro filter (adaptive): strong smoothing while the card is still (no
      // shake), light smoothing while it moves (no lag). Works on the direction
      // from the camera (what you see sideways) and on log-distance (depth), so
      // the settings behave the same at any card distance.
      if (!follower) return;
      const SIDE_MIN_HZ = __SIDE_MIN__, SIDE_BETA = __SIDE_BETA__;
      const DEPTH_MIN_HZ = __DEPTH_MIN__, DEPTH_BETA = __DEPTH_BETA__;
      const alpha = (hz, dt) => 1 / (1 + 1 / (2 * Math.PI * hz * dt));
      function OneEuro(minHz, beta, n) {
        this.x = null; this.dx = new Array(n).fill(0);
        this.step = (v, dt) => {
          if (!this.x) { this.x = v.slice(); return 0; }
          const ad = alpha(1, dt);
          let speed = 0;
          for (let i = 0; i < n; i++) {
            this.dx[i] += ad * ((v[i] - this.x[i]) / dt - this.dx[i]);
            speed += this.dx[i] * this.dx[i];
          }
          const a = alpha(minHz + beta * Math.sqrt(speed), dt);
          for (let i = 0; i < n; i++) this.x[i] += a * (v[i] - this.x[i]);
          return a;
        };
      }
      let side, depth, last = 0;
      const pos = new THREE.Vector3(), quat = new THREE.Quaternion(), scl = new THREE.Vector3();
      (function follow(now) {
        now = now || performance.now();
        const dt = Math.min(0.1, Math.max(0.001, (now - last) / 1000));
        last = now;
        if (targetVisible) {
          const t = target.object3D;
          const f = follower.object3D;
          t.updateMatrixWorld(true);
          t.matrixWorld.decompose(pos, quat, scl);
          const d = pos.length() || 1;
          if (snap) {
            side = new OneEuro(SIDE_MIN_HZ, SIDE_BETA, 3);
            depth = new OneEuro(DEPTH_MIN_HZ, DEPTH_BETA, 1);
            f.quaternion.copy(quat); f.scale.copy(scl);
            snap = false;
          }
          const a = side.step([pos.x / d, pos.y / d, pos.z / d], dt);
          depth.step([Math.log(d)], dt);
          if (a) { f.quaternion.slerp(quat, a); f.scale.lerp(scl, a); }
          f.position.set(side.x[0], side.x[1], side.x[2]).normalize().multiplyScalar(Math.exp(depth.x[0]));
        }
        requestAnimationFrame(follow);
      })();
    });
  </script>
"""


def patch(text, category, stem):
    if MARK in text:
        return None
    name = display_name(category, stem)
    scan = html.escape(f"Scan the {name}")
    out = text

    def sub1(pattern, repl, s, flags=0):
        new, n = re.subn(pattern, repl, s, count=1, flags=flags)
        if n != 1:
            raise ValueError(f"pattern not found: {pattern[:60]}")
        return new

    # Fix 1: model size
    if category in BIG_CATEGORIES:
        out = sub1(r'(<a-gltf-model\b[^>]*?\bscale=")0\.5 0\.5 0\.5(")', r"\g<1>" + NEW_SCALE + r"\2", out, re.S)

    # Fix 2 + 3: MindAR config (autoStart off, own scan hint, jitter filter)
    cfg = (f"autoStart: false; uiScanning: no; filterMinCF: {FILTER_MIN_CF}; "
           f"filterBeta: {FILTER_BETA}; warmupTolerance: {WARMUP}; missTolerance: {MISS};")
    out = sub1(r"autoStart: true;", cfg, out)

    # Fix 2: move the model into a smoothing container at the scene root
    if float(SMOOTH) > 0:
        out = sub1(
            r'(<a-entity id="target-entity" mindar-image-target="targetIndex: 0">)(\s*<a-gltf-model\b.*?</a-gltf-model>\s*)(</a-entity>)',
            r'\1\3\n\n    <!-- toynik-ar-fix: the model follows the card through this container (smooths jitter) -->\n'
            r'    <a-entity id="smooth-container" visible="false">\2</a-entity>',
            out, re.S)

    # Fix 3: page title, styles, overlay, script
    if "<title>" not in out:
        out = sub1(r'(<meta name="viewport"[^>]*>)', r"\1\n  <title>" + html.escape(name) + " - Toynik AR</title>", out)
    out = sub1(r"(</style>)", CSS.replace("\\", "\\\\") + r"  \1", out)
    out = sub1(r"(<body[^>]*>)", r"\1\n  " + MARK + BODY_TOP.format(scan=scan, name=html.escape(name)).replace("\\", "\\\\"), out)
    script = (SCRIPT.replace("__SIDE_MIN__", SIDE_MIN).replace("__SIDE_BETA__", SIDE_BETA)
              .replace("__DEPTH_MIN__", DEPTH_MIN).replace("__DEPTH_BETA__", DEPTH_BETA))
    idx = out.rindex("</body>")
    out = out[:idx] + script.lstrip("\n") + out[idx:]
    return out


def main():
    check = "--check" in sys.argv
    roots = [a for a in sys.argv[1:] if not a.startswith("--")] or ["new/pages", "old/pages"]
    changed = skipped = 0
    for r in roots:
        base = os.path.join(ROOT, r)
        for category in sorted(os.listdir(base)):
            cdir = os.path.join(base, category)
            if not os.path.isdir(cdir):
                continue
            for fn in sorted(os.listdir(cdir)):
                if not fn.endswith(".html"):
                    continue
                path = os.path.join(cdir, fn)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                new = patch(text, category, fn[:-5])
                if new is None:
                    skipped += 1
                    continue
                changed += 1
                if not check:
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(new)
    print(f"{'would patch' if check else 'patched'} {changed} page(s), {skipped} already fixed")


if __name__ == "__main__":
    main()
