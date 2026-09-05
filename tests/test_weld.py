from __future__ import annotations

import pytest

from conftest import fixture
from vaseweld.parser import parse_e_mode, parse_file, parse_g92_e, parse_move
from vaseweld.weld import WeldError, state_at, weld

CUT_Z = 12.4
CUT_LAYER = 62


def physical_extrusion(gcode, start, end, e_start=0.0):
    """Millimetres of filament the source file pushes over ``lines[start:end]``."""
    relative = gcode.relative_e
    position = e_start
    total = 0.0
    for line in gcode.lines[start:end]:
        mode = parse_e_mode(line)
        if mode is not None:
            relative = mode
            continue
        reset = parse_g92_e(line)
        if reset is not None:
            position = reset
            continue
        move = parse_move(line)
        if move is None or move.e is None:
            continue
        if relative:
            total += move.e
            position += move.e
        else:
            total += round(move.e - position, 5)
            position = move.e
    return total


def output_extrusion(lines):
    return sum(
        move.e for move in (parse_move(line) for line in lines) if move and move.e is not None
    )


@pytest.fixture(scope="module")
def welded(ps_normal, ps_vase):
    return weld(ps_normal, ps_vase, CUT_Z)


def test_layer_membership_either_side_of_the_cut(welded):
    assert welded.cut_layer == CUT_LAYER
    assert welded.cut_z == pytest.approx(12.4)
    assert welded.bottom_range == (1, 61)
    assert welded.top_range == (62, 200)
    assert welded.bottom_z == (pytest.approx(0.2), pytest.approx(12.2))
    assert welded.top_z == (pytest.approx(12.4), pytest.approx(40.0))


def test_summary_is_the_documented_wording(welded):
    assert welded.summary() == [
        "cut snapped to Z=12.400 (layer 62)",
        "normal: layers 1-61 (Z 0.200-12.200)",
        "vase: layers 62-200 (Z 12.400-40.000)",
        "E mode: absolute -> relative (converted)",
        "transition ramp: 0.80 -> 1.00 over layer 62",
    ]


def test_output_is_relative_e_throughout(welded):
    modes = [parse_e_mode(line) for line in welded.lines]
    assert True in modes
    assert False not in modes
    assert any("G92 E0" in line for line in welded.lines)


@pytest.mark.parametrize(
    "requested, expected_z, expected_layer",
    [(12.4, 12.4, 62), (12.5, 12.4, 62), (12.39, 12.2, 61), (0.4, 0.4, 2), (40.0, 40.0, 200)],
)
def test_cut_snaps_down_to_a_real_layer(ps_normal, ps_vase, requested, expected_z, expected_layer):
    result = weld(ps_normal, ps_vase, requested)
    assert result.cut_z == pytest.approx(expected_z)
    assert result.cut_layer == expected_layer


def test_snapping_is_reported_when_it_happens(ps_normal, ps_vase):
    result = weld(ps_normal, ps_vase, 12.5)
    assert result.summary()[0] == "requested Z=12.500 is between layers, snapping down"


@pytest.mark.parametrize("cut", [0.2, 0.35, 40.2, -1.0])
def test_cut_outside_the_range_names_the_range(ps_normal, ps_vase, cut):
    with pytest.raises(WeldError) as excinfo:
        weld(ps_normal, ps_vase, cut)
    message = str(excinfo.value)
    assert "0.400" in message and "40.000" in message


def test_absolute_to_relative_round_trips_to_the_same_extrusion(ps_normal, ps_vase):
    """With the flow ramp switched off the welded file must move exactly as much filament."""
    cut_start = ps_vase.layers[CUT_LAYER - 1].start
    expected = physical_extrusion(ps_normal, 0, ps_normal.layers[CUT_LAYER - 2].end)
    expected += physical_extrusion(
        ps_vase, cut_start, len(ps_vase.lines), e_start=state_at(ps_vase, cut_start).e_pos
    )
    plain = weld(
        ps_normal,
        ps_vase,
        CUT_Z,
        start_flow=1.0,
        seam_retract=False,
    )
    assert output_extrusion(plain.lines) == pytest.approx(expected, abs=1e-6)


def decode_layer(gcode, layer):
    """(delta, xy distance) per line of a layer, or None for lines without an E word.

    Starts from the state the source file itself was in, which is what the weld
    restores at the seam.
    """
    start = state_at(gcode, layer.start)
    position, x, y = start.e_pos, start.x, start.y
    decoded = []
    for line in gcode.lines[layer.start : layer.end]:
        reset = parse_g92_e(line)
        if reset is not None:
            position = reset
            decoded.append(None)
            continue
        move = parse_move(line)
        if move is None:
            decoded.append(None)
            continue
        distance = 0.0
        if (move.x is not None or move.y is not None) and x is not None and y is not None:
            distance = (
                ((move.x if move.x is not None else x) - x) ** 2
                + ((move.y if move.y is not None else y) - y) ** 2
            ) ** 0.5
        x = move.x if move.x is not None else x
        y = move.y if move.y is not None else y
        if move.e is None:
            decoded.append(None)
            continue
        delta = move.e if gcode.relative_e else round(move.e - position, 5)
        position = position + move.e if gcode.relative_e else move.e
        decoded.append((delta, distance))
    return decoded


SEAM = "; vaseweld: seam"


def bridge_lines(result):
    return [line for line in result.lines if SEAM in line]


@pytest.mark.parametrize("flow", [0.8, 0.5, 0.0])
def test_ramp_scales_the_transition_layer_exactly(ps_normal, ps_vase, flow):
    """Every extrusion on layer 62 is scaled by flow + fraction * (1 - flow)."""
    result = weld(
        ps_normal,
        ps_vase,
        CUT_Z,
        start_flow=flow,
        seam_retract=False,
    )
    layer = ps_vase.layers[CUT_LAYER - 1]
    start = next(i for i, line in enumerate(result.lines) if "weld boundary" in line) + 2
    window = result.lines[start : start + (layer.end - layer.start) + len(bridge_lines(result))]
    got = [line for line in window if SEAM not in line][: layer.end - layer.start]

    decoded = decode_layer(ps_vase, layer)
    total = sum(
        dist for value in decoded if value and value[0] > 0 and value[1] > 0 for dist in [value[1]]
    )
    travelled = 0.0
    factors = []
    for out_line, value in zip(got, decoded):
        if value is None:
            continue
        delta, distance = value
        if delta > 0 and distance > 0:
            travelled += distance
            factor = flow + travelled / total * (1.0 - flow)
            factors.append(factor)
            expected = round(delta * factor, 5)
        else:
            expected = delta
        assert parse_move(out_line).e == pytest.approx(expected, abs=1e-6)

    assert len(factors) > 50
    assert factors[0] == pytest.approx(flow, abs=0.02)
    assert factors[-1] == pytest.approx(1.0, abs=1e-9)


def test_vase_first_ramps_the_last_vase_layer_down(ps_normal, ps_vase):
    result = weld(ps_normal, ps_vase, CUT_Z, first_role="vase")
    assert result.summary()[1] == "vase: layers 1-61 (Z 0.200-12.200)"
    assert result.summary()[2] == "normal: layers 62-200 (Z 12.400-40.000)"
    assert result.summary()[4] == "transition ramp: 1.00 -> 0.25 over layer 61"


def test_arc_moves_pass_through_untouched(small_vase):
    arcs = parse_file(fixture("arcfit_normal_6mm.gcode"))
    result = weld(arcs, small_vase, 3.0)
    source = {
        line.split("E")[0].strip()
        for line in arcs.lines[: arcs.layers[13].end]
        if line.startswith(("G2 ", "G3 "))
    }
    welded_arcs = {
        line.split("E")[0].strip() for line in result.lines if line.startswith(("G2 ", "G3 "))
    }
    assert source
    assert source == welded_arcs


def test_klipper_layer_markers_are_rewritten(klipper_normal, klipper_vase):
    result = weld(klipper_normal, klipper_vase, 3.0)
    totals = [line for line in result.lines if "TOTAL_LAYER" in line and line.startswith("SET_")]
    assert totals == ["SET_PRINT_STATS_INFO TOTAL_LAYER=30"]
    currents = [
        int(line.rsplit("=", 1)[1])
        for line in result.lines
        if line.startswith("SET_PRINT_STATS_INFO CURRENT_LAYER")
    ]
    assert currents == sorted(currents)
    assert max(currents) == 30


def test_orca_progress_and_material_are_recomputed(orca_normal, orca_vase):
    result = weld(orca_normal, orca_vase, CUT_Z)
    assert "M73 progress remapped" in result.stats_note
    starts = [line for line in result.lines if line.startswith("M73")][0]
    assert starts.startswith("M73 P0 R")
    used = next(float(line.split("=")[1]) for line in result.lines if "filament used [mm]" in line)
    assert used == pytest.approx(output_extrusion(result.lines), abs=0.01)
    assert used < float(orca_normal.config.get("filament_diameter", 1.75)) * 10_000


def test_time_and_progress_are_stripped_when_unknowable(welded):
    assert "time estimate stripped" in welded.stats_note
    assert not [line for line in welded.lines if line.startswith("M73")]
    assert not [line for line in welded.lines if "estimated printing time" in line]


def test_config_block_reports_relative_e(welded):
    assert "; use_relative_e_distances = 1" in welded.lines


def test_provenance_banner_names_both_inputs(welded):
    banner = "\n".join(welded.lines[:8])
    assert "vaseweld" in banner
    assert "prusaslicer_normal_40mm.gcode (normal)" in banner
    assert "prusaslicer_vase_40mm.gcode (vase)" in banner


def test_seam_retract_is_added_only_when_the_layer_lacks_one(ps_normal, ps_vase):
    forward = weld(ps_normal, ps_vase, CUT_Z)
    assert [line.split(SEAM)[1].strip() for line in bridge_lines(forward)] == [
        "retract",
        "travel to where the next slice expects the nozzle",
        "unretract",
    ]
    # The normal slice retracts and travels at every layer change, so the seam needs nothing.
    backward = weld(ps_normal, ps_vase, CUT_Z, first_role="vase")
    assert bridge_lines(backward) == []


def test_removals_are_reported_only_when_something_was_removed(welded, orca_normal, orca_vase):
    assert welded.stats_removed == ["the printing time estimate"]
    orca = weld(orca_normal, orca_vase, CUT_Z)
    assert orca.stats_removed == []


def test_bambustudio_layout_is_understood(bambu_normal, bambu_vase):
    """BambuStudio puts its config block at the top and marks layers differently."""
    assert len(bambu_normal.layers) == 200
    assert bambu_normal.layers[0].z == pytest.approx(0.2)
    assert bambu_normal.layers[-1].z == pytest.approx(40.0)
    assert bambu_normal.config["printer_model"] == "Bambu Lab P1P"
    assert bambu_normal.relative_e is True
    assert len(bambu_vase.layers) == 200


@pytest.mark.parametrize("cut", [12.4, 25.0])
@pytest.mark.parametrize("direction", ["normal-first", "vase-first"])
@pytest.mark.parametrize("slicer", ["prusa", "orca", "bambu"])
def test_every_weld_survives_check(request, tmp_path, slicer, direction, cut):
    """Three slicers, both directions, two cut heights: all of it has to be printable."""
    from vaseweld.validate import check

    normal = request.getfixturevalue(
        {"prusa": "ps_normal", "orca": "orca_normal", "bambu": "bambu_normal"}[slicer]
    )
    vase = request.getfixturevalue(
        {"prusa": "ps_vase", "orca": "orca_vase", "bambu": "bambu_vase"}[slicer]
    )
    result = weld(normal, vase, cut, first_role=direction.removesuffix("-first"))

    path = tmp_path / "welded.gcode"
    path.write_text(
        result.newline.join(result.lines) + result.newline, encoding="utf-8", newline=""
    )
    report = check(path)
    assert report.ok, " | ".join([report.summary()] + [str(p) for p in report.problems])


def test_seam_correction_precedes_the_layer_that_primes_itself(bambu_normal, bambu_vase):
    """BambuStudio's normal layers unretract themselves, so the fix has to come first."""
    result = weld(bambu_normal, bambu_vase, CUT_Z, first_role="vase")
    boundary = next(i for i, line in enumerate(result.lines) if "weld boundary" in line)
    window = result.lines[boundary : boundary + 12]
    correction = next(i for i, line in enumerate(window) if SEAM in line)
    own_prime = next(i for i, line in enumerate(window) if line.strip() == "G1 E0.8 F1800")
    assert correction < own_prime


def test_seam_matches_the_retraction_state_each_slicer_leaves(
    ps_normal, ps_vase, bambu_normal, bambu_vase
):
    """BambuStudio retracts at the end of a layer, PrusaSlicer at the start of the next."""
    from vaseweld.weld import state_at

    assert state_at(bambu_normal, bambu_normal.layers[60].end).retracted == pytest.approx(
        0.8, abs=1e-3
    )
    assert state_at(ps_normal, ps_normal.layers[60].end).retracted == pytest.approx(0.0, abs=1e-6)

    bambu = weld(bambu_normal, bambu_vase, CUT_Z)
    seam = [line.split(SEAM)[1].strip() for line in bridge_lines(bambu)]
    assert seam == ["travel to where the next slice expects the nozzle", "unretract"]

    prusa = weld(ps_normal, ps_vase, CUT_Z)
    seam = [line.split(SEAM)[1].strip() for line in bridge_lines(prusa)]
    assert seam == ["retract", "travel to where the next slice expects the nozzle", "unretract"]


def test_bambu_vase_first_corrects_the_retraction_the_other_way(bambu_normal, bambu_vase):
    result = weld(bambu_normal, bambu_vase, CUT_Z, first_role="vase")
    assert [line.split(SEAM)[1].strip() for line in bridge_lines(result)] == ["retract"]


def test_bambustudio_header_totals_are_recomputed(bambu_normal, bambu_vase):
    result = weld(bambu_normal, bambu_vase, CUT_Z)
    header = {
        line.split(":", 1)[0].strip(" ;"): line.split(":", 1)[1].strip()
        for line in result.lines[:20]
        if line.startswith(("; total filament", "; model printing time"))
    }
    length = float(header["total filament length [mm]"])
    assert length == pytest.approx(output_extrusion(result.lines), abs=0.01)
    assert length < 1833.60  # the source normal slice on its own
    # BambuStudio labels the volume cm^3 but computes mm^3; keep its convention.
    volume = float(header["total filament volume [cm^3]"])
    assert volume == pytest.approx(length * 3.14159265 * (1.75 / 2) ** 2, rel=1e-4)
    assert "38m" not in header["model printing time"]
    assert "; total layer number: 200" in result.lines[:20]
    assert "; use_relative_e_distances = 1" in result.lines


def test_crlf_input_round_trips_to_crlf_output(tmp_path, small_normal, small_vase):
    """A file saved with Windows line endings has to come back out that way."""
    pair = []
    for source in (small_normal, small_vase):
        path = tmp_path / source.path.name
        path.write_bytes(("\r\n".join(source.lines) + "\r\n").encode())
        pair.append(parse_file(path))
    assert pair[0].newline == "\r\n"

    result = weld(pair[0], pair[1], 3.0)
    out = tmp_path / "out.gcode"
    out.write_text(result.newline.join(result.lines) + result.newline, encoding="utf-8", newline="")
    raw = out.read_bytes()
    assert raw.count(b"\r\n") == len(result.lines)
    assert raw.count(b"\n") == raw.count(b"\r\n")  # no bare LF anywhere


def test_forced_weld_warns_when_the_seam_step_is_wrong(small_normal):
    """--force can squeeze a thick layer into a thin gap; say so rather than stay quiet."""
    thick = parse_file(fixture("mismatch_layerheight_6mm.gcode"))
    result = weld(small_normal, thick, 3.0)
    assert len(result.warnings) == 1
    assert "0.200 mm step" in result.warnings[0]
    assert "0.300 mm layers" in result.warnings[0]
    assert "150%" in result.warnings[0]


def test_a_matched_weld_warns_about_nothing(welded):
    assert welded.warnings == []


def bead_widths(gcode, layer_index, *, layer_height=0.2, filament=1.75):
    """(length, bead width) for every extruding move of one layer.

    Same rectangle-with-round-ends model sim/deposit.py uses, in the standard
    library so the regression guard costs nothing to run.
    """
    import math

    from vaseweld.parser import walk

    layer = gcode.layers[layer_index - 1]
    area_of_filament = math.pi * (filament / 2.0) ** 2
    cursor = state_at(gcode, layer.start)
    out = []
    for step in walk(gcode.lines[layer.start : layer.end], cursor):
        if step.kind != "move" or not step.delta or step.delta <= 0 or step.dist <= 0:
            continue
        area = step.delta * area_of_filament / step.dist
        width = (area + layer_height**2 * (1 - math.pi / 4)) / layer_height
        out.append((step.dist, width))
    return out


def reparse(result, tmp_path):
    """Round-trip a WeldResult through a file so it can be measured as shipped."""
    path = tmp_path / "welded.gcode"
    path.write_text(
        result.newline.join(result.lines) + result.newline, encoding="utf-8", newline=""
    )
    return parse_file(path)


def malformed_lines(gcode, layer_index):
    """Long moves whose bead is nowhere near the layer's own nominal width.

    A hand splice produces one or the other depending on the extruder mode: a
    starved thread dragged across the part when the file is relative E, and a
    gorged blob when it is absolute E and the values are read as positions.
    """
    import statistics

    widths = bead_widths(gcode, layer_index)
    nominal = statistics.median(width for _, width in widths)
    return [
        (length, width)
        for length, width in widths
        if length > 1.0 and not 0.5 * nominal <= width <= 2.0 * nominal
    ]


def slices_for(request, slicer):
    return (
        request.getfixturevalue(
            {"prusa": "ps_normal", "orca": "orca_normal", "bambu": "bambu_normal"}[slicer]
        ),
        request.getfixturevalue(
            {"prusa": "ps_vase", "orca": "orca_vase", "bambu": "bambu_vase"}[slicer]
        ),
    )


@pytest.mark.parametrize("slicer", ["prusa", "orca", "bambu"])
def test_the_weld_layer_lays_a_proper_bead(request, tmp_path, slicer):
    """Every printing move at the weld has to be a wall, not a thread or a blob.

    Measured on the welded output, so it fails if the seam ever stops travelling to
    where the slice above expects the nozzle.
    """
    normal, vase = slices_for(request, slicer)
    result = weld(normal, vase, CUT_Z)
    bad = malformed_lines(reparse(result, tmp_path), CUT_LAYER)
    assert not bad, f"{slicer}: malformed beads at the weld layer: {bad}"


@pytest.mark.parametrize(
    "slicer, mode", [("prusa", "absolute"), ("orca", "relative"), ("bambu", "relative")]
)
def test_the_guard_catches_a_hand_splice(request, tmp_path, slicer, mode):
    """Prove the check above can fail, using the splice people do in a text editor.

    The defect takes two shapes: a starved thread dragged across the part when the
    files are relative E, and a blob when they are absolute and every E value after
    the paste is read as a position.
    """
    normal, vase = slices_for(request, slicer)
    naive = (
        normal.lines[: normal.layers[CUT_LAYER - 1].start]
        + vase.lines[vase.layers[CUT_LAYER - 1].start :]
    )
    path = tmp_path / "naive.gcode"
    path.write_text("\n".join(naive) + "\n", encoding="utf-8", newline="")

    bad = malformed_lines(parse_file(path), CUT_LAYER)
    assert bad, f"{slicer}: the hand splice should be caught and is not"
    length, width = max(bad, key=lambda pair: pair[0])
    # A spiral segment is about 0.5 mm; these run across the open part.
    assert length > 3.0
    assert width < 0.1 if mode == "relative" else width > 5.0


# --- more than one weld point -------------------------------------------------

SECOND_CUT_Z = 30.0
SECOND_CUT_LAYER = 150


@pytest.fixture(scope="module")
def sandwiched(ps_normal, ps_vase):
    """Solid base, vase body, solid lid: the shape the slicer issues keep asking for."""
    return weld(ps_normal, ps_vase, [CUT_Z, SECOND_CUT_Z])


def test_two_cuts_make_three_slabs(sandwiched):
    assert [slab.role for slab in sandwiched.slabs] == ["normal", "vase", "normal"]
    assert [slab.layers for slab in sandwiched.slabs] == [(1, 61), (62, 149), (150, 200)]
    assert sandwiched.slabs[1].z == (pytest.approx(12.4), pytest.approx(29.8))
    assert [cut.layer for cut in sandwiched.cuts] == [CUT_LAYER, SECOND_CUT_LAYER]


def test_two_cuts_ramp_in_and_back_out(sandwiched):
    assert sandwiched.ramps == [
        "0.80 -> 1.00 over layer 62",
        "1.00 -> 0.25 over layer 149",
    ]
    assert sandwiched.summary()[:4] == [
        "cuts snapped to Z=12.400 (layer 62), Z=30.000 (layer 150)",
        "normal: layers 1-61 (Z 0.200-12.200)",
        "vase: layers 62-149 (Z 12.400-29.800)",
        "normal: layers 150-200 (Z 30.000-40.000)",
    ]


def test_two_cuts_survive_check_and_lay_proper_beads(sandwiched, tmp_path):
    from vaseweld.validate import check

    welded = reparse(sandwiched, tmp_path)
    report = check(welded.path)
    assert report.ok, " | ".join([report.summary()] + [str(p) for p in report.problems])
    for layer in (CUT_LAYER, SECOND_CUT_LAYER):
        assert not malformed_lines(welded, layer), f"bad beads at layer {layer}"


def test_cuts_are_sorted_however_they_arrive(ps_normal, ps_vase, sandwiched):
    shuffled = weld(ps_normal, ps_vase, [SECOND_CUT_Z, CUT_Z])
    assert shuffled.summary() == sandwiched.summary()


def test_two_cuts_on_the_same_layer_are_refused(ps_normal, ps_vase):
    with pytest.raises(WeldError, match="same layer"):
        weld(ps_normal, ps_vase, [12.4, 12.45])


def test_a_one_layer_vase_section_says_so(ps_normal, ps_vase):
    result = weld(ps_normal, ps_vase, [12.4, 12.6])
    assert result.slabs[1].layers == (62, 62)
    assert result.ramps == ["0.80 -> 1.00 over layer 62"]
    assert any("single layer" in warning for warning in result.warnings)


def test_provenance_names_every_slab(sandwiched):
    banner = "\n".join(sandwiched.lines[:8])
    assert "Z=12.400 (layer 62), Z=30.000 (layer 150)" in banner
    assert banner.count("prusaslicer_normal_40mm.gcode (normal)") == 2
    assert "prusaslicer_vase_40mm.gcode (vase)" in banner
