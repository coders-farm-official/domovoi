"""LTL Remote — remote access for Domovoi, by Lazy Thumb Labs.

The household half of the system described in ``ltl/docs/ARCHITECTURE.md``:
holds an outbound link to the LTL relay, authenticates remote devices
end to end, and forwards allowlisted traffic to the Domovoi dashboard and
core over loopback.
"""

SLUG = "ltl_remote"
SCHEMA = "plugin_ltl_remote"
__version__ = "1.0.0"
