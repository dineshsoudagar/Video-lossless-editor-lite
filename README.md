# Simple Video Editor — Trim, Combine & Down‑size

![app screenshot.png](data/screenshot.png)

Tiny PyQt app that lets you:

| Action | Backend | Notes |
|--------|---------|-------|
| **Lossless trim + concat** | FFmpeg `-c copy` | Instant, zero re‑encode. |
| **Down‑size via FFmpeg** | `scale_cuda / scale_npp / scale` → NVENC | GPU‑fast on NVIDIA, CPU fallback otherwise. |
| **Flexible re‑encode** | MoviePy | Any codec/res, but slower. |

---

## ✨ Features
* Add multiple clips, set **start / end** per clip.
* **Drag to reorder** or remove segments.
* Choose a **resolution preset** (Original / 1080p / 720p / 480p / 360p / 240p) before export.
* Three export buttons  
  * **Export Lossless…** – packet copy.  
  * **Export FFmpeg Resize…** – down‑size on GPU/CPU then concat.  
  * **Export Re‑encode…** – MoviePy pipeline (fallback if you need filters).

---

## 🚀 Quick start

```bash
git clone https://github.com/<your‑handle>/simple-video-editor.git
cd simple-video-editor
python -m venv .venv && .venv/Scripts/activate      # Win
pip install -r requirements.txt

# run
python video_editor.py
