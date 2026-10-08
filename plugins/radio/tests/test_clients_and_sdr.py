"""Client scaffolding: stub determinism, ffmpeg-grab failure paths, what
ffmpeg is allowed to open, the plugin UA, and SdrTuner's config gates +
URL shape (subprocess plumbing is exercised only where it can fail fast
without hardware)."""

from __future__ import annotations

import asyncio
import ipaddress

import pytest

from domovoi.sdk import net_safety

from domovoi_plugin_radio import USER_AGENT
from domovoi_plugin_radio.clients import shazam_stream
from domovoi_plugin_radio.clients.fcc_fm import FccFmStubClient
from domovoi_plugin_radio.clients.radio_browser import RadioBrowserStubClient
from domovoi_plugin_radio.clients.rtl_sdr import SdrTuner
from domovoi_plugin_radio.clients.shazam_stream import (
    ShazamStreamStubClient,
    TrackIdentity,
)


def test_plugin_user_agent_branding() -> None:
    assert USER_AGENT.startswith("domovoi-radio/1.0")
    assert "github.com/coders-farm-official/domovoi" in USER_AGENT


# ─── Stub clients (what USE_STUBS=true wires in) ────────────────────────


async def test_radio_browser_stub_paginates() -> None:
    stub = RadioBrowserStubClient()
    assert await stub.search() == []                      # empty query guard
    page = await stub.search(name="jazz", limit=30)
    assert len(page) == 3
    assert all(s.external_id.startswith("stub-jazz-") for s in page)
    assert await stub.search(name="jazz", offset=3) == []


async def test_fcc_stub_shape() -> None:
    stub = FccFmStubClient()
    rows = await stub.fetch_state("mi".upper())
    assert [r.call_sign for r in rows] == ["KMIA", "KMIB"]
    assert await stub.fetch_state("X") == []


async def test_shazam_stub_encodes_input() -> None:
    stub = ShazamStreamStubClient()
    ident = await stub.identify_wav("C:/tmp/sample-abc.wav")
    assert ident == TrackIdentity(title="stub-wav-sample-abc", artist="Stub Artist")


# ─── ffmpeg grab failure paths ──────────────────────────────────────────


class _Upstream:
    """What the mocked station answers: a status, headers and a body,
    plus a log of every URL the grab asked for (redirect hops included).

    URLs are logged by NAME, as the station row spells them: the outbound
    guard may connect to the literal address it vetted while sending the
    name as ``Host`` (and SNI), so the name is read from the ``Host``
    header rather than from the address the request was sent to."""

    def __init__(self) -> None:
        self.status = 200
        self.headers: dict[str, str] = {"content-type": "audio/mpeg"}
        self.body = b"\xff\xfb\x90\x00" * 4096
        self.redirects: dict[str, str] = {}
        self.opened: list[str] = []

    def answer(self, request):
        import httpx

        host = request.headers.get("host") or request.url.netloc.decode()
        url = f"{request.url.scheme}://{host}{request.url.raw_path.decode()}"
        self.opened.append(url)
        if url in self.redirects:
            return httpx.Response(302, headers={"location": self.redirects[url]})
        return httpx.Response(self.status, headers=self.headers, content=self.body)


@pytest.fixture
def upstream(monkeypatch) -> _Upstream:
    """Every httpx client the grab builds answers from ``_Upstream`` — no
    test here reaches the network."""
    import httpx

    up = _Upstream()
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(up.answer)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return up


@pytest.fixture
def public_stream_host(monkeypatch, upstream):
    """``x`` resolves to a public address, so the grab's outbound-URL
    check passes, and the station answers from the mock upstream, so the
    ffmpeg paths below are what is under test."""
    monkeypatch.setattr(
        net_safety,
        "resolve_host",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )
    return upstream


class _FakeStdin:
    def __init__(self) -> None:
        self.fed = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.fed.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeStderr:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def read(self) -> bytes:
        return self.data


class _FakeProc:
    """An ffmpeg that reads its stdin to the end, then exits ``rc``."""

    def __init__(self, rc: int, stderr: bytes = b"") -> None:
        self._rc = rc
        self.returncode: int | None = None
        self.stdin = _FakeStdin()
        self.stderr = _FakeStderr(stderr)

    async def wait(self) -> int:
        while not self.stdin.closed:
            await asyncio.sleep(0)
        self.returncode = self._rc
        return self._rc

    def kill(self) -> None:
        self.returncode = -9


async def test_grab_missing_ffmpeg_returns_none(
    monkeypatch, tmp_path, public_stream_host
) -> None:
    async def boom(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    out = await shazam_stream.grab_to_tempfile("http://x/stream", 15)
    assert out is None


async def test_grab_nonzero_rc_returns_none(monkeypatch, public_stream_host) -> None:
    async def fake_exec(*args, **kwargs):
        return _FakeProc(1, b"err: no stream")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await shazam_stream.grab_to_tempfile("http://x/stream", 15) is None


async def test_grab_tiny_output_rejected(monkeypatch, public_stream_host) -> None:
    async def fake_exec(*args, **kwargs):
        return _FakeProc(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    # mkstemp creates a 0-byte file; the < 1 KB sanity check rejects it.
    assert await shazam_stream.grab_to_tempfile("http://x/stream", 15) is None


# ─── What ffmpeg is allowed to open (A2-03) ─────────────────────────────


async def test_ffmpeg_reads_only_its_stdin_and_never_sees_the_url(
    monkeypatch, public_stream_host
) -> None:
    """ffmpeg given a URL resolves the name itself, follows redirects
    unchecked and opens an HLS playlist's segment URLs. So it is never
    given one: the grab fetches the stream through the checked fetcher
    and feeds the bytes on stdin, and the argv lets ffmpeg open nothing
    but that pipe — said before -i, where it governs the input."""
    seen: list[list[str]] = []
    procs: list[_FakeProc] = []

    async def fake_exec(*args, **kwargs):
        seen.append(list(args))
        assert kwargs.get("stdin") == asyncio.subprocess.PIPE
        procs.append(_FakeProc(1, b"err"))
        return procs[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await shazam_stream.grab_to_tempfile("http://x/stream", 15)

    (argv,) = seen
    assert argv[argv.index("-protocol_whitelist") + 1] == "pipe"
    assert argv[argv.index("-i") + 1] == "pipe:0"
    assert argv.index("-protocol_whitelist") < argv.index("-i")
    assert not any("x/stream" in a or a.startswith("http") for a in argv)
    # ...and what it was fed is the station's body, through the fetcher.
    assert public_stream_host.opened == ["http://x/stream"]
    assert bytes(procs[0].stdin.fed) == public_stream_host.body
    assert procs[0].stdin.closed


async def test_a_redirect_into_the_house_is_refused_before_ffmpeg(
    monkeypatch, public_stream_host
) -> None:
    """The phase-2 reproduction: a public URL that 302s to the core on
    loopback. The first URL passes the check; the hop does not, and
    nothing is fetched from it or handed to ffmpeg."""
    public_stream_host.redirects["http://x/stream"] = "http://127.0.0.1:6370/v1/health"

    async def never(*args, **kwargs):  # pragma: no cover — spawning IS the failure
        raise AssertionError("spawned ffmpeg after a refused redirect")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", never)
    assert await shazam_stream.grab_to_tempfile("http://x/stream", 15) is None
    assert public_stream_host.opened == ["http://x/stream"]


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("application/vnd.apple.mpegurl", b"#EXTM3U\nhttp://127.0.0.1:6370/seg.ts\n"),
        ("audio/x-mpegurl", b"#EXTM3U\nhttp://127.0.0.1:6370/seg.ts\n"),
        ("audio/x-scpls", b"[playlist]\nFile1=http://127.0.0.1:6370/\n"),
        # A playlist under an audio type is still a playlist.
        ("audio/mpeg", b"#EXTM3U\n#EXTINF:10,\nhttp://127.0.0.1:6370/seg.ts\n"),
        ("application/octet-stream", b"\xef\xbb\xbf[playlist]\nFile1=http://10.0.0.5/\n"),
        ("text/html", b"<html>not a stream</html>"),
        ("application/json", b"{}"),
    ],
)
async def test_a_playlist_or_a_page_is_never_handed_to_ffmpeg(
    monkeypatch, public_stream_host, content_type, body
) -> None:
    public_stream_host.headers = {"content-type": content_type}
    public_stream_host.body = body

    async def never(*args, **kwargs):  # pragma: no cover — spawning IS the failure
        raise AssertionError(f"spawned ffmpeg for a {content_type} body")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", never)
    assert await shazam_stream.grab_to_tempfile("http://x/stream", 15) is None


async def test_the_feed_stops_at_the_byte_budget(monkeypatch, public_stream_host) -> None:
    """A station that never stops (and an ffmpeg that never exits) costs
    at most the budget, not the whole timeout's worth of bandwidth."""
    monkeypatch.setattr(shazam_stream, "_FEED_BYTES_PER_SEC", 1024)
    monkeypatch.setattr(shazam_stream, "_FEED_PROBE_SEC", 0)
    monkeypatch.setattr(shazam_stream, "_FEED_CHUNK", 512)
    public_stream_host.body = b"\x00" * (64 * 1024)
    procs: list[_FakeProc] = []

    async def fake_exec(*args, **kwargs):
        procs.append(_FakeProc(1))
        return procs[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await shazam_stream.grab_to_tempfile("http://x/stream", 2)
    assert 2048 <= len(procs[0].stdin.fed) < 2048 + 512 + 1
    assert procs[0].stdin.closed


@pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None, reason="ffmpeg not on PATH"
)
async def test_a_real_ffmpeg_records_from_the_pipe(
    monkeypatch, tmp_path, public_stream_host
) -> None:
    """End to end with the real binary: three seconds of MP3 fed on
    stdin come out as a 16 kHz mono WAV, so the pipe really works on
    this platform."""
    import subprocess

    mp3 = tmp_path / "tone.mp3"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-ac", "1", "-b:a", "64k", str(mp3),
        ],
        check=True,
        timeout=60,
    )
    public_stream_host.body = mp3.read_bytes()
    path = await shazam_stream.grab_to_tempfile("http://x/stream", 2, timeout_sec=30.0)
    assert path is not None
    try:
        import wave

        with wave.open(path, "rb") as w:
            assert w.getnchannels() == 1
            assert w.getframerate() == 16000
            assert 1.5 <= w.getnframes() / 16000 <= 2.1
    finally:
        import os

        os.unlink(path)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "concat:one.mp3|two.mp3",
        "http://127.0.0.1:6370/v1/admin/snapshot",
        "http://127.1:6370/v1/admin/snapshot",
        "http://[::1]:6370/x",
        "http://10.0.0.5/stream",
        "http://192.168.1.50/stream",
        "http://169.254.169.254/latest/meta-data/",
        "http://[fc00::1]/stream",
    ],
)
async def test_the_grab_never_spawns_ffmpeg_for_a_house_local_url(
    monkeypatch, url
) -> None:
    monkeypatch.setattr(net_safety, "resolve_host", lambda host: [])

    async def never(*args, **kwargs):  # pragma: no cover — spawning IS the failure
        raise AssertionError(f"spawned ffmpeg for {url}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", never)
    assert await shazam_stream.grab_to_tempfile(url, 15) is None


# ─── SdrTuner gates + URL shape ─────────────────────────────────────────


async def test_tuner_disabled_probe_and_tune() -> None:
    tuner = SdrTuner(enabled=False)
    assert await tuner.probe() is False
    with pytest.raises(RuntimeError, match="disabled"):
        await tuner.tune(97.5)


async def test_tuner_rejects_out_of_band_frequency() -> None:
    tuner = SdrTuner(enabled=True)
    with pytest.raises(ValueError, match="out of FM band"):
        await tuner.tune(200.0)
    with pytest.raises(ValueError):
        await tuner.tune(50.0)


def test_tuner_stream_url_shape() -> None:
    tuner = SdrTuner(
        enabled=True, http_port=6391, stream_base="http://192.168.1.10/"
    )
    # Trailing slash normalized; literal path, NO query string (ffmpeg's
    # -listen matcher rejects cache-buster params).
    assert tuner.stream_url == "http://192.168.1.10:6391/fm.mp3"
    assert "?" not in tuner.stream_url


async def test_tuner_probe_without_rtl_test_is_false(monkeypatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    tuner = SdrTuner(enabled=True)
    assert await tuner.probe() is False


async def test_tuner_stop_is_idempotent() -> None:
    tuner = SdrTuner(enabled=True)
    await tuner.stop()      # nothing running — must not raise
    assert tuner.current_frequency_mhz is None


# --- the listener binds one address, never every interface ---------------
#
# ffmpeg's -listen 1 serves exactly one client; a wildcard bind is a
# listener anyone on the network can occupy ahead of the room's MPD.


def test_the_listener_binds_the_host_mpd_dials() -> None:
    from domovoi_plugin_radio.clients.rtl_sdr import listener_host

    assert listener_host("http://192.168.1.10") == "192.168.1.10"
    assert listener_host("http://192.168.1.10/") == "192.168.1.10"
    assert listener_host("http://127.0.0.1") == "127.0.0.1"
    assert listener_host("192.168.1.10") == "192.168.1.10"


def test_an_explicit_bind_host_wins() -> None:
    from domovoi_plugin_radio.clients.rtl_sdr import listener_host

    assert listener_host("http://domovoi.lan", "10.0.0.5") == "10.0.0.5"


def test_a_name_is_resolved_once_and_an_unresolvable_one_falls_back_to_loopback() -> None:
    from domovoi_plugin_radio.clients.rtl_sdr import listener_host

    assert listener_host("http://domovoi.lan", resolve=lambda n: "192.168.1.20") == "192.168.1.20"

    def nope(name):
        raise OSError("no such host")

    assert listener_host("http://nowhere.invalid", resolve=nope) == "127.0.0.1"


@pytest.mark.parametrize("configured", ["0.0.0.0", "http://0.0.0.0", "::", "*", "", "http://"])
def test_the_wildcard_is_never_bound(configured) -> None:
    from domovoi_plugin_radio.clients.rtl_sdr import listener_host

    assert listener_host(configured) == "127.0.0.1"
    assert listener_host("http://192.168.1.10", configured) != "0.0.0.0"


def test_the_tuner_hands_ffmpeg_the_bound_address_not_the_wildcard() -> None:
    import inspect

    from domovoi_plugin_radio.clients import rtl_sdr

    tuner = SdrTuner(enabled=True, http_port=6391, stream_base="http://192.168.1.10")
    assert tuner.listen_host == "192.168.1.10"
    assert tuner.stream_url == "http://192.168.1.10:6391/fm.mp3"
    src = inspect.getsource(rtl_sdr.SdrTuner._start_locked)
    assert "0.0.0.0" not in src
    assert "{self.listen_host}:{self._http_port}" in src
    probe = inspect.getsource(rtl_sdr.SdrTuner._wait_for_listener_bound)
    assert "0.0.0.0" not in probe
