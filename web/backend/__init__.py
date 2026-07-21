"""Domovoi Web — local-network management UI backend.

Separate FastAPI process from the Domovoi server (port 6369). Reuses
the Domovoi server's Postgres connection via session_scope. Read
endpoints + a WebSocket at /ws/state for real-time updates.
"""
