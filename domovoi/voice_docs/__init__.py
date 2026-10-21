"""Documents by voice, in any room (documents plan D7).

* :mod:`.reading` — "read me the lease": a household document found by
  name in Documents, its text cut into spoken parts (a page or so each),
  with the length said first for anything over about a page.
* :mod:`.letters` — "take a letter to the landlord": the room's dictation
  becomes a D4 draft (a chat with the letter in it), written by the QA
  model into D4's letter template, saved as .docx in Documents → From Chat
  and never sent anywhere.

The handler that owns the phrases is ``domovoi/handlers/documents.py``
(band 225: after chat_mode and printing, before voice_notes, whose
"write …" capture would otherwise take "write a letter to the landlord"
as a note).
"""
