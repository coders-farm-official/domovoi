"""Shared helpers for handlers and admin endpoints.

These modules live under ``domovoi/handlers/shared/`` rather
than directly under ``domovoi/`` to make their intent obvious:
they're utility functions that several handlers (and the
admin/web fan-out endpoints) reuse, not part of the handler
registration surface. Importing from here is one-way — nothing in
this package imports from individual handler modules.
"""
