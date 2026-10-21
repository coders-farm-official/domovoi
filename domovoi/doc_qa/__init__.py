"""Questions about an attached document: find, then answer (documents plan, part 3).

Pure functions with no I/O, shared in behaviour with the Android app's
``assistant/docs/DocQa.kt`` (the same rules, the same wording, and the
same test vectors: ``shared_cases.json`` beside this file, read by both
test suites):

* :mod:`.text` cuts a document's parts into passages of about 300 tokens
  on sentence boundaries, with one sentence of overlap, and tokenises
  words for the index (NFKC, lowercase, a short stop-word list, simple
  suffix stripping).
* :mod:`.bm25` ranks passages for a question (our own BM25; no library).
* :mod:`.answer` picks the mode from the wording (summary or question),
  builds the prompt (passages in a marked block of data, never
  instructions), and checks the reply: only citations of passages the app
  gave are kept, quotes must match their passage, and "I couldn't find
  that in the document" is recognised.

A *part* (what the extractors make, :mod:`domovoi.doc_extract`) is a dict
``{"label": "p. 7", "text": "...", "head": optional}``. ``head`` is
repeated at the top of every passage cut from the part (a table's header
row) and never counted against its size.
"""

from domovoi.doc_qa.answer import (  # noqa: F401
    NOT_FOUND,
    Checked,
    build_prompt,
    check_reply,
    choose_mode,
    retrieve,
    search_query,
)
from domovoi.doc_qa.text import Passage, cut_passages, tokens  # noqa: F401
