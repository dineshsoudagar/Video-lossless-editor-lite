"""
Simple Video Editor (lossless trim + combine) — Python / PyQt6
==============================================================

🆕 Two export paths
1. **Re‑encode** (MoviePy) — safe when clips differ, lets you change resolution/codec, *but* recompresses.
2. **Lossless copy** (FFmpeg stream‑copy) — instant, no resize, zero quality loss, *but* **all clips must share
   the same codec, resolution, and framerate**.

Robust FFmpeg discovery → works even when `ffmpeg.exe` isn’t on `PATH`
(by falling back to the static binary bundled in *imageio‑ffmpeg* or an environment variable `FFMPEG_PATH`).

---------------------------------------------------------------------
Install
    python -m pip install --upgrade pip wheel
    python -m pip install PyQt6 moviepy imageio-ffmpeg
Run
    python simple_video_editor.py
---------------------------------------------------------------------
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from moviepy.video.VideoClip import ColorClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip

from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

# ---------------------------------------------------------------------------
# Optional imageio‑ffmpeg fallback
# ---------------------------------------------------------------------------
try:
    import imageio_ffmpeg  # type: ignore
except ImportError:
    imageio_ffmpeg = None  # not fatal

# ---------------------------------------------------------------------------
# MoviePy import that works on both 1.x and 2.x
# ---------------------------------------------------------------------------
try:
    from moviepy import VideoFileClip, concatenate_videoclips  # type: ignore
except ImportError:  # MoviePy 1.x namespace
    from moviepy.editor import VideoFileClip, concatenate_videoclips  # type: ignore


# ---------------------------------------------------------------------------
# Locate an FFmpeg executable we can call
# ---------------------------------------------------------------------------
def ffmpeg_exe() -> str:
    """Return a path to an FFmpeg executable or raise FileNotFoundError."""
    env_path = os.getenv("FFMPEG_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    path_in_path = shutil.which("ffmpeg")
    if path_in_path:
        return path_in_path

    if imageio_ffmpeg:
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()  # type: ignore[attr-defined]
        except Exception:
            pass

    raise FileNotFoundError(
        "FFmpeg executable not found. ① Install FFmpeg, or ② pip install imageio‑ffmpeg, "
        "or ③ set the FFMPEG_PATH environment variable."
    )


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def hms_to_seconds(t: str) -> float:
    parts = [float(p) for p in t.split(":")]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    raise ValueError("Bad time; use HH:MM:SS, MM:SS or SS")


def seconds_to_hms(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:d}:{m:02d}:{s:05.2f}" if h else f"{m:d}:{s:05.2f}"


# ---------------------------------------------------------------------------
@dataclass
class Clip:
    path: Path
    duration: float
    start: float = 0.0
    end: float = field(init=False)

    def __post_init__(self):
        self.end = self.duration

    def label(self) -> str:
        return f"{self.path.name}  [{seconds_to_hms(self.start)} → {seconds_to_hms(self.end)}]"


# Resolution presets: (label, (w, h)) – None ⇒ keep original
PRESETS: list[Tuple[str, Optional[Tuple[int, int]]]] = [
    ("Original", None),
    ("1080p (1920×1080)", (1920, 1080)),
    ("720p (1280×720)", (1280, 720)),
    ("480p (854×480)", (854, 480)),
    ("360p (640×360)", (640, 360)),
    ("240p (426×240)", (426, 240)),
]


# ---------------------------------------------------------------------------
class Editor(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Simple Video Editor — Lossless Trim & Combine")
        self.resize(840, 440)

        self.preset_combo = QComboBox()
        for label, _ in PRESETS:
            self.preset_combo.addItem(label)
        self.preset_combo.setCurrentIndex(0)  # default → “Original”

        self.clips: List[Clip] = []

        # ------------- widgets -------------
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._sync_fields)

        self.btn_add = QPushButton("Add Video…")
        self.btn_rm = QPushButton("Remove")
        self.btn_up = QPushButton("↑")
        self.btn_dn = QPushButton("↓")
        self.btn_exp_lossless = QPushButton("Export Lossless…")
        self.btn_exp_reenc = QPushButton("Export Re‑encode…")
        self.btn_exp_scale = QPushButton("Export FFmpeg Resize…")

        self.in_field = QLineEdit()
        self.out_field = QLineEdit()
        self.in_field.editingFinished.connect(self._apply_trim)
        self.out_field.editingFinished.connect(self._apply_trim)
        self.btn_exp_scale.clicked.connect(self._export_ffmpeg_scaled)

        # ------------- layout -------------
        btn_col = QVBoxLayout()
        for b in (
                self.btn_add, self.btn_rm, self.btn_up, self.btn_dn,
                self.btn_exp_lossless, self.btn_exp_scale, self.btn_exp_reenc
        ):
            btn_col.addWidget(b)
        btn_col.addStretch()

        form = QFormLayout()
        form.addRow(QLabel("Start (HH:MM:SS):"), self.in_field)
        form.addRow(QLabel("End   (HH:MM:SS):"), self.out_field)
        form.addRow(QLabel("Resize preset:"), self.preset_combo)
        right = QVBoxLayout()
        right.addLayout(form)
        right.addStretch()

        main = QHBoxLayout(self)
        main.addWidget(self.list_widget, 2)
        main.addLayout(btn_col)
        main.addLayout(right, 1)

        # ------------- connections -------------
        self.btn_add.clicked.connect(self._add)
        self.btn_rm.clicked.connect(self._remove)
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_dn.clicked.connect(lambda: self._move(1))
        self.btn_exp_lossless.clicked.connect(self._export_lossless)
        self.btn_exp_reenc.clicked.connect(self._export_reencode)

    # ------------------------------------------------------------------ UI helpers
    def _refresh_list(self):
        self.list_widget.clear()
        for c in self.clips:
            QListWidgetItem(c.label(), self.list_widget)

    def _sync_fields(self, row: int):
        if 0 <= row < len(self.clips):
            c = self.clips[row]
            self.in_field.setText(seconds_to_hms(c.start))
            self.out_field.setText(seconds_to_hms(c.end))
        else:
            self.in_field.clear()
            self.out_field.clear()

    def _apply_trim(self):
        idx = self.list_widget.currentRow()
        if not (0 <= idx < len(self.clips)):
            return
        try:
            start = hms_to_seconds(self.in_field.text())
            end = hms_to_seconds(self.out_field.text())
        except ValueError as e:
            QMessageBox.warning(self, "Time format", str(e))
            return
        clip = self.clips[idx]
        if not (0 <= start < end <= clip.duration):
            QMessageBox.warning(self, "Range", "0 ≤ start < end ≤ duration")
            return
        clip.start, clip.end = start, end
        self._refresh_list()
        self.list_widget.setCurrentRow(idx)

    # ------------------------------------------------------------------ actions
    def _add(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select video(s)",
            str(Path.home()),
            "Video (*.mp4 *.mov *.mkv *.avi *.webm);;All Files (*)",
        )
        for p in paths:
            try:
                vf = VideoFileClip(p)
                dur = vf.duration
                vf.close()
            except Exception as e:
                QMessageBox.warning(self, "Load failed", f"{p}\n{e}")
                continue
            self.clips.append(Clip(Path(p), dur))
        self._refresh_list()

    def _remove(self):
        idx = self.list_widget.currentRow()
        if 0 <= idx < len(self.clips):
            self.clips.pop(idx)
            self._refresh_list()

    def _move(self, delta: int):
        idx = self.list_widget.currentRow()
        new_idx = idx + delta
        if 0 <= idx < len(self.clips) and 0 <= new_idx < len(self.clips):
            self.clips[idx], self.clips[new_idx] = self.clips[new_idx], self.clips[idx]
            self._refresh_list()
            self.list_widget.setCurrentRow(new_idx)

    # ------------------------------------------------------------------ export helpers
    def _validate(self) -> bool:
        if not self.clips:
            QMessageBox.information(self, "Empty", "Add at least one clip first.")
            return False
        for c in self.clips:
            if not (0 <= c.start < c.end <= c.duration):
                QMessageBox.warning(self, "Trim error", f"Check in/out for {c.path.name}")
                return False
        return True

    def _export_ffmpeg_scaled(self):
        if not self._validate():
            return

        out, _ = QFileDialog.getSaveFileName(
            self,
            "Save down‑sized (FFmpeg)",
            str(Path.home() / "output_scaled.mp4"),
            "MP4 Video (*.mp4)",
        )
        if not out:
            return

        try:
            ff = ffmpeg_exe()
        except FileNotFoundError as e:
            QMessageBox.critical(self, "FFmpeg missing", str(e))
            return

        # target dimensions from preset
        _, dims = PRESETS[self.preset_combo.currentIndex()]
        if dims is None:
            QMessageBox.warning(self, "Resize preset", "Choose a preset other than Original.")
            return
        tgt_w, tgt_h = dims

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                parts = []
                for i, c in enumerate(self.clips):
                    part = Path(tmpdir) / f"part{i}.mp4"
                    vf = (
                        f"scale={tgt_w}:-2,"
                        f"pad={tgt_w}:{tgt_h}:(iw-{tgt_w})/2:(ih-{tgt_h})/2:black"
                    )
                    cmd = [
                        ff,
                        "-y",
                        "-ss",
                        str(c.start),
                        "-to",
                        str(c.end),
                        "-i",
                        str(c.path),
                        "-vf",
                        vf,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "18",
                        "-c:a",
                        "copy",
                        str(part),
                    ]
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
                    parts.append(part)

                # concat the re‑encoded parts (they now share codec/size/FPS)
                concat_txt = Path(tmpdir) / "concat.txt"
                concat_txt.write_text("\n".join(f"file '{p}'" for p in parts), encoding="utf-8")

                subprocess.run(
                    [
                        ff,
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(concat_txt),
                        "-c",
                        "copy",
                        str(out),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )

        except subprocess.CalledProcessError as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return

        QMessageBox.information(self, "Done", f"Exported down‑sized video:\n{out}")

    # ------------------------------------------------------------------ export paths
    def _export_lossless(self):
        if not self._validate():
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save lossless",
            str(Path.home() / "output_lossless.mp4"),
            "MP4 Video (*.mp4)",
        )
        if not out_path:
            return

        try:
            ff = ffmpeg_exe()
        except FileNotFoundError as e:
            QMessageBox.critical(self, "FFmpeg missing", str(e))
            return

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                parts: List[Path] = []
                for i, c in enumerate(self.clips):
                    part_file = Path(tmp_dir) / f"part{i}.mp4"
                    cmd_part = [
                        ff,
                        "-y",
                        "-ss",
                        str(c.start),
                        "-to",
                        str(c.end),
                        "-i",
                        str(c.path),
                        "-c",
                        "copy",
                        "-avoid_negative_ts",
                        "1",
                        str(part_file),
                    ]
                    res = subprocess.run(cmd_part, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    if res.returncode:
                        raise RuntimeError(res.stderr.decode() or "ffmpeg error")
                    parts.append(part_file)

                concat_txt = Path(tmp_dir) / "concat.txt"
                concat_txt.write_text("\n".join(f"file '{p}'" for p in parts), encoding="utf-8")

                cmd_concat = [
                    ff,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_txt),
                    "-c",
                    "copy",
                    str(out_path),
                ]
                res2 = subprocess.run(cmd_concat, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if res2.returncode:
                    raise RuntimeError(res2.stderr.decode() or "ffmpeg concat error")

        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return

        QMessageBox.information(self, "Done", f"Exported lossless:\n{out_path}")

    def _export_reencode(self):
        if not self._validate():
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save re‑encoded", str(Path.home() / "output.mp4"), "MP4 Video (*.mp4)"
        )
        if not out_path:
            return

        # chosen preset
        _, dims = PRESETS[self.preset_combo.currentIndex()]

        try:
            clips = []
            for c in self.clips:
                base = VideoFileClip(str(c.path))
                # trim
                if hasattr(base, "subclip"):
                    seg = base.subclip(c.start, c.end)
                elif hasattr(base, "subclipped"):
                    seg = base.subclipped(c.start, c.end)
                else:  # slice syntax in MoviePy 2.x
                    seg = base[c.start: c.end]

                # optional resize
                if dims is not None:  # dims = (target_w, target_h)
                    tgt_w, tgt_h = dims
                    src_w, src_h = seg.size

                    # scale factor that fits inside target
                    scale = min(tgt_w / src_w, tgt_h / src_h)
                    new_w, new_h = int(src_w * scale), int(src_h * scale)

                    # 1) resize
                    seg = seg.resized(width=new_w, height=new_h)

                    # 2) letter/pillar‑box if needed
                    if (new_w, new_h) != (tgt_w, tgt_h):
                        bg = (
                            ColorClip(size=(tgt_w, tgt_h), color=(0, 0, 0))
                            .with_duration(seg.duration)
                        )
                        seg = seg.with_position(("center", "center"))
                        seg = CompositeVideoClip([bg, seg])

                clips.append(seg)

            final = concatenate_videoclips(clips, method="compose")
            final.write_videofile(out_path, codec="libx264", audio_codec="aac")

        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
        finally:
            for s in locals().get("clips", []):
                s.close()

        QMessageBox.information(self, "Done", f"Exported (re‑encode):\n{out_path}")


# ---------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    gui = Editor()
    gui.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
