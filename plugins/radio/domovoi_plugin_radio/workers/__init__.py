"""Background workers for the radio plugin.

Poll workers implement only ``tick()`` — the core ``WorkerRunner`` owns
the loop, start/stop, and status surfacing (design §4.5). The FCC
import and simulcast backfill are startup hooks registered in
``core.register()``.
"""

from domovoi_plugin_radio.workers.detections_reaper import (  # noqa: F401
    RadioDetectionsReaper,
)
from domovoi_plugin_radio.workers.icy_poller import RadioIcyPoller  # noqa: F401
from domovoi_plugin_radio.workers.sampler import RadioSampler  # noqa: F401
from domovoi_plugin_radio.workers.track_fingerprinter import (  # noqa: F401
    TrackFingerprinter,
)
