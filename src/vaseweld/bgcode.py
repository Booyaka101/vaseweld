"""Read PrusaSlicer's binary G-code back into the text form the rest of vaseweld expects.

Binarising a slice moves two things out of the G-code stream that vaseweld needs: the
config block, which becomes a Slicer Metadata block, and the filament and time totals,
which become a Print Metadata block. Without them the compatibility check has no fields
to compare and the totals cannot be recomputed, so this puts both back where PrusaSlicer
would have written them in a text file.

The trailing comment on every command (``G28 ; home all axes``) is not recoverable. The
binariser drops it, which is a good part of why the format is smaller.

Format: https://github.com/prusa3d/libbgcode/blob/main/doc/specifications.md
"""

from __future__ import annotations

import struct
import zlib

MAGIC = b"GCDE"

_FILE_METADATA = 0
_GCODE = 1
_SLICER_METADATA = 2
_PRINT_METADATA = 4
_THUMBNAIL = 5

_DEFLATE = 1
_HEATSHRINK_11_4 = 2
_HEATSHRINK_12_4 = 3

_MEATPACK = 1
_MEATPACK_COMMENTS = 2

# The 16 characters MeatPack can pack two-to-a-byte. 0xF means "not packed, the real
# byte follows"; 0xB is a space unless the no-spaces command swapped it for 'E'.
_PACKED = "0123456789. \nGX"
_SIGNAL = 0xFF
_ENABLE_PACKING = 251
_DISABLE_PACKING = 250
_RESET_ALL = 249
_ENABLE_NO_SPACES = 247
_DISABLE_NO_SPACES = 246

# G-line parameters, which the binariser is free to drop the space in front of.
_PARAMETERS = frozenset("XYZEFIJRSGPWHCA")

_CONFIG_MARKERS = {
    "OrcaSlicer": ("; CONFIG_BLOCK_START", "; CONFIG_BLOCK_END"),
    "BambuStudio": ("; CONFIG_BLOCK_START", "; CONFIG_BLOCK_END"),
    "SuperSlicer": ("; SuperSlicer_config = begin", "; SuperSlicer_config = end"),
}
_DEFAULT_MARKERS = ("; prusaslicer_config = begin", "; prusaslicer_config = end")


class BgcodeError(ValueError):
    """The file claims to be binary G-code but cannot be read as one."""


def is_bgcode(raw: bytes) -> bool:
    return raw[:4] == MAGIC


def heatshrink(data: bytes, window_bits: int, lookahead_bits: int, size: int) -> bytes:
    """Decompress a heatshrink stream, stopping at ``size`` bytes.

    Tokens are read most significant bit first: a 1 bit introduces an 8-bit literal, a 0
    bit a backreference of ``window_bits`` index and ``lookahead_bits`` count, both stored
    one less than their real value. The final byte is zero-padded, so the caller's
    uncompressed size is what ends the stream rather than running out of bits.
    """
    out = bytearray()
    append = out.append
    acc = held = pos = 0
    end_of_data = len(data)
    # The widest token is the tag plus a whole backreference; refilling to that once
    # per token keeps the bit twiddling out of a helper, which this loop runs millions
    # of times on a real file.
    wanted = 1 + max(8, window_bits + lookahead_bits)
    index_mask = (1 << window_bits) - 1
    count_mask = (1 << lookahead_bits) - 1

    while len(out) < size:
        while held < wanted and pos < end_of_data:
            acc = (acc << 8) | data[pos]
            pos += 1
            held += 8
        if held < 1:
            break

        held -= 1
        literal = (acc >> held) & 1
        acc &= (1 << held) - 1

        if literal:
            if held < 8:
                break
            held -= 8
            append((acc >> held) & 0xFF)
            acc &= (1 << held) - 1
            continue

        if held < window_bits + lookahead_bits:
            break
        held -= window_bits
        index = (acc >> held) & index_mask
        held -= lookahead_bits
        count = ((acc >> held) & count_mask) + 1
        acc &= (1 << held) - 1

        start = len(out) - index - 1
        if start < 0:
            raise BgcodeError("backreference points before the start of the stream")
        stop = start + count
        if stop <= len(out):
            out += out[start:stop]
        else:
            # The match runs into what it is producing, which is how a run of one
            # repeated byte is stored. It has to be copied a byte at a time.
            for i in range(start, stop):
                append(out[i])

    if len(out) < size:
        raise BgcodeError(f"stream ended early, got {len(out)} of {size} bytes")
    return bytes(out[:size])


def meatpack(data: bytes, respace: bool) -> str:
    """Unpack a MeatPack stream, optionally putting the G-line spaces back."""
    chars: list[str] = []
    packing = no_spaces = command = False
    signals = pending = 0
    held: str | None = None

    def unpack(nibble: int) -> str:
        return "E" if nibble == 0xB and no_spaces else _PACKED[nibble]

    def receive(byte: int) -> None:
        nonlocal pending, held
        if not packing:
            chars.append(chr(byte))
            return
        if pending:
            # A full-width character owed from the previous byte. If its partner was
            # packed it was decoded already and has been waiting for this one.
            chars.append(chr(byte))
            if held is not None:
                chars.append(held)
                held = None
            pending -= 1
            return
        low, high = byte & 0xF, byte >> 4
        if low == 0xF:
            pending += 1
            if high == 0xF:
                pending += 1
            else:
                held = unpack(high)
            return
        first = unpack(low)
        chars.append(first)
        if first == "\n":
            return
        if high == 0xF:
            pending += 1
        else:
            chars.append(unpack(high))

    for byte in data:
        if byte == _SIGNAL:
            if signals:
                command, signals = True, 0
            else:
                signals = 1
            continue
        if command:
            if byte == _ENABLE_PACKING:
                packing = True
            elif byte in (_DISABLE_PACKING, _RESET_ALL):
                packing = False
            elif byte == _ENABLE_NO_SPACES:
                no_spaces = True
            elif byte == _DISABLE_NO_SPACES:
                no_spaces = False
            command = False
            continue
        if signals:
            receive(_SIGNAL)
            signals = 0
        receive(byte)

    return _respace(chars) if respace else "".join(chars)


def _respace(chars: list[str]) -> str:
    """Re-insert the spaces between G-line parameters that the binariser dropped."""
    out: list[str] = []
    spacing = False
    for char in chars:
        starts_line = not out or out[-1] == "\n"
        opened = False
        if char == "G" and starts_line:
            spacing = opened = True
        elif char == "\n":
            spacing = False
        if not opened and spacing and char in _PARAMETERS and (not out or out[-1] != " "):
            out.append(" ")
        if char != "\n" or not out or out[-1] != "\n":
            out.append(char)
    return "".join(out)


def _blocks(raw: bytes):
    """Yield ``(type, parameter, payload)`` for every block, decompressed."""
    if len(raw) < 10:
        raise BgcodeError("file is too short to hold a header")
    magic, version, checksum = struct.unpack_from("<4sIH", raw, 0)
    if magic != MAGIC:
        raise BgcodeError("missing the GCDE magic number")
    if version != 1:
        raise BgcodeError(f"version {version} is not supported, only version 1 is")
    if checksum > 1:
        raise BgcodeError(f"unknown checksum type {checksum}")

    at = 10
    while at < len(raw):
        try:
            btype, compression = struct.unpack_from("<HH", raw, at)
            at += 4
            (size,) = struct.unpack_from("<I", raw, at)
            at += 4
            stored = size
            if compression:
                (stored,) = struct.unpack_from("<I", raw, at)
                at += 4
            if btype == _THUMBNAIL:
                parameter = 0
                at += 6
            else:
                (parameter,) = struct.unpack_from("<H", raw, at)
                at += 2
        except struct.error as exc:
            raise BgcodeError("truncated block header") from exc

        payload = raw[at : at + stored]
        if len(payload) < stored:
            raise BgcodeError("truncated block data")
        at += stored + (4 if checksum else 0)

        if btype == _THUMBNAIL:
            continue
        if compression == _DEFLATE:
            try:
                payload = zlib.decompress(payload)
            except zlib.error as exc:
                raise BgcodeError(f"deflate block would not decompress ({exc})") from exc
        elif compression in (_HEATSHRINK_11_4, _HEATSHRINK_12_4):
            window = 11 if compression == _HEATSHRINK_11_4 else 12
            payload = heatshrink(payload, window, 4, size)
        elif compression:
            raise BgcodeError(f"unknown compression algorithm {compression}")
        yield btype, parameter, payload


def decode(raw: bytes) -> str:
    """Turn binary G-code into the text file the same slice would have produced."""
    gcode: list[str] = []
    producer = ""
    totals: list[str] = []
    config: list[str] = []

    for btype, parameter, payload in _blocks(raw):
        if btype == _GCODE:
            if parameter in (_MEATPACK, _MEATPACK_COMMENTS):
                gcode.append(meatpack(payload, parameter == _MEATPACK_COMMENTS))
            else:
                gcode.append(payload.decode("utf-8", errors="replace"))
            continue
        text = payload.decode("utf-8", errors="replace")
        if btype == _FILE_METADATA:
            producer = _entries(text).get("Producer", "")
        elif btype == _PRINT_METADATA:
            totals = [f"; {key} = {value}" for key, value in _entries(text).items()]
        elif btype == _SLICER_METADATA:
            config = [f"; {key} = {value}" for key, value in _entries(text).items()]

    if not gcode:
        raise BgcodeError("no G-code blocks in the file")

    lines = "".join(gcode).splitlines()
    lines += totals
    if config:
        begin, end = _DEFAULT_MARKERS
        for name, markers in _CONFIG_MARKERS.items():
            if producer.startswith(name):
                begin, end = markers
        lines += ["", begin, *config, end]
    return "\n".join(lines) + "\n"


def _entries(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            entries[key.strip()] = value.strip()
    return entries
