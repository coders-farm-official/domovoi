"""The IPP codec (domovoi/printing/ipp.py): every value tag the printing
code sends, round-tripped, and the decoder's caps against hostile answers."""

from __future__ import annotations

import struct

import pytest

from domovoi.printing import ipp
from domovoi.printing.ipp import Attr


def _response(groups, status=0, rid=7):
    return ipp.encode_request(status, rid, groups)


def test_request_header_and_operation_attributes_in_order():
    data = ipp.encode_request("Get-Printer-Attributes", 42, [
        (ipp.OPERATION, ipp.operation_attrs("ipp://printer.local/ipp/print")),
    ])
    assert data[:2] == b"\x02\x00"
    assert struct.unpack("!H", data[2:4])[0] == 0x000B
    assert struct.unpack("!I", data[4:8])[0] == 42
    msg = ipp.decode(data)
    names = list(msg.group(ipp.OPERATION))
    assert names == ["attributes-charset", "attributes-natural-language", "printer-uri"]
    assert data.endswith(bytes([ipp.END]))


def test_every_value_kind_round_trips():
    job = [
        Attr.of(ipp.INTEGER, "copies", 3),
        Attr.of(ipp.BOOLEAN, "last-document", True),
        Attr.of(ipp.ENUM, "printer-state", 5),
        Attr.of(ipp.KEYWORD, "sides", "two-sided-long-edge"),
        Attr(ipp.RANGE, "page-ranges", [ipp.Range(1, 3), ipp.Range(5, 5)]),
        Attr.of(ipp.RESOLUTION, "printer-resolution", ipp.Resolution(600, 600)),
        ipp.media_col(21590, 27940),
        Attr.of(ipp.MIME_TYPE, "document-format", "image/pwg-raster"),
        Attr.of(ipp.NAME, "job-name", "Cover letter ✓"),
    ]
    msg = ipp.decode(_response([(ipp.OPERATION, []), (ipp.JOB, job)]))
    g = msg.group(ipp.JOB)
    assert g["copies"] == [3]
    assert g["last-document"] == [True]
    assert g["page-ranges"] == [ipp.Range(1, 3), ipp.Range(5, 5)]
    assert g["printer-resolution"] == [ipp.Resolution(600, 600)]
    assert str(g["printer-resolution"][0]) == "600x600dpi"
    col = g["media-col"][0]
    size = col["media-size"][0]
    assert size == {"x-dimension": [21590], "y-dimension": [27940]}
    assert g["job-name"] == ["Cover letter ✓"]


def test_nested_collections_and_one_set_of_collections():
    db = [
        {"media-size": Attr.of(ipp.BEGIN_COLLECTION, "", {"x-dimension": Attr.of(ipp.INTEGER, "", 21590),
                                                           "y-dimension": Attr.of(ipp.INTEGER, "", 27940)}),
         "media-source": Attr.of(ipp.KEYWORD, "", "main")},
        {"media-size": Attr.of(ipp.BEGIN_COLLECTION, "", {"x-dimension": Attr.of(ipp.INTEGER, "", 21000),
                                                           "y-dimension": Attr.of(ipp.INTEGER, "", 29700)})},
    ]
    msg = ipp.decode(_response([(ipp.PRINTER, [Attr(ipp.BEGIN_COLLECTION, "media-col-ready", db),
                                               Attr.of(ipp.INTEGER, "after", 1)])]))
    g = msg.group(ipp.PRINTER)
    assert len(g["media-col-ready"]) == 2
    assert g["media-col-ready"][0]["media-source"] == ["main"]
    assert g["media-col-ready"][1]["media-size"][0]["x-dimension"] == [21000]
    assert g["after"] == [1]


def test_every_two_digit_success_status_counts():
    assert ipp.is_success(0x0000)
    assert ipp.is_success(0x0001)  # Brother's ignored-or-substituted
    assert ipp.is_success(0x0002)
    assert not ipp.is_success(0x0400)
    assert not ipp.is_success(0x0507)


def test_strings_are_capped_and_stripped_of_control_characters():
    hostile = "Ready\x1b[31m\r\nrm -rf /" + "x" * 5000
    msg = ipp.decode(_response([(ipp.PRINTER, [Attr.of(ipp.TEXT, "printer-state-message", hostile)])]))
    v = msg.group(ipp.PRINTER)["printer-state-message"][0]
    assert "\x1b" not in v and "\r" not in v and "\n" not in v
    assert len(v) <= ipp.MAX_STRING


def test_values_per_attribute_are_capped():
    attr = Attr(ipp.KEYWORD, "media-supported", ["m%d" % i for i in range(3000)])
    msg = ipp.decode(_response([(ipp.PRINTER, [attr])]))
    assert len(msg.group(ipp.PRINTER)["media-supported"]) == ipp.MAX_VALUES_PER_ATTRIBUTE


@pytest.mark.parametrize("mutate,why", [
    (lambda b: b[:-1], "missing end"),
    (lambda b: b[:20], "truncated"),
    (lambda b: b + b"\x00" * (ipp.MAX_RESPONSE + 1), "too large"),
])
def test_malformed_answers_are_errors_not_guesses(mutate, why):
    good = _response([(ipp.PRINTER, [Attr.of(ipp.KEYWORD, "sides-supported", "one-sided")])])
    with pytest.raises(ipp.IppDecodeError):
        ipp.decode(mutate(good))


def test_unbalanced_and_deep_collections_are_refused():
    begin = bytes([ipp.BEGIN_COLLECTION]) + struct.pack("!H", 1) + b"c" + struct.pack("!H", 0)
    member = bytes([ipp.MEMBER_NAME, 0, 0]) + struct.pack("!H", 1) + b"m"
    nested = bytes([ipp.BEGIN_COLLECTION, 0, 0, 0, 0])
    head = struct.pack("!BBHI", 2, 0, 0, 1) + bytes([ipp.PRINTER])
    # never closed
    with pytest.raises(ipp.IppDecodeError):
        ipp.decode(head + begin + bytes([ipp.END]))
    # nested past MAX_DEPTH
    deep = head + begin + (member + nested) * (ipp.MAX_DEPTH + 2)
    with pytest.raises(ipp.IppDecodeError):
        ipp.decode(deep + bytes([ipp.END]))


def test_bad_fixed_length_values_are_refused():
    head = struct.pack("!BBHI", 2, 0, 0, 1) + bytes([ipp.PRINTER])
    bad = bytes([ipp.INTEGER]) + struct.pack("!H", 1) + b"x" + struct.pack("!H", 3) + b"abc"
    with pytest.raises(ipp.IppDecodeError):
        ipp.decode(head + bad + bytes([ipp.END]))
