"""Media understanding (1.5.0): what the house works out about its photos
and videos, in the background, voice first.

* :mod:`.catalog` — the ``media_catalog`` walker (stat() only);
* :mod:`.runner` — the ``media_understanding`` runner (chat videos first,
  then library work in the night window and the daytime trickle) and the
  analyser hooks the photo, video and face slices register;
* :mod:`.budget` — the adaptive per-video frame budget and the benchmark
  it stands on;
* :mod:`.ratelimit` — the 20 MB/s read bucket and partial fingerprints;
* :mod:`.switches` — the household's switches (household_settings).
* :mod:`.faces` — faces and people (off by default): storage, grouping,
  training, forget-my-face, the photo analyser and the video-frame hook;
  :mod:`.face_engine` (YuNet + SFace on ONNX Runtime, in numpy) and
  :mod:`.face_models` (the pinned fetch on opt-in).

Design: MEDIA-UNDERSTANDING §5 and §7. Core-internal: not offered to
plugins.
"""
