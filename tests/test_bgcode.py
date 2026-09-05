import struct

import pytest
from conftest import fixture
from vaseweld.bgcode import BgcodeError, decode, heatshrink, is_bgcode, meatpack
from vaseweld.parser import parse_file
from vaseweld.validate import check
from vaseweld.weld import weld


def commands(lines):
    """Command lines with their trailing comment removed, which binarising drops."""
    out = []
    for line in lines:
        if line.lstrip().startswith(";") or not line.strip():
            continue
        out.append(line.split(";")[0].rstrip())
    return out


def test_the_decoded_file_carries_the_same_commands_as_its_text_twin():
    # Both fixtures came from one model and one set of slicer flags, so every
    # command must survive binarising. Only the trailing comments are lost.
    binary = decode(fixture("binary_6mm.bgcode").read_bytes()).splitlines()
    text = fixture("prusaslicer_normal_6mm.gcode").read_text(encoding="utf-8").splitlines()
    assert commands(binary) == commands(text)


def test_the_config_block_comes_back_where_the_parser_looks_for_it():
    doc = parse_file(fixture("binary_6mm.bgcode"))
    assert doc.config["layer_height"] == "0.2"
    assert doc.config["nozzle_diameter"] == "0.4"
    assert doc.config["binary_gcode"] == "1"
    assert len(doc.config) == 347


def test_the_print_totals_come_back_as_comments():
    lines = decode(fixture("binary_6mm.bgcode").read_bytes()).splitlines()
    assert "; filament used [mm] = 292.17" in lines
    assert "; estimated printing time (normal mode) = 4m 44s" in lines


def test_a_binary_pair_welds_and_checks_out(tmp_path):
    normal = parse_file(fixture("binary_6mm.bgcode"))
    vase = parse_file(fixture("binary_vase_6mm.bgcode"))
    result = weld(normal, vase, [3.0])
    out = tmp_path / "out.gcode"
    out.write_text(result.newline.join(result.lines) + result.newline, encoding="utf-8", newline="")
    assert check(out).ok


def test_a_binary_weld_matches_the_same_weld_from_text():
    binary = weld(
        parse_file(fixture("binary_6mm.bgcode")),
        parse_file(fixture("binary_vase_6mm.bgcode")),
        [3.0],
    )
    text = weld(
        parse_file(fixture("prusaslicer_normal_6mm.gcode")),
        parse_file(fixture("prusaslicer_vase_6mm.gcode")),
        [3.0],
    )
    assert binary.cut_z == text.cut_z
    assert binary.cut_layer == text.cut_layer
    assert commands(binary.lines) == commands(text.lines)


def test_is_bgcode_only_matches_the_magic():
    assert is_bgcode(b"GCDE\x01\x00\x00\x00\x01\x00")
    assert not is_bgcode(b"G28 ; home")
    assert not is_bgcode(b"")


@pytest.mark.parametrize(
    "raw, expected",
    [
        pytest.param(b"GCDE", "too short", id="truncated-header"),
        pytest.param(b"NOPE" + b"\x00" * 6, "GCDE magic", id="bad-magic"),
        pytest.param(b"GCDE" + struct.pack("<IH", 2, 0), "version 2", id="future-version"),
        pytest.param(b"GCDE" + struct.pack("<IH", 1, 7), "checksum type 7", id="bad-checksum"),
        pytest.param(
            b"GCDE" + struct.pack("<IH", 1, 0) + struct.pack("<HHIIH", 1, 9, 4, 4, 0) + b"xxxx",
            "compression algorithm 9",
            id="unknown-compression",
        ),
        pytest.param(
            b"GCDE" + struct.pack("<IH", 1, 0) + struct.pack("<HHIH", 0, 0, 4, 0) + b"a=b\n",
            "no G-code blocks",
            id="metadata-only",
        ),
    ],
)
def test_broken_files_say_what_is_wrong(raw, expected):
    with pytest.raises(BgcodeError, match=expected):
        decode(raw)


def test_a_truncated_heatshrink_stream_is_not_silently_short():
    with pytest.raises(BgcodeError, match="ended early"):
        heatshrink(b"\x80", 12, 4, 64)


def test_heatshrink_literals_and_backreferences():
    # 1 + 'A', 1 + 'B', then a backreference two bytes back, two bytes long.
    bits = "1" + f"{ord('A'):08b}" + "1" + f"{ord('B'):08b}" + "0" + f"{1:012b}" + f"{1:04b}"
    bits += "0" * (-len(bits) % 8)
    data = bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))
    assert heatshrink(data, 12, 4, 4) == b"ABAB"


def test_heatshrink_copies_a_run_that_overlaps_itself():
    # A backreference one byte back and four long is how a repeated byte is stored.
    bits = "1" + f"{ord('z'):08b}" + "0" + f"{0:012b}" + f"{3:04b}"
    bits += "0" * (-len(bits) % 8)
    data = bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))
    assert heatshrink(data, 12, 4, 5) == b"zzzzz"


ENABLE = bytes([0xFF, 0xFF, 251])
DISABLE = bytes([0xFF, 0xFF, 250])


def test_meatpack_passes_bytes_through_until_packing_is_enabled():
    assert meatpack(b"G28\n", False) == "G28\n"


def test_meatpack_unpacks_two_characters_per_byte():
    # low nibble first: 0x21 is '1' then '2'.
    assert meatpack(ENABLE + bytes([0x21]), False) == "12"


def test_meatpack_reads_a_full_width_character_after_a_0xf_nibble():
    # low nibble 5 packs to '5'; the high nibble 0xF means the next byte is literal.
    assert meatpack(ENABLE + bytes([0xF5, ord("Y")]), False) == "5Y"


def test_meatpack_emits_an_out_of_order_pair_in_the_right_order():
    # low nibble 0xF, high nibble 3: the literal byte comes first in the stream but
    # second in the output, so '3' has to wait for it.
    assert meatpack(ENABLE + bytes([0x3F, ord("M")]), False) == "M3"


def test_meatpack_drops_the_high_nibble_after_a_newline():
    # 0x?C packs a newline in the low nibble; whatever is in the high nibble is padding.
    assert meatpack(ENABLE + bytes([0x9C]), False) == "\n"


def test_meatpack_stops_unpacking_on_the_disable_command():
    assert meatpack(ENABLE + bytes([0x21]) + DISABLE + b"G1", False) == "12G1"


def test_meatpack_puts_the_g_line_spaces_back_only_when_asked():
    # 'G','1','X','2' packed two to a byte, then a newline.
    packed = bytes([0x1D, 0xF0 | 0xE, ord("2"), 0x0C])
    assert meatpack(ENABLE + packed, False) == "G1X2\n"
    assert meatpack(ENABLE + packed, True) == "G1 X2\n"
