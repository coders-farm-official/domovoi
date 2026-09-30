"""Shared vocabulary for the per-handler LLM tool-offer gate.

``Handler.offers_tool`` withholds a handler's ``tool_schema`` from the
LLM router for utterances that provably can't be that handler's — a
small tool model on a CPU host reaches for the nearest schema it is
shown rather than answering with no tool at all (F-V002/F-V004).

Several handlers need the same first half of that judgement: "is this a
bare knowledge question?". Keeping the pattern here means the two gates
that use it can't drift apart — a handler added to the withhold list
later gets exactly the openers that were measured, not a fresh
approximation of them.

The router asks the same question one level up (``answers_without_tools``):
a plain question about the world, or a request for a joke or a story, is
not shown to the tool model at all — it goes straight to the Q&A model.
That is cheaper than offering it a trimmed tool list (no router call, and
no change to the tool list whose prompt prefix Ollama caches), and no
bait tool is on offer to be picked.
"""

from __future__ import annotations

import re

# The polite wrappers Whisper transcribes in full ("tell me who wrote the
# odyssey"). They carry "me"/"you", so the cue search below skips them.
_LEAD_IN = r"(?:(?:tell me|do you know|do you happen to know|any idea|i wonder)[,\s]+)?"

# Question openers that never front a command. Deliberately NOT "what",
# "how", "when": those front real handler turns ("what is a third of
# ninety", "how many songs do i have", "what did i add today", "when is
# thanksgiving").
KNOWLEDGE_QUESTION_RE = re.compile(r"^" + _LEAD_IN + r"(?:who|whose|whom|why|where)\b")
_LEAD_IN_RE = re.compile(r"^" + _LEAD_IN)

# Anything that could make an utterance a question ABOUT THE COLLECTION
# rather than about the world: a media noun, or an ownership/curation
# verb. Generous on purpose — a false positive only means the library
# schema is offered to the tool model, exactly as it always was.
MUSIC_CUE_RE = re.compile(
    r"\b(?:"
    r"librar\w*|collection|music|song|songs|track|tracks|album|albums"
    r"|artist\w*|band|bands|record|records|recording\w*|playlist\w*"
    r"|discograph\w*|mp3|vinyl|cover|covers|remix\w*"
    r"|sing|sings|singer\w*|sang|sung|perform\w*|play\w*"
    r"|have|got|own|owns|downloaded|saved|added|add"
    r")\b"
)

# What makes a question about THIS household rather than about the world:
# the speaker or the assistant ("my", "you"), something pointed at
# ("this", "that", "it"), or the name of something Domovoi runs (plus
# MUSIC_CUE_RE). Any one of them sends the utterance to the tool model as
# before — "why is the wifi so slow", "where did I leave off", "who sings
# creep". Kept to words that rarely turn up in trivia: a false positive
# only costs a router call.
_ABOUT_THE_HOUSE_RE = re.compile(
    r"\b(?:"
    # the speaker, the household, the assistant
    r"i|i'm|im|i've|i'd|i'll|me|my|mine|myself|we|we're|our|ours|us"
    r"|you|your|yours|you're|youre|yourself|domovoi"
    # something pointed at
    r"|this|that|that's|these|those|here|it|its|it's"
    # what Domovoi runs
    r"|wi-?fi|internet|network|speakers?|satellites?|volume|timers?|alarms?"
    r"|reminders?|remind|notes?|memos?|news|headlines?|briefing"
    r"|podcasts?|episodes?|audiobooks?|chapters?|radio|stations?|server|homelab"
    r"|rooms?|kitchen|bedroom|bathroom|office|garage|basement|living room"
    r"|upstairs|downstairs|intercom|home|doors?|doorbell|cameras?"
    r"|everyone|everybody|anyone|anybody|listen\w*"
    r")\b"
)

# A request for something to listen to that only the Q&A model makes up:
# a joke, a riddle, a fun fact, a story, a poem — or an explanation
# ("explain how a rainbow forms"). The article is required for the
# singular, so "tell me the stories" (the news) stays a routed turn.
_QA_REQUEST_RE = re.compile(
    r"^(?:tell|give|say|share)(?: me| us)?\s+"
    r"(?:"
    r"(?:a|an|another|one more|one|some|any)\s+(?:\w+\s+){0,2}?"
    r"(?:joke|jokes|riddle|riddles|pun|puns|fact|facts|story|poem|limerick|tongue twister)"
    r"|something (?:interesting|funny|fun|random|cool|amazing|surprising)"
    r")"
    r"(?:\s+(?:about|on|with|involving|featuring)\s+.+)?$"
    r"|^make (?:me|us) laugh$"
    r"|^(?:explain|describe)\s+(?:how|why|what|the|a|an)\b.+$"
)


def about_the_house(text: str) -> bool:
    """Whether ``text`` (normalized) is about this household rather than
    the world: the speaker or the assistant, something pointed at, or
    something Domovoi runs. A music word counts too: "who sings creep" is a
    library turn."""
    return bool(_ABOUT_THE_HOUSE_RE.search(text) or MUSIC_CUE_RE.search(text))



def is_plain_knowledge_question(transcript: str) -> bool:
    """A who/whose/whom/why/where question about the world: "who wrote the
    odyssey", "why is the sky blue", "tell me where the eiffel tower is".
    ``transcript`` is normalized the way fast paths see it. The lead-in is
    left out of the cue search (it is "tell me", "do you know")."""
    if not KNOWLEDGE_QUESTION_RE.match(transcript):
        return False
    question = transcript[_LEAD_IN_RE.match(transcript).end():]
    return not about_the_house(question)


def is_plain_qa_request(transcript: str) -> bool:
    """"tell me a joke", "give me a fun fact", "tell me a story about a
    dragon", "explain how a rainbow forms": something to say that no tool
    provides."""
    m = _QA_REQUEST_RE.match(transcript)
    if m is None:
        return False
    # Skip "tell me" / "give us" before looking for cues.
    rest = re.sub(r"^(?:tell|give|say|share|make|explain|describe)(?: me| us)?\s+", "", transcript)
    return not about_the_house(rest)


def answers_without_tools(transcript: str) -> bool:
    """Whether the router should hand ``transcript`` (normalized, filler
    stripped) straight to the Q&A model without asking the tool model.
    True only for a plain knowledge question or a plain request for a
    joke, fact or story — the utterances no tool is for, and the ones a
    small tool model most often answers itself (or baits on) instead of
    saying "no tool"."""
    return is_plain_knowledge_question(transcript) or is_plain_qa_request(transcript)
# What a statement about a timer or reminder goes on with: "my reminder for
# 10 minutes DIDN'T go off", "that timer WAS useless".
STATEMENT_VERBS = (
    "is|isn't|isnt|was|wasn't|wasnt|were|weren't|are|aren't|did|didn't|didnt|"
    "does|doesn't|doesnt|has|hasn't|had|hadn't|never|won't|wont|went|failed|"
    "keeps|kept|sucks|sucked|already"
)

_TIMER_NOUN = r"(?:reminder|timer|alarm)s?\b"

# Talk ABOUT a timer or reminder, which asks for no timer or reminder
# action. The reminder and timer handlers both withhold their tools on
# it: shown the reminder tool, qwen3:8b (2026-09-30) made "you have a
# reminder in 10 minutes" a new reminder and "the reminder in 10 minutes
# is for the oven" a reminder "oven"; with only that tool withheld it
# reached for the timer's instead (a 10 minute "oven reminder" timer).
# Kept to shapes a command cannot take.
TIMER_STATEMENT_RE = re.compile(
    r"^(?:"
    # "my reminder for 10 minutes didn't go off", "that reminder was
    # useless", "the reminder in 10 minutes is for the oven": the verb
    # straight after the reminder and what it is for, so "the pasta timer,
    # how long is left" still asks the timer
    rf"(?:my|the|that|this|your|our|those|these)(?: [a-z'-]+){{0,3}}? {_TIMER_NOUN}"
    r"(?: (?:for|in|at|from|on|about|to|that|called) [a-z0-9' -]+?)?,?"
    rf" (?:{STATEMENT_VERBS})(?![a-z'-])"
    # "you have a reminder in 10 minutes", "i've got a timer going",
    # "you've set a reminder" — someone else's, or one that already
    # exists. Not "i need / i want a reminder", and not "i set a reminder
    # ..." / "you set a timer ...", which are also what Whisper makes of
    # "hi, set a reminder ..." / "can you set a timer ...".
    r"|(?:you(?:'ve| have| had| got| gave me)|(?:i|we)(?:'ve| have| had| got))"
    rf"(?: got)? (?!to\b)(?:[a-z'-]+ ){{0,3}}?{_TIMER_NOUN}"
    # "why didn't my reminder go off", "why is the timer beeping": a why
    # question about a timer or reminder asks for no action. Nor does a
    # where/who one about what did, or didn't, happen: "where did my timers
    # go", "who didn't hear the reminder". "where's the timer", "where are
    # my reminders", "who set the timer" ask for its status or the list:
    # ordinary routed turns, offered the whole list.
    rf"|why\b.*?\b{_TIMER_NOUN}"
    r"|(?:where|who|whose|whom) (?:did|didn't|didnt|don't|dont|doesn't|doesnt|"
    r"wasn't|wasnt|weren't|werent|won't|wont|isn't|isnt|aren't|arent|"
    r"hasn't|hasnt|haven't|havent|hadn't|hadnt)(?![a-z'-])"
    rf".*?\b{_TIMER_NOUN}"
    # "i set a reminder for 10 minutes and it never went off"
    rf"|.*?\b{_TIMER_NOUN}.*?\b(?:didn't|didnt|did not|never|doesn't|doesnt|"
    r"does not|won't|wont|wasn't|wasnt|failed to)"
    r" (?:[a-z'-]+ )??(?:go(?:es)? off|went off|work|ring|fire|sound|play|remind)"
    r")"
)
