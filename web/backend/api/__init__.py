"""Route modules for the web backend.

Stub layout — each module owns a slice of the API surface (music,
people, satellites, calendar). Each exports both an ``APIRouter``
and a ``snapshot()`` coroutine that the realtime poll loop composes.
"""
