"""Synced room playout (1.5.0): every speaker in a room plays the room's
sound at the same moment. Wire protocol: docs/ROOM_AUDIO_PROTOCOL.md.

* ``protocol``: the wire format;
* ``clock``: the server clock and the reference clock fit;
* ``streamer``: one room's stamped timeline (music, mixed-in sounds,
  timed controls), and ``MusicFeed``: a host's music decoded once, read by
  every room following it (shared queues and groups play in step);
* ``sources``: the room's MPD stream, decoded;
* ``service``: ``RoomPlayout``, the per-room streamers and their speakers,
  and the ``RoomRegistry`` interface slice 2 provides;
* ``session``: the ``/v1/room-audio/{satellite_id}`` socket;
* ``ref_client``: the reference timed player.
"""
