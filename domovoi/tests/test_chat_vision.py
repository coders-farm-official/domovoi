"""Pictures in the text chat, as the vision model gets them — DB-free.

Owner decision 2026-10-12, from a CPU benchmark of ten vision models
(design-notes/vision-model-research): the default vision model is
qwen3-vl:2b-instruct-q4_K_M, the chat shrinks each picture to a 1280 px
long edge (upright: a phone photo's EXIF orientation applied), and a call
that carries pictures sends ``think: false`` and a context window of its
own (``ollama_vision_num_ctx``, 8192), so four pictures fit. Covered here:

* ``_vision_bytes`` (web/backend/api/chat.py): the copy beside the upload,
  its size, format, orientation and cache; pictures left as they are.
* ``chat_stream`` (domovoi/clients/ollama.py): ``think`` and ``num_ctx`` on
  the wire, and the one retry without ``think`` when a server refuses it.
* The default, the provisioning pins and the examples agree.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

import web.backend.api.chat as chat_api
from domovoi.clients import ollama
from domovoi.config import Settings, settings
from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate

REPO = Path(__file__).resolve().parents[2]
TOKEN = "0123456789abcdef0123456789abcdef"
DEFAULT_VISION = "qwen3-vl:2b-instruct-q4_K_M"


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    d = tmp_path / "chat_uploads"
    d.mkdir()
    monkeypatch.setattr(chat_api, "UPLOADS_DIR", d)
    return d


def _save(path: Path, im: Image.Image, fmt: str, **kw) -> Path:
    im.save(path, fmt, **kw)
    return path


def _open(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def _halves(size: tuple[int, int]) -> Image.Image:
    """Left half red, right half blue: shows which way a picture turned."""
    w, h = size
    im = Image.new("RGB", size, (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, w // 2, h))
    return im


def _exif_orientation(value: int) -> bytes:
    exif = Image.Exif()
    exif[chat_api._EXIF_ORIENTATION] = value
    return exif.tobytes()


# ─── the copy the model gets ────────────────────────────────────────────────


def test_a_big_photo_is_shrunk_to_1280_and_kept_beside_the_upload(uploads):
    src = _save(uploads / f"{TOKEN}.jpg", _halves((3000, 2000)), "JPEG", quality=95)
    data = chat_api._vision_bytes(src)
    im = _open(data)
    assert im.format == "JPEG" and im.size == (1280, 853)
    copy = uploads / f"{TOKEN}-1280.jpg"
    assert copy.read_bytes() == data
    # The upload itself is untouched, and is still the one the token names.
    assert _open(src.read_bytes()).size == (3000, 2000)
    assert chat_api._upload_path(TOKEN) == src
    # Nothing half-written is left behind.
    assert sorted(p.name for p in uploads.iterdir()) == [f"{TOKEN}-1280.jpg", f"{TOKEN}.jpg"]


def test_the_copy_is_made_once(uploads, monkeypatch):
    src = _save(uploads / f"{TOKEN}.png", Image.new("RGB", (2000, 1000), (9, 9, 9)), "PNG")
    first = chat_api._vision_bytes(src)

    def _no_decoding(*a, **kw):
        raise AssertionError("decoded again")

    monkeypatch.setattr(Image, "open", _no_decoding)
    assert chat_api._vision_bytes(src) == first


def test_a_picture_already_small_and_upright_goes_as_it_was(uploads):
    src = _save(uploads / f"{TOKEN}.png", Image.new("RGB", (1280, 720), (1, 2, 3)), "PNG")
    assert chat_api._vision_bytes(src) == src.read_bytes()
    assert [p.name for p in uploads.iterdir()] == [f"{TOKEN}.png"]


def test_a_sideways_phone_photo_is_turned_upright(uploads):
    """EXIF orientation 6: the camera was turned a quarter clockwise; the
    picture shows upright once turned 90 degrees clockwise. Ollama ignores
    the tag, so the copy is turned instead, small as it is."""
    src = _save(uploads / f"{TOKEN}.jpeg", _halves((1000, 600)), "JPEG",
                quality=95, exif=_exif_orientation(6))
    im = _open(chat_api._vision_bytes(src))
    assert im.size == (600, 1000)
    # The left (red) half is on top now, the right (blue) half below.
    top, bottom = im.getpixel((300, 150)), im.getpixel((300, 850))
    assert top[0] > 200 and top[2] < 60, top
    assert bottom[2] > 200 and bottom[0] < 60, bottom
    # Upright, so no orientation tag is left to turn it again.
    assert im.getexif().get(chat_api._EXIF_ORIENTATION) is None
    assert (uploads / f"{TOKEN}-1280.jpg").is_file()


def test_a_big_sideways_photo_is_turned_then_shrunk(uploads):
    src = _save(uploads / f"{TOKEN}.jpg", _halves((4032, 3024)), "JPEG",
                quality=90, exif=_exif_orientation(6))
    assert _open(chat_api._vision_bytes(src)).size == (960, 1280)


def test_a_screenshot_stays_lossless(uploads):
    shot = Image.new("RGBA", (1920, 1080), (20, 20, 20, 255))
    src = _save(uploads / f"{TOKEN}.png", shot, "PNG")
    im = _open(chat_api._vision_bytes(src))
    assert (im.format, im.size, im.mode) == ("PNG", (1280, 720), "RGBA")
    assert (uploads / f"{TOKEN}-1280.png").is_file()


@pytest.mark.parametrize("ext,fmt", [(".gif", "GIF"), (".bmp", "BMP"), (".webp", "WEBP")])
def test_other_kinds_become_a_png(uploads, ext, fmt):
    pic = Image.new("RGB", (2000, 1000), (200, 10, 10))
    if fmt == "GIF":
        pic = pic.convert("P")
    src = _save(uploads / f"{TOKEN}{ext}", pic, fmt)
    im = _open(chat_api._vision_bytes(src))
    assert im.format == "PNG" and im.size == (1280, 640)
    # A palette picture is resampled in colour, not pixel by pixel.
    assert im.mode in ("RGB", "RGBA")
    assert (uploads / f"{TOKEN}-1280.png").is_file()


def test_a_picture_pillow_cannot_read_goes_as_it_was(uploads):
    src = uploads / f"{TOKEN}.png"
    src.write_bytes(b"\x89PNGfake")
    assert chat_api._vision_bytes(src) == b"\x89PNGfake"
    assert [p.name for p in uploads.iterdir()] == [f"{TOKEN}.png"]


def test_an_upload_that_is_gone_raises_oserror(uploads):
    with pytest.raises(OSError):
        chat_api._vision_bytes(uploads / f"{TOKEN}.png")


def test_the_copy_is_never_taken_for_the_upload(uploads):
    """``<token>-1280.png`` is outside ``<token>.*``: the token keeps naming
    the picture the user sent, never the model's copy."""
    (uploads / f"{TOKEN}-1280.png").write_bytes(b"copy")
    assert chat_api._upload_path(TOKEN) is None
    assert chat_api._vision_copy_path(uploads / f"{TOKEN}.JPEG").name == f"{TOKEN}-1280.jpg"
    assert chat_api._vision_copy_path(uploads / f"{TOKEN}.webp").name == f"{TOKEN}-1280.png"


def test_reply_stats_record_the_window_the_call_asked_for():
    st = chat_api._reply_stats({}, first_token_ms=None, wall_ms=1.0, context_sent=1,
                               context_in_thread=1, model_role="vision", num_ctx=8192)
    assert st["num_ctx"] == 8192
    st = chat_api._reply_stats({}, first_token_ms=None, wall_ms=1.0, context_sent=1,
                               context_in_thread=1, model_role="chat", num_ctx=0)
    assert "num_ctx" not in st


# ─── the client: think and num_ctx on the wire ──────────────────────────────

_REAL_CLIENT = httpx.AsyncClient
_OK_LINES = "\n".join([
    json.dumps({"message": {"content": "a cat"}, "done": False}),
    json.dumps({"message": {"content": ""}, "done": True}),
]).encode()


def _ollama(monkeypatch, *responses: httpx.Response) -> list[dict]:
    """Ollama answering each POST /api/chat with the next response; returns
    the request bodies it was sent."""
    bodies: list[dict] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return queue.pop(0)

    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: _REAL_CLIENT(transport=httpx.MockTransport(handler), timeout=None))
    return bodies


async def _drain(**kw) -> list[str]:
    msgs = [{"role": "user", "content": "what is this?", "images": ["aGk="]}]
    return [d async for d in ollama.chat_stream(msgs, model=DEFAULT_VISION, **kw)]


async def test_a_vision_call_sends_think_false_and_its_window(monkeypatch):
    monkeypatch.setattr(settings, "ollama_num_ctx", 4096)
    bodies = _ollama(monkeypatch, httpx.Response(200, content=_OK_LINES))
    assert await _drain(think=False, num_ctx=8192) == ["a cat"]
    assert bodies[0]["think"] is False
    assert bodies[0]["options"] == {"num_ctx": 8192}


@pytest.mark.parametrize("qa_ctx,expected", [(4096, {"num_ctx": 4096}), (0, None)])
async def test_no_window_of_its_own_falls_back_to_the_qa_one(monkeypatch, qa_ctx, expected):
    monkeypatch.setattr(settings, "ollama_num_ctx", qa_ctx)
    bodies = _ollama(monkeypatch, httpx.Response(200, content=_OK_LINES))
    await _drain(num_ctx=0)
    assert bodies[0].get("options") == expected
    assert "think" not in bodies[0]


async def test_a_server_that_refuses_think_is_asked_again_without_it(monkeypatch):
    refusal = httpx.Response(400, json={"error": f'"{DEFAULT_VISION}" does not support thinking'})
    bodies = _ollama(monkeypatch, refusal, httpx.Response(200, content=_OK_LINES))
    assert await _drain(think=False, num_ctx=8192) == ["a cat"]
    assert len(bodies) == 2
    assert bodies[0]["think"] is False and "think" not in bodies[1]
    assert bodies[1]["options"] == {"num_ctx": 8192} and bodies[1]["messages"] == bodies[0]["messages"]


async def test_any_other_refusal_is_not_retried(monkeypatch):
    body = {"error": "request (9044 tokens) exceeds the available context size (4096 tokens)"}
    bodies = _ollama(monkeypatch, httpx.Response(400, json=body), httpx.Response(200, content=_OK_LINES))
    with pytest.raises(ollama.OllamaChatError) as e:
        await _drain(think=False)
    assert e.value.status == 400 and "context size" in e.value.detail
    assert len(bodies) == 1


async def test_a_second_think_refusal_is_raised(monkeypatch):
    refusal = {"error": "think value is not supported"}
    bodies = _ollama(monkeypatch, httpx.Response(400, json=refusal), httpx.Response(400, json=refusal))
    with pytest.raises(ollama.OllamaChatError):
        await _drain(think=False)
    assert len(bodies) == 2


# ─── the setting, the default and the pins agree ────────────────────────────


def test_the_default_vision_model_and_window():
    fields = Settings.model_fields
    assert fields["ollama_vision_model"].default == DEFAULT_VISION
    assert fields["ollama_vision_num_ctx"].default == 8192


def test_a_blank_vision_window_reads_as_unset(monkeypatch):
    monkeypatch.setenv("OLLAMA_VISION_NUM_CTX", " ")
    assert Settings(_env_file=None).ollama_vision_num_ctx == 0
    monkeypatch.setenv("OLLAMA_VISION_NUM_CTX", "16384")
    assert Settings(_env_file=None).ollama_vision_num_ctx == 16384


def test_the_vision_window_is_editable_beside_the_vision_model():
    spec = FIELD_BY_NAME["ollama_vision_num_ctx"]
    assert (spec.group, spec.type) == ("Models", "int")
    # Only the web process reads it, and that one reads settings at start.
    assert spec.tier == "restart"
    assert coerce_and_validate(spec, "8192") == 8192
    with pytest.raises(ValueError):
        coerce_and_validate(spec, -1)


def _manifest() -> dict[str, str]:
    out = {}
    for line in (REPO / "scripts/linux/provision-manifest.conf").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            out[k] = v
    return out


def test_the_provisioning_tiers_pin_the_default():
    """A provisioned box and one that reads the repo default (the Beelink)
    use the same model, pinned by the digest the registry served."""
    m = _manifest()
    for tier in ("floor", "standard"):
        assert m[f"tier_{tier}_vision_model"] == DEFAULT_VISION
        assert m[f"tier_{tier}_vision_digest"] == (
            "ea422f1e73652a95479954d8572d3c8c6022f628ce2d38a1a04aae1b7f2d5300"
        )


def test_the_examples_name_the_default():
    env = (REPO / "domovoi/.env.example").read_text(encoding="utf-8")
    assert f"# OLLAMA_VISION_MODEL={DEFAULT_VISION}\n" in env
    assert "# OLLAMA_VISION_NUM_CTX=8192\n" in env
    catalog = json.loads((REPO / "web/backend/model_catalog.json").read_text(encoding="utf-8"))
    row = next(r for r in catalog["ollama"] if r["name"] == DEFAULT_VISION)
    assert row["role"] == "vision" and row["license"] == "Apache-2.0"
