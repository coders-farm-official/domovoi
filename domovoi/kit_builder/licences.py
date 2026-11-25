"""Which models may go on a kit, and under which licence (design section 9).

The licence check is code: the builder copies an Ollama model only when
:func:`model_licence` names a licence on the kit format's allow-list
(``domovoi.offline_kit.format.LICENCES``). A model whose licence forbids
passing it on maps to a ``NEVER_ON_A_KIT`` licence and is refused with the
reason; a model this table doesn't know is refused as "unknown terms".

Rules are matched by the model's name (the part before ``:``) and, where
the licence depends on the size, a pattern on the tag. First match wins.
Keep this table in step with docs/LICENSES.md ("On an offline kit").
"""

from __future__ import annotations

import re

from domovoi.offline_kit import format as fmt

#: (model name, tag pattern or None for any tag, licence).
MODEL_RULES: tuple[tuple[str, str | None, str], ...] = (
    # Qwen3 and Qwen3-VL: Apache-2.0 (each model card).
    ("qwen3", None, "Apache-2.0"),
    ("qwen3-vl", None, "Apache-2.0"),
    # Qwen2.5: the 3B is research-only, the 72B under the Qwen licence;
    # the other sizes are Apache-2.0.
    ("qwen2.5", r"3b(\b|-|$)", "LicenseRef-Qwen-Research"),
    ("qwen2.5", r"72b(\b|-|$)", "LicenseRef-Qwen-Research"),
    ("qwen2.5", r"(0\.5|1\.5|7|14|32)b(\b|-|$)", "Apache-2.0"),
    # Llama 3.2 text models: the community licence (notice, AUP, "Built
    # with Llama"). The vision models carry the EU restriction: never.
    ("llama3.2-vision", None, "LicenseRef-Llama-Vision-EU"),
    ("llama3.2", r"(1|3)b(\b|-|$)", "LicenseRef-Llama-3.2-Community"),
    # Gemma: Google's terms bind every recipient.
    ("gemma", None, "LicenseRef-Gemma"),
    ("gemma2", None, "LicenseRef-Gemma"),
    ("gemma3", None, "LicenseRef-Gemma"),
)

#: Licences this module maps to that the format doesn't list as never,
#: with the reason (they are refused like NEVER_ON_A_KIT).
EXTRA_NEVER = {
    "LicenseRef-Llama-Vision-EU": "the EU restriction on Llama's multimodal models",
}


def split_tag(model: str) -> tuple[str, str]:
    """("qwen3", "8b-q4_K_M") from "qwen3:8b-q4_K_M" (library/ dropped)."""
    name, _, tag = model.partition(":")
    name = name.removeprefix("library/")
    return name, tag or "latest"


def model_licence(model: str) -> str | None:
    """The licence of an Ollama model, or None when its terms are unknown."""
    name, tag = split_tag(model)
    for rule_name, pattern, licence in MODEL_RULES:
        if name != rule_name:
            continue
        if pattern is None or re.match(pattern, tag):
            return licence
    return None


def refusal(model: str) -> str | None:
    """Why ``model`` can't go on a kit, in plain words, or None."""
    licence = model_licence(model)
    if licence is None:
        return f"{model}: its licence is not known, so it can't be passed on"
    if licence in fmt.NEVER_ON_A_KIT:
        return f"{model}: its licence doesn't allow passing it on ({fmt.NEVER_ON_A_KIT[licence]})"
    if licence in EXTRA_NEVER:
        return f"{model}: its licence doesn't allow passing it on ({EXTRA_NEVER[licence]})"
    if licence not in fmt.LICENCES:
        return f"{model}: its licence ({licence}) is not on the list a kit may carry"
    return None


#: Wake words: the published Domovoi word and openWakeWord's pre-trained
#: words and melspectrogram model are CC BY-NC-SA 4.0; openWakeWord's
#: embedding model is Apache-2.0; Silero VAD is MIT. A word the household
#: trained itself is built on openWakeWord's NC-SA features, so it travels
#: under the same terms.
WAKE_NC_SA = "CC-BY-NC-SA-4.0"
OWW_LICENCES = {
    "embedding_model": "Apache-2.0",
    "silero_vad": "MIT",
    "melspectrogram": WAKE_NC_SA,
}


def oww_licence(file_name: str) -> str:
    stem = file_name.split(".", 1)[0]
    return OWW_LICENCES.get(stem, WAKE_NC_SA)
