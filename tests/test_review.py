"""Independent modal-state and binary-integrity regressions from the full review."""

import json
import struct
import zlib

import pytest

from vaseweld.bgcode import BgcodeError, decode
from vaseweld.compat import first_layer_footprint
from vaseweld.parser import parse_file, parse_move
from vaseweld.preview import Preview, Section, render
from vaseweld.weld import weld


def binary_block(payload, *, compression=0, size=None, encoding=0, checksum=True, kind=1):
    header = struct.pack("<HHI", kind, compression, len(payload) if size is None else size)
    if compression:
        header += struct.pack("<I", len(payload))
    block = header + struct.pack("<H", encoding) + payload
    return (b"GCDE" + struct.pack("<IH", 1, int(checksum)) + block
            + (struct.pack("<I", zlib.crc32(block)) if checksum else b""))


def test_binary_crc_is_verified_before_commands_are_used():
    raw = binary_block(b"G1 X10 E1\n")
    assert decode(raw) == "G1 X10 E1\n"
    damaged = raw.replace(b"X10", b"X90")
    with pytest.raises(BgcodeError, match="checksum"):
        decode(damaged)


@pytest.mark.parametrize("missing", [1, 2, 3, 4])
def test_binary_checksum_must_be_complete(missing):
    raw = binary_block(b"G1 X10 E1\n")
    with pytest.raises(BgcodeError, match="checksum"):
        decode(raw[:-missing])


def test_binary_deflate_must_match_the_declared_size():
    raw = binary_block(zlib.compress(b"G1 X10 E1\n"), compression=1, size=100)
    with pytest.raises(BgcodeError, match="size"):
        decode(raw)


@pytest.mark.parametrize("kind, encoding", [(1, 3), (2, 1)])
def test_unknown_binary_encoding_is_not_interpreted_as_plain_text(kind, encoding):
    raw = binary_block(b"G1 X10 E1\n", kind=kind, encoding=encoding)
    with pytest.raises(BgcodeError, match="encoding"):
        decode(raw)


def modal_print_feedrates(lines):
    feed = None
    for line in lines:
        move = parse_move(line)
        if move is None:
            continue
        if move.f is not None:
            feed = move.f
        if move.e is not None and move.e > 0 and move.x is not None:
            yield feed


@pytest.mark.parametrize("seam_retract", [False, True])
@pytest.mark.parametrize("cuts", [[0.4], [0.4, 0.6]])
@pytest.mark.parametrize("explicit_layer_feed", [False, True])
def test_splice_preserves_modal_print_feedrates(tmp_path, seam_retract, cuts, explicit_layer_feed):
    def sliced(name, feed, spiral):
        path = tmp_path / name
        lines = ["M83", f"G1 X0 Y0 F{feed}"]
        for layer in range(1, 4):
            lines += [";LAYER_CHANGE", f";Z:{layer * 0.2:.1f}",
                      f"G1 Z{layer * 0.2:.1f}"]
            if not spiral:
                lines += ["G1 X0 Y0"]
            # A slicer may set the print speed before the first printing move.
            if explicit_layer_feed:
                lines += [f"G1 F{feed}"]
            lines += [f"G1 X{layer * 10} Y10 E1"]
        lines += ["M104 S0", "; prusaslicer_config = begin",
                  "; retract_length = 0.8", "; prusaslicer_config = end"]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return parse_file(path)

    normal = sliced("normal.gcode", 600, False)
    vase = sliced("vase.gcode", 1200, True)
    result = weld(normal, vase, cuts, seam_retract=seam_retract)
    expected = [600, 1200, 600 if len(cuts) == 2 else 1200]
    assert list(modal_print_feedrates(result.lines)) == expected


def test_preview_treats_filenames_and_provenance_as_data():
    name = "</title><script>alert(1)</script>__DATA__"
    source = "</span><script>alert(2)</script>__NORMAL__"
    preview = Preview(name=name)
    preview.sections = [Section(role="vase", source=source, first=1, last=1)]
    page = render(preview)
    assert "</title><script>alert(1)" not in page
    assert "</span><script>alert(2)" not in page
    assert "&lt;/title&gt;" in page
    assert "&lt;/span&gt;" in page
    assert r"\u003c/script>" in page
    assert "__DATA__ - vaseweld preview</title>" in page
    assert "__NORMAL__" in page
    embedded = page.split("const DATA = ", 1)[1].split(";\nconst COLOUR", 1)[0]
    data = json.loads(embedded)
    assert data["name"] == name
    assert data["sections"][0]["source"] == source


def test_absolute_retracting_wipe_is_not_part_of_object_footprint(tmp_path):
    path = tmp_path / "wipe.gcode"
    path.write_text(
        "M82\nG92 E0\nG1 X0 Y0 Z0.2\n;LAYER_CHANGE\n;Z:0.2\n"
        "G1 X10 Y0 E1\nG1 X10 Y10 E2\nG1 X0 Y10 E3\nG1 X0 Y0 E4\n"
        "G1 X100 Y100 E3.2\n;LAYER_CHANGE\n;Z:0.4\nG1 Z0.4\nG1 X10 Y10 E4.2\n"
        "M104 S0\n", encoding="utf-8",
    )
    footprint = first_layer_footprint(parse_file(path))
    assert footprint.max_x == footprint.max_y == 10


@pytest.mark.parametrize("pair", [("ps_normal", "ps_vase"), ("orca_normal", "orca_vase"),
                                  ("bambu_normal", "bambu_vase")])
@pytest.mark.parametrize("first_role", ["normal", "vase"])
def test_every_retained_motion_keeps_its_source_feedrate(request, pair, first_role):
    sources = dict(zip(("normal", "vase"), (request.getfixturevalue(name) for name in pair)))
    last_role = "vase" if first_role == "normal" else "normal"
    first, last = sources[first_role], sources[last_role]

    def speeds(lines):
        feed = None
        result = {}
        for index, line in enumerate(lines):
            move = parse_move(line)
            if move is None:
                continue
            if move.f is not None:
                feed = move.f
            if any(value is not None for value in (move.x, move.y, move.z, move.e)):
                if "; vaseweld: seam" not in line:
                    result[index] = feed
        return result

    first_end = next(layer.start for layer in first.layers if layer.z >= 12.4)
    last_start = next(layer.start for layer in last.layers if layer.z >= 12.4)
    expected = [feed for index, feed in speeds(first.lines).items() if index < first_end]
    expected += [feed for index, feed in speeds(last.lines).items() if index >= last_start]
    result = weld(sources["normal"], sources["vase"], 12.4, first_role=first_role)
    assert list(speeds(result.lines).values()) == expected
