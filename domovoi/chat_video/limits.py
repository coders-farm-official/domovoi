"""What a chat video may be, and the words for what it may not.

Shared by the web process (the upload's checks) and the core (the job),
stdlib only.

The numbers, and why:

* **3 minutes** (owner decision 2026-11-16). Longer is refused at once.
* **2 GiB per file.** A phone records 4K at 30 fps at about 50 Mbit/s and at
  60 fps at 80-90 Mbit/s, so three minutes of the heaviest ordinary phone
  video is about 1.8-1.9 GB (a 3-minute 4K clip from a current phone is
  "over 1 GB", as the brief says). 2 GiB takes every one of those, and a
  file bigger than that can't be three minutes of phone video: it is a
  camera's raw or professional format, which chat doesn't read anyway. The
  file is only on the server while it is being watched (minutes), then
  deleted, so the cap bounds a short-lived use of disk, not storage.
* **12 frames at most** per video. Each frame is one call to the vision
  model, expected to take roughly 10-30 s on a CPU-only server for the
  default qwen3-vl:2b (a few hundred image tokens in, about 60 words out),
  so 12 frames keep the whole job to a few minutes. Home video changes scene rarely:
  12 is one frame per 15 s of a 3-minute clip, after near-duplicates are
  dropped, which covers each scene of a typical clip. The descriptions
  then fit beside a 3-minute transcript in one question's prompt.
* **9-second speech pieces.** Speech is transcribed in pieces of at most 9
  seconds (cut at the quietest moment), the length the speech model
  decodes on its short, fast window (clients/whisper.py), so the step
  already under way when someone says the wake word ends within about a
  second, while they are still talking, and no piece ever asks the model
  for timestamps (the cause of the 2026-11 endless-decode incident).
"""

from __future__ import annotations

from pathlib import Path

#: The longest video chat takes, in seconds (owner decision 2026-11-16).
MAX_DURATION_SEC = 180.0
#: A little slack over :data:`MAX_DURATION_SEC` for a container's rounding
#: (a phone's "3:00" clip can probe as 180.02 s).
DURATION_SLACK_SEC = 0.5
#: The largest video file chat takes (see the module docstring).
MAX_BYTES = 2 * 1024 * 1024 * 1024
#: One video per message.
MAX_VIDEOS_PER_MESSAGE = 1
#: A staged video no message took is removed after this long.
UNSENT_TTL_SEC = 3600
#: The most frames described per video.
MAX_FRAMES = 12
#: The fewest frames worth describing (when the video is long enough).
MIN_FRAMES = 3
#: A frame is added where no scene change came for this long.
FILL_EVERY_SEC = 30.0
#: The longest piece of speech sent to the speech model at once.
SPEECH_PIECE_SEC = 9.0
#: How many times a job may be started (a restart in the middle counts);
#: past this it fails cleanly rather than loop on a video that stops the
#: server.
MAX_ATTEMPTS = 3

#: File name endings the picker offers and the upload takes.
EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm")
#: What ffprobe calls the containers those are (its ``format_name``).
CONTAINERS = frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm"})
#: The picture codecs phones record (and a browser's screen recorder).
VIDEO_CODECS = frozenset({"h264", "hevc", "vp8", "vp9", "av1"})
#: For the composer's file picker.
ACCEPT = "video/mp4,video/quicktime,video/webm,.mp4,.m4v,.mov,.webm"

#: Where staged and queued videos wait (deleted once watched), and the
#: job's temporary frames and audio. Never inside a media library folder.
VIDEOS_DIR = Path.home() / ".domovoi" / "chat_uploads" / "videos"
WORK_DIR = VIDEOS_DIR / "work"

# ─── Words (each refusal is a sentence, with a help-desk code) ──────────
TOO_LONG = "Videos up to 3 minutes can be attached."
TOO_LARGE = "Videos up to 2 GB can be attached. Send a shorter clip, or record it at a lower quality."
UNSUPPORTED = ("Chat takes MP4, MOV and WebM videos (H.264 or HEVC, the way phones record them). "
               "Share the video from your phone as MP4 and try again.")
UNREADABLE = "Domovoi couldn't read that video. It may be damaged, or still being saved on the phone."
NO_ROOM = "The home server is short of disk space for that video. Free some space and try again."
NO_TOOLS = "This home server can't read videos: it needs ffmpeg, which isn't installed."

#: Help-desk codes (domovoi/help_desk/error_codes.py has their entries).
CODE_TOO_LONG = "video_too_long"
CODE_TOO_LARGE = "video_too_large"
CODE_UNSUPPORTED = "video_unsupported"
CODE_UNREADABLE = "video_unreadable"
CODE_NO_ROOM = "video_no_room"
CODE_MISSING = "video_missing"
CODE_INTERRUPTED = "video_interrupted"
CODE_FAILED = "video_failed"


def valid_token(token: str | None) -> bool:
    return len(token or "") == 32 and all(c in "0123456789abcdef" for c in token or "")


def valid_job_id(job_id: str | None) -> bool:
    return valid_token(job_id)


def video_path(token: str) -> Path | None:
    """The stored video for ``token``, or None."""
    if not valid_token(token):
        return None
    for ext in EXTENSIONS:
        p = VIDEOS_DIR / f"{token}{ext}"
        if p.is_file():
            return p
    return None


def meta_path(token: str) -> Path:
    return VIDEOS_DIR / f"{token}.json"


def work_dir(job_id: str) -> Path:
    return WORK_DIR / job_id


def clock(seconds: float | int | None) -> str:
    """``2:05`` for 125 s (``1:02:05`` past an hour, never for chat)."""
    s = max(0, int(round(float(seconds or 0))))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def too_long_text(duration: float) -> str:
    return f"{TOO_LONG} This one is {clock(duration)} long."


def extension_ok(name: str) -> bool:
    return Path(name or "").suffix.lower() in EXTENSIONS
