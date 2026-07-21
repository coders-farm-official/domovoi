"""DB session shim — re-exports the Domovoi server's async session
factory so the web backend can run a single connection pool against
the same Postgres the Domovoi server uses.

Why re-export rather than create a parallel pool: a single shared
pool keeps the connection budget bounded, surfaces DB problems in
one place, and ensures schema migrations applied for the Domovoi server
are applied for the web (since they're literally the same database).
"""

from __future__ import annotations

from domovoi.db.session import SessionLocal, engine, session_scope

__all__ = ["SessionLocal", "engine", "session_scope"]
