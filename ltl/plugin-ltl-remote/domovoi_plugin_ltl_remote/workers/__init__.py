"""Background work for the LTL Remote plugin.

Two workers, deliberately different shapes:

* :class:`~domovoi_plugin_ltl_remote.link.RelayLink` is a
  ``LongRunWorker`` — it owns a persistent socket, so reconnection is
  its own business and the runner's restart policy is only a backstop.
* :class:`RemoteRetentionReaper` is a poll ``Worker`` — it wakes on a
  cadence, deletes expired rows, and goes back to sleep.
"""

from .reaper import RemoteRetentionReaper

__all__ = ["RemoteRetentionReaper"]
