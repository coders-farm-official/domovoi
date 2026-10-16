"""docs/LICENSES.md names what Domovoi downloads, and is linked where people look.

The table is the licence statement for every model, voice and dataset a
server, the Android app and the wake-word trainer fetch (owner decision 7,
2026-10-16: ``hey_jarvis`` and the Llama model ship, stated there). These
checks hold it to the pins themselves: a new default model, voice or wake
word fails here until the table names it. Pure file reads.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "LICENSES.md"
URL = "https://github.com/coders-farm-official/domovoi/blob/main/docs/LICENSES.md"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _manifest() -> dict[str, str]:
    out = {}
    for line in (REPO / "scripts" / "linux" / "provision-manifest.conf").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def test_every_model_provision_installs_is_in_the_table():
    conf, text = _manifest(), _doc()
    models = {v for k, v in conf.items() if k.startswith("tier_") and k.endswith("_model")}
    assert len(models) >= 4
    for model in sorted(models):
        assert f"`{model}`" in text, f"{model} (provision-manifest.conf) is not in docs/LICENSES.md"
    assert conf["whisper_repo"] in text and f"`{conf['whisper_model']}`" in text


def test_every_wake_word_file_the_server_fetches_is_named():
    src = (REPO / "domovoi" / "satellite_media" / "fetchers.py").read_text(encoding="utf-8")
    files = re.findall(r'^\s+"([a-z_]+(?:_v0\.1)?)\.(?:onnx|tflite)": \(', src, re.M)
    assert "hey_jarvis_v0.1" in files
    text = _doc()
    for name in sorted({re.sub(r"_v0\.1$", "", f) for f in files}):
        assert name in text, f"{name} (fetchers.OWW_MODEL_FILES) is not in docs/LICENSES.md"


def test_the_voices_the_server_offers_are_named():
    src = (REPO / "domovoi" / "clients" / "tts.py").read_text(encoding="utf-8")
    voices = re.findall(r'ShippedVoice\("[^"]+", "([^"]+)"', src)
    assert voices
    text = _doc()
    for voice in voices:
        assert voice in text, voice


def test_every_model_the_app_downloads_is_named():
    kt = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "domovoi" / "app" / "assistant" / "ModelCatalog.kt")
    repos = set(re.findall(r"huggingface\.co/([\w.-]+/[\w.-]+)/resolve", kt.read_text(encoding="utf-8")))
    assert repos
    text = _doc()
    for repo in sorted(repos):
        size = re.search(r"moonshine-(tiny|base)-en", repo)
        token = f"`{size.group(1)}-en`" if size else repo
        assert token in text, f"{repo} (ModelCatalog.kt) is not in docs/LICENSES.md"


def test_the_two_defaults_that_are_not_permissive_are_called_out():
    text = _doc()
    short = text.split("## In short", 1)[1].split("\n## ", 1)[0]
    assert "`hey_jarvis`" in short and "CC BY-NC-SA 4.0" in short
    assert "`llama3.2:3b`" in short and "Llama 3.2" in short


def test_its_links_inside_the_repo_resolve():
    text = _doc()
    headings = {re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")
                for h in re.findall(r"^#{1,6} (.+)$", text, re.M)}
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if target.startswith(("http://", "https://")):
            continue
        path, _, anchor = target.partition("#")
        if not path:
            assert anchor in headings, f"#{anchor} is not a heading of docs/LICENSES.md"
            continue
        assert (DOC.parent / path).resolve().exists(), f"{target} does not exist"


def test_it_is_linked_from_the_readme_the_dashboard_and_the_app():
    assert "(docs/LICENSES.md)" in (REPO / "README.md").read_text(encoding="utf-8")
    assert f"'{URL}'" in (REPO / "web" / "static" / "settings.jsx").read_text(encoding="utf-8")
    card = REPO / "android/app/src/main/java/com/domovoi/app/ui/screens/settings/LicencesCard.kt"
    assert f'"{URL}"' in card.read_text(encoding="utf-8")
