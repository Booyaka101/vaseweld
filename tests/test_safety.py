"""Input preservation and failed-write regressions for generated printer files."""

import os

import pytest
from conftest import fixture, weld_argv
from test_auto import auto_argv

from vaseweld.cli import EXIT_USAGE, main
from vaseweld.files import atomic_write_text


def test_auto_rejects_nonzero_exit_even_when_gcode_exists(fake_slicer, tmp_path, capsys):
    fake_slicer.returncode = 1
    fake_slicer.stderr = "post-processing failed\n"
    output = tmp_path / "out.gcode"
    assert main(auto_argv(fake_slicer, output)) == EXIT_USAGE
    assert len(fake_slicer.slices) == 1
    assert not output.exists()
    message = capsys.readouterr().err
    assert "exit 1" in message
    assert "post-processing failed" in message


@pytest.mark.parametrize("role", ["normal", "spiral"])
def test_kept_slice_cannot_overwrite_a_load_config(fake_slicer, tmp_path, capsys, role):
    config = tmp_path / f"cylinder_6mm-{role}.gcode"
    config.write_bytes(b"layer_height = 0.2\n")
    original = config.read_bytes()
    output = tmp_path / "out.gcode"
    args = auto_argv(fake_slicer, output, extra=["--load", str(config),
                                               "--keep-slices", str(tmp_path)])
    assert main(args) == EXIT_USAGE
    assert not fake_slicer.slices
    assert config.read_bytes() == original
    assert not output.exists()
    assert "--load config" in capsys.readouterr().err


def test_preview_cannot_overwrite_a_hardlinked_input(tmp_path, capsys):
    source = tmp_path / "source.gcode"
    source.write_bytes(fixture("prusaslicer_normal_6mm.gcode").read_bytes())
    alias = tmp_path / "preview.html"
    try:
        alias.hardlink_to(source)
    except OSError:
        pytest.skip("hard links unavailable")
    original = source.read_bytes()
    assert main(["preview", str(source), "-o", str(alias)]) == EXIT_USAGE
    assert source.read_bytes() == alias.read_bytes() == original
    assert "itself" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["weld", "preview", "hook"])
def test_failed_output_replacement_preserves_previous_file(tmp_path, monkeypatch, capsys, command):
    output = tmp_path / "out.gcode"
    previous = fixture("prusaslicer_vase_6mm.gcode").read_bytes()
    output.write_bytes(previous)

    def cannot_replace(*args, **kwargs):
        raise PermissionError("file is locked")

    monkeypatch.setattr(os, "replace", cannot_replace)
    if command == "weld":
        args = weld_argv(output=output)
    elif command == "preview":
        args = ["preview", str(fixture("prusaslicer_normal_6mm.gcode")), "-o", str(output)]
    else:
        args = weld_argv(vase=None, trailing=output)
    assert main(args) == EXIT_USAGE
    assert output.read_bytes() == previous
    assert not list(tmp_path.glob(".vaseweld-*"))
    assert "cannot write" in capsys.readouterr().err


def test_partial_write_preserves_previous_file(tmp_path, monkeypatch):
    import vaseweld.files

    output = tmp_path / "out.gcode"
    output.write_bytes(b"previous complete output\n")
    real_temporary_file = vaseweld.files.tempfile.NamedTemporaryFile

    def failing_temporary_file(*args, **kwargs):
        stream = real_temporary_file(*args, **kwargs)

        def partial_write(text):
            stream.file.write(text[:5])
            raise OSError("disk full")

        stream.write = partial_write
        return stream

    monkeypatch.setattr(vaseweld.files.tempfile, "NamedTemporaryFile", failing_temporary_file)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(output, "replacement output\n")
    assert output.read_bytes() == b"previous complete output\n"
    assert not list(tmp_path.glob(".vaseweld-*"))


def test_atomic_write_follows_an_existing_symlink(tmp_path):
    target = tmp_path / "target.gcode"
    target.write_bytes(b"old\n")
    link = tmp_path / "hook.gcode"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    atomic_write_text(link, "new\n", newline="")
    assert link.is_symlink()
    assert target.read_bytes() == link.read_bytes() == b"new\n"
