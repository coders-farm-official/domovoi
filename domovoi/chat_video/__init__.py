"""Video in chat (1.4.0): attach a short video to a home-server chat, and
ask about it.

A household member attaches a video of at most three minutes to a chat.
The server watches it once, in the background, and keeps only text: a
transcript of what was said (with times) and a short description of a
handful of frames, worded as what each frame *might show*, plus a short
summary. Then it deletes the video and every frame it grabbed. Questions
about the video are answered from that text, in the same chat.

Nothing else: no library analysis, no Videos-tab search, no automatic or
background scanning of anything nobody attached.

Who does what:

* the web process (``web/backend/api/chat_videos.py``) takes the upload,
  checks it with ffprobe before accepting it (:mod:`.probe`, the limits in
  :mod:`.limits`), and queues a job row (V064) when the message is sent;
* the core (:mod:`.runner`) runs the jobs, one at a time and at low
  priority, because it holds the speech model and knows, in-process, when
  a voice turn is under way (:mod:`domovoi.voice_activity`): the job holds
  off from the wake word to the end of the reply, and resumes after it;
* the passes (:mod:`.media`, :mod:`.plan`): pass 0 finds scene changes and
  where people speak; pass 1 transcribes the speech in short pieces; pass 2
  describes a capped number of frames through the chat's vision model; the
  combine step writes a short summary. Every piece is saved the moment it
  is done, so a restart carries on from the last saved step;
* the chat (``web/backend/api/chat.py``) answers questions about the video
  from the saved text (:mod:`.context`).
"""
