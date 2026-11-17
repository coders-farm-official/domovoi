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
    assert request.code == asker_code and len(asker_code) == 4 and asker_code.isdigit()
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
    a, b = b"\x01" * 32, b"\x02" * 32
    base = fp.code(a, b, b"\x03" * 32, b"\x04" * 32)
    assert base != fp.code(a, b, b"\x05" * 32, b"\x04" * 32)
    assert base != fp.code(a, b, b"\x03" * 32, b"\x06" * 32)
    assert base != fp.code(b, a, b"\x03" * 32, b"\x04" * 32)
