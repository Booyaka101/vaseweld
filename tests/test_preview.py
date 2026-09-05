"""The interactive preview: a self-contained page anyone can open without installing."""

from __future__ import annotations

import json
import re

import pytest

from conftest import fixture
from vaseweld.parser import parse_file
from vaseweld.preview import build, read_banner, render, write
from vaseweld.weld import weld


@pytest.fixture(scope="module")
def welded_file(tmp_path_factory, ps_normal, ps_vase):
    result = weld(ps_normal, ps_vase, [12.4, 30.0])
    path = tmp_path_factory.mktemp("preview") / "welded.gcode"
    path.write_text(
        result.newline.join(result.lines) + result.newline, encoding="utf-8", newline=""
    )
    return parse_file(path)


def embedded(html: str) -> dict:
    match = re.search(r"const DATA = (.*?);\nconst COLOUR", html, re.S)
    assert match, "the page must embed its data"
    return json.loads(match.group(1))


def test_the_banner_recovers_the_weld_layout(welded_file):
    sections, cuts = read_banner(welded_file)
    assert [(s.role, s.first, s.last) for s in sections] == [
        ("normal", 1, 61),
        ("vase", 62, 149),
        ("normal", 150, 200),
    ]
    assert cuts == [62, 150]


def test_a_plain_slicer_file_still_previews(ps_vase):
    preview = build(ps_vase)
    assert preview.sections == []
    data = embedded(render(preview))
    assert len(data["layers"]) == 200
    assert set(data["roles"]) == {"normal"}


def test_every_layer_carries_geometry(welded_file):
    data = embedded(render(build(welded_file)))
    assert len(data["layers"]) == len(welded_file.layers)
    assert all(layer["paths"] for layer in data["layers"])
    # Points are (x, y) pairs, so every path has an even count of at least four.
    for layer in data["layers"]:
        for path in layer["paths"]:
            assert len(path["p"]) >= 4 and len(path["p"]) % 2 == 0
            assert 0.0 < path["w"] < 2.0


def test_bounds_match_the_object(welded_file):
    data = embedded(render(build(welded_file)))
    min_x, min_y, max_x, max_y = data["bounds"]
    assert max_x - min_x == pytest.approx(32.3, abs=1.0)  # 20 mm object plus its skirt
    assert max_y - min_y == pytest.approx(32.3, abs=1.0)


def test_the_page_is_self_contained(welded_file, tmp_path):
    html = render(build(welded_file))
    assert not re.search(r"__[A-Z]+__", html), "a placeholder was left unfilled"
    assert "<script" in html and "src=" not in html.split("<script")[1][:200]
    assert "http://" not in html and "https://" not in html
    assert html.count("<script") == 1

    page = write(tmp_path / "out.html", welded_file.path)
    assert page.read_text(encoding="utf-8") == html


def test_the_script_is_wrapped_so_globals_cannot_collide(welded_file):
    """A bare `const top` at global scope silently kills the whole script.

    window.top is non-configurable, so declaring it lexically makes the script fail
    at scope instantiation. Nothing throws where you can see it: the page renders
    and does nothing. Wrapping the script is what stops that whole class of bug.
    """
    html = render(build(welded_file))
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    assert script.lstrip().startswith("//") or script.lstrip().startswith("(function")
    assert "(function () {" in script
    assert script.rstrip().endswith("})();")
    for reserved in (" top ", " top=", " name ", " length ", " parent ", " self "):
        assert f"const{reserved}" not in script
        assert f"let{reserved}" not in script


def test_cut_layers_are_flagged_for_the_readout(welded_file):
    data = embedded(render(build(welded_file)))
    assert data["cuts"] == [62, 150]
    assert data["roles"][61] == "vase"
    assert data["roles"][60] == "normal"
    assert data["roles"][149] == "normal"


def test_preview_refuses_binary_gcode(tmp_path):
    from vaseweld.parser import GcodeError

    with pytest.raises(GcodeError, match="binary G-code"):
        write(tmp_path / "x.html", fixture("binary_6mm.bgcode"))
