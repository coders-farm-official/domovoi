"""Friend files and friend requests (domovoi/friend_houses/friend_file.py,
pairing.py), over a loopback socket."""

from __future__ import annotations

import asyncio

import pytest

from domovoi.friend_houses import friend_file as ff
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import pairing as fp
from domovoi.friend_houses import tor_config as tc

# ─── the friend file ──────────────────────────────────────────────────────


def _file(tmp_path, name="The Smiths", invite=b"\x11" * 16):
    keys = fid.create(tmp_path / "a.json")
    return keys, ff.make(keys, pairing_onion=bytes(range(32)), invite_id=invite,
                         expires_day=20_500, name=name)


def test_a_friend_file_round_trips_as_one_short_line(tmp_path) -> None:
    keys, f = _file(tmp_path)
    text = f.to_text()
    assert text.startswith("domovoi-friend:1:") and "\n" not in text
    assert len(text) < 400
    back = ff.parse(f"Hi! Here's our file:\n\n{text}\n\nSee you soon")
    assert back == f
    assert back.invite_secret == keys.invite_secret(b"\x11" * 16)
    assert back.pairing_address == fid.onion_address(bytes(range(32)))


def test_invite_secrets_are_derived_and_differ_per_invite(tmp_path) -> None:
    keys = fid.create(tmp_path / "a.json")
    assert keys.invite_secret(b"\x01" * 16) == keys.invite_secret(b"\x01" * 16)
    assert keys.invite_secret(b"\x01" * 16) != keys.invite_secret(b"\x02" * 16)
    other = fid.create(tmp_path / "b.json")
    assert other.invite_secret(b"\x01" * 16) != keys.invite_secret(b"\x01" * 16)


def test_any_change_to_the_file_is_refused(tmp_path) -> None:
    _, f = _file(tmp_path)
    text = f.to_text()
    body = text[len(ff.PREFIX):]
    for i in (0, 10, 60, len(body) - 3):
        flipped = body[:i] + ("A" if body[i] != "A" else "B") + body[i + 1:]
        with pytest.raises(ff.BadFriendFile):
            ff.parse(ff.PREFIX + flipped)


@pytest.mark.parametrize("text", ["", "hello", "domovoi-friend:1:", "domovoi-friend:2:" + "A" * 300,
                                  "x" * (ff.MAX_TEXT + 1)],
                         ids=["empty", "word", "prefix-only", "version-2", "too-long"])
def test_things_that_arent_friend_files_say_so_plainly(text: str) -> None:
    with pytest.raises(ff.BadFriendFile) as err:
        ff.parse(text)
    assert "friend file" in str(err.value).lower()


def test_names_lose_control_and_bidi_characters_and_are_capped(tmp_path) -> None:
    assert ff.clean_name("Grand‮ma's\x00 house\n") == "Grandma's house"
    long = ff.clean_name("é" * 100)
    assert len(long.encode("utf-8")) <= ff.NAME_MAX_BYTES
    _, f = _file(tmp_path, name="  The\tSmiths‏ ")
    assert ff.parse(f.to_text()).name == "The Smiths"


def test_the_download_name_is_plain(tmp_path) -> None:
    assert ff.filename_for("The Smiths") == "The Smiths friend file.domovoi-friend"
    assert ff.filename_for("../../etc") == "etc friend file.domovoi-friend"
    assert ff.filename_for("???") == "Domovoi friend file.domovoi-friend"


# ─── the request ──────────────────────────────────────────────────────────


def _asker(tmp_path):
    keys = fid.create(tmp_path / "b.json")
    return keys, keys.address_statement(bytes([9]) * 32, 1_800_000_000)


async def _inviter(keys, open_ids: set[bytes], results: list):
    async def invite_open(invite_id: bytes) -> bool:
        return invite_id in open_ids

    async def handler(reader, writer):
        try:
            results.append(await fp.answer(reader, writer, keys=keys, invite_open=invite_open))
        except fp.PairingRefused as e:
            results.append(e)
        finally:
            writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_a_request_arrives_and_both_houses_get_the_same_code(tmp_path) -> None:
    a_keys, f = _file(tmp_path)
    b_keys, b_statement = _asker(tmp_path)
    reach = tc.ReachKey.generate()
    results: list = []
    server, port = await _inviter(a_keys, {f.invite_id}, results)
    async with server:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        asker_code = await fp.ask(r, w, file=ff.parse(f.to_text()), keys=b_keys,
                                  statement=b_statement, reach_private=reach.private,
                                  name="The Patels‮")
        w.close()
        await asyncio.sleep(0.05)
    request = results[0]
    assert isinstance(request, fp.Request)
    assert request.code == asker_code and len(asker_code) == 6 and asker_code.isdigit()
    assert request.house_key == b_keys.house_key
    assert request.statement.session_key == b_keys.session_key
    assert request.reach_private == reach.private
    assert request.name == "The Patels"
    assert request.invite_id == f.invite_id


async def test_codes_differ_between_runs(tmp_path) -> None:
    a_keys, f = _file(tmp_path)
    b_keys, b_statement = _asker(tmp_path)
    codes = set()
    for _ in range(6):
        results: list = []
        server, port = await _inviter(a_keys, {f.invite_id}, results)
        async with server:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            codes.add(await fp.ask(r, w, file=f, keys=b_keys, statement=b_statement,
                                   reach_private=b"\x00" * 32, name="B"))
            w.close()
    assert len(codes) > 1  # fresh randomness each time, not a key fingerprint


async def test_a_used_or_unknown_invite_is_refused_silently(tmp_path) -> None:
    a_keys, f = _file(tmp_path)
    b_keys, b_statement = _asker(tmp_path)
    results: list = []
    server, port = await _inviter(a_keys, set(), results)  # nothing open
    async with server:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        with pytest.raises(fp.PairingRefused):
            await fp.ask(r, w, file=f, keys=b_keys, statement=b_statement,
                         reach_private=b"\x00" * 32, name="B")
        await asyncio.sleep(0.05)
    assert isinstance(results[0], fp.PairingRefused)


async def test_a_guessed_or_wrong_secret_gets_nothing(tmp_path) -> None:
    a_keys, f = _file(tmp_path)
    b_keys, b_statement = _asker(tmp_path)
    forged = ff.FriendFile(f.house_key, f.session_key, f.pairing_onion, f.invite_id,
                           b"\x00" * 32, f.expires_day, f.name, f.signature)
    results: list = []
    server, port = await _inviter(a_keys, {f.invite_id}, results)
    async with server:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        with pytest.raises(fp.PairingRefused):
            await fp.ask(r, w, file=forged, keys=b_keys, statement=b_statement,
                         reach_private=b"\x00" * 32, name="B")
        await asyncio.sleep(0.05)
    assert isinstance(results[0], fp.PairingRefused)


async def test_a_statement_for_another_session_key_is_refused(tmp_path) -> None:
    a_keys, f = _file(tmp_path)
    b_keys, _ = _asker(tmp_path)
    c_keys = fid.create(tmp_path / "c.json")
    c_statement = c_keys.address_statement(bytes(32), 1)  # signed by C, B's session
    results: list = []
    server, port = await _inviter(a_keys, {f.invite_id}, results)
    async with server:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        with pytest.raises(fp.PairingRefused):
            await fp.ask(r, w, file=f, keys=b_keys, statement=c_statement,
                         reach_private=b"\x00" * 32, name="B")
        await asyncio.sleep(0.05)
    assert isinstance(results[0], fp.PairingRefused)


def test_the_code_depends_on_both_random_values() -> None:
    a, b, h = b"\x01" * 32, b"\x02" * 32, b"\x07" * 32
    base = fp.code(h, a, b, b"\x03" * 32, b"\x04" * 32)
    assert base != fp.code(h, a, b, b"\x05" * 32, b"\x04" * 32)
    assert base != fp.code(h, a, b, b"\x03" * 32, b"\x06" * 32)
    assert base != fp.code(h, b, a, b"\x03" * 32, b"\x04" * 32)
    assert base != fp.code(b"\x08" * 32, a, b, b"\x03" * 32, b"\x04" * 32)


async def _grinding_ask(reader, writer, *, file, session_private, wanted_code=None, tries=100_000):
    """Audit R6 FH-1's proof of concept: commit to nB, see nA, THEN pick a
    house key (same session key) whose code is the one wanted, and send its
    statement. Returns (statement sent, the code it was ground to)."""
    import secrets as _s

    from domovoi.friend_houses import session as fs
    from domovoi.friend_houses.noise import Handshake

    first = fid.HouseKeys(house_seed=_s.token_bytes(32), session_private=session_private)
    hs = Handshake("IKpsk2", initiator=True, prologue=fp.PROLOGUE, static=first.session,
                   remote_static=file.session_key, psk=file.invite_secret)
    n_b = _s.token_bytes(32)
    writer.write(fp.PREFACE + fs.frame(hs.write_message(
        file.invite_id + fp.commitment(n_b, first.house_key))))
    await writer.drain()
    n_a = hs.read_message(await fs.read_frame(reader, max_size=fp._MSG2_SIZE, timeout=10))
    transport = hs.transport()
    chosen = None
    for _ in range(tries):  # the grind: a new house key after seeing nA
        k = fid.HouseKeys(house_seed=_s.token_bytes(32), session_private=session_private)
        got = fp.code(transport.handshake_hash, file.house_key, k.house_key, n_a, n_b)
        if wanted_code is None or got == wanted_code:
            chosen = (k, got)
            break
    assert chosen, "grind found nothing"
    k, got = chosen
    statement = k.address_statement(bytes([9]) * 32, 1_800_000_000)
    request = fp._REQUEST_FIXED.pack(n_b, statement.to_bytes(), b"\x00" * 32, 1) + b"M"
    writer.write(fs.frame(transport.encrypt(request)))
    await writer.drain()
    return statement, got


async def test_a_house_key_chosen_after_seeing_the_nonce_is_refused(tmp_path) -> None:
    # R6 FH-1: the asking house used to commit to its nonce only, so it
    # could generate house keys until the inviter's code matched one a
    # victim house had shown. Its house key is now in the commitment.
    a_keys, f = _file(tmp_path)
    b_keys, _ = _asker(tmp_path)
    results: list = []
    server, port = await _inviter(a_keys, {f.invite_id}, results)
    async with server:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        # Any house key other than the committed one stands for the grind's
        # result (a full grind to a chosen code takes ~1,000,000 keys).
        await _grinding_ask(r, w, file=f, session_private=b_keys.session_private)
        for _ in range(100):
            if results:
                break
            await asyncio.sleep(0.05)
        w.close()
    assert isinstance(results[0], fp.PairingRefused)
    assert "commitment" in str(results[0])


# ─── pairing v2: the 6-digit code, and no way back to v1 ──────────────────

V1_PREFACE = b"DFP1"
V1_PROLOGUE = b"domovoi-fh-pairing-v1"


def _v1_file(keys: fid.HouseKeys, invite=b"\x22" * 16) -> str:
    """A friend file exactly as a house before 1.5.0 makes it: version byte
    1, signed by its house."""
    import base64
    import struct

    name = b"Old House"
    body = struct.pack(">B32s32s32s16s32sIB", 1, keys.house_key, keys.session_key,
                       bytes(range(32)), invite, keys.invite_secret(invite), 20_500,
                       len(name)) + name
    raw = body + keys.sign(fid.CTX_FRIEND_FILE, body)
    return ff.PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_the_code_is_six_digits_with_leading_zeros_kept(monkeypatch) -> None:
    import secrets as _s

    assert fp.CODE_DIGITS == 6 and fp.PROTOCOL_VERSION == 2
    seen = set()
    for _ in range(300):
        c = fp.code(*(_s.token_bytes(32) for _ in range(5)))
        assert fp.is_code(c) and len(c) == 6
        seen.add(c)
    assert len(seen) > 290  # a million values, not a handful
    # A digest small enough to need padding still gives six digits.
    from types import SimpleNamespace

    monkeypatch.setattr(fp.hashlib, "sha256", lambda data=b"": SimpleNamespace(
        digest=lambda: bytes(31) + bytes([7])))
    assert fp.code(b"", b"", b"", b"", b"") == "000007"
    monkeypatch.undo()
    for bad in ("1234", "12345", "1234567", "12345a", "١٢٣٤٥٦", None, 123456):
        assert not fp.is_code(bad)


def test_new_friend_files_are_version_2_and_old_houses_read_them_as_newer(tmp_path) -> None:
    keys, f = _file(tmp_path)
    text = f.to_text()
    assert text.startswith("domovoi-friend:1:")  # the line format is unchanged
    import base64

    raw = base64.urlsafe_b64decode(text[len(ff.PREFIX):] + "==")
    assert raw[0] == 2  # what a 1.4 house checks first: "from a newer Domovoi"


def test_a_version_1_friend_file_is_refused_with_a_plain_message(tmp_path) -> None:
    old = fid.create(tmp_path / "old.json")
    with pytest.raises(ff.OldFriendFile) as err:
        ff.parse(_v1_file(old))
    assert str(err.value) == ff.OLD_FILE
    assert "needs updating to add friends" in str(err.value)


def test_a_v2_file_relabelled_as_v1_reads_as_damaged_not_old(tmp_path) -> None:
    import base64

    _, f = _file(tmp_path)
    raw = bytearray(base64.urlsafe_b64decode(f.to_text()[len(ff.PREFIX):] + "=="))
    raw[0] = 1
    relabelled = ff.PREFIX + base64.urlsafe_b64encode(bytes(raw)).decode("ascii").rstrip("=")
    with pytest.raises(ff.BadFriendFile) as err:
        ff.parse(relabelled)
    assert not isinstance(err.value, ff.OldFriendFile)
    assert "changed or damaged" in str(err.value)


async def _v1_ask(reader, writer, *, file_keys: fid.HouseKeys, invite: bytes, asker: fid.HouseKeys,
                  preface: bytes = V1_PREFACE, prologue: bytes = V1_PROLOGUE) -> bytes | None:
    """An asking house speaking pairing v1 (or a mix): returns the inviter's
    nonce if it ever answered, else None."""
    from domovoi.friend_houses import session as fs
    from domovoi.friend_houses.noise import Handshake

    hs = Handshake("IKpsk2", initiator=True, prologue=prologue, static=asker.session,
                   remote_static=file_keys.session_key, psk=file_keys.invite_secret(invite))
    writer.write(preface + fs.frame(hs.write_message(invite + b"\x00" * 32)))
    await writer.drain()
    try:
        msg2 = await fs.read_frame(reader, max_size=fp._MSG2_SIZE, timeout=2)
        return hs.read_message(msg2)
    except Exception:  # noqa: BLE001 — refused, closed or garbled all mean "no answer"
        return None


@pytest.mark.parametrize("preface,prologue", [(V1_PREFACE, V1_PROLOGUE), (fp.PREFACE, V1_PROLOGUE)],
                         ids=["v1", "v2-preface-v1-prologue"])
async def test_a_v1_request_gets_no_answer_and_spends_nothing(tmp_path, preface, prologue) -> None:
    # Downgrade: an asker claiming to be old (or mixing versions) never gets
    # the inviter's nonce, so there is no 4-digit code to compute, and the
    # v1 preface is refused before the invite is even looked up.
    a_keys, f = _file(tmp_path)
    b_keys, _ = _asker(tmp_path)
    looked_up: list[bytes] = []
    results: list = []

    async def invite_open(invite_id: bytes) -> bool:
        looked_up.append(invite_id)
        return True

    async def handler(reader, writer):
        try:
            results.append(await fp.answer(reader, writer, keys=a_keys, invite_open=invite_open))
        except fp.PairingRefused as e:
            results.append(e)
        finally:
            writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    async with server:
        r, w = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        nonce = await _v1_ask(r, w, file_keys=a_keys, invite=f.invite_id, asker=b_keys,
                              preface=preface, prologue=prologue)
        w.close()
        for _ in range(40):
            if results:
                break
            await asyncio.sleep(0.05)
    assert nonce is None
    assert isinstance(results[0], fp.PairingRefused)
    if preface == V1_PREFACE:
        assert looked_up == [] and "unknown protocol" in str(results[0])


async def test_asking_an_old_house_never_offers_v1(tmp_path) -> None:
    # The asking side: whatever answers, it opens with DFP2 and the v2
    # prologue, so an old (v1-only) inviter just closes and nothing pairs.
    _, f = _file(tmp_path)
    b_keys, b_statement = _asker(tmp_path)
    got: list[bytes] = []

    async def old_inviter(reader, writer):
        got.append(await reader.readexactly(4))
        writer.close()  # what a 1.4 house does with anything but DFP1

    server = await asyncio.start_server(old_inviter, "127.0.0.1", 0)
    async with server:
        r, w = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        with pytest.raises(fp.PairingRefused):
            await fp.ask(r, w, file=f, keys=b_keys, statement=b_statement,
                         reach_private=b"\x00" * 32, name="B")
    assert got == [b"DFP2"]


def test_the_friend_session_protocol_is_unchanged() -> None:
    # Friends made with v1 keep talking: only pairing moved to v2.
    from domovoi.friend_houses import session as fs

    assert fs.PREFACE == b"DFH1" and fs.PROLOGUE == b"domovoi-fh-session-v1"


def test_the_pairing_budget_is_unchanged_by_the_longer_code(tmp_path) -> None:
    # Six digits make each try 100 times less likely to land; the limits
    # stay as strict as with four: 20 requests an hour naming an open file.
    from domovoi.friend_houses import listener as fl

    assert fl.PAIRING_PER_HOUR == 20
    state = fl.State(tmp_path, fid.create(tmp_path / "l.json"))
    assert all(state.pairing_allowed() for _ in range(fl.PAIRING_PER_HOUR))
    assert not state.pairing_allowed()
