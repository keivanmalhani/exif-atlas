"""The console script.

Packaging is the part that breaks silently: everything imports in the
checkout and the installed command does not exist. These tests load the
entry point the way pip registers it and then run the real executable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib.metadata import entry_points

import pytest

from exif_atlas import __version__
from tests import fixtures as fx

DISTRIBUTION = "exif-atlas"
COMMAND = "exif-atlas"


def console_scripts():
    return {ep.name: ep for ep in entry_points(group="console_scripts")}


def test_the_console_script_is_registered():
    assert COMMAND in console_scripts(), (
        "%s is not installed; run pip install -e '.[dev]'" % DISTRIBUTION)


def test_the_entry_point_loads_and_is_callable():
    entry = console_scripts()[COMMAND]
    assert entry.value == "exif_atlas.cli:main"
    main = entry.load()
    assert callable(main)


def test_the_loaded_entry_point_is_the_same_function_as_the_module():
    from exif_atlas.cli import main
    assert console_scripts()[COMMAND].load() is main


def test_the_entry_point_runs_and_returns_an_exit_code(tmp_path, capsys):
    main = console_scripts()[COMMAND].load()
    root = str(tmp_path / "photos")
    fx.write(root, "a.jpg", fx.simple_jpeg())
    code = main(["scan", root, "-o", str(tmp_path / "atlas.html"), "--quiet"])
    assert code == 0
    assert os.path.isfile(str(tmp_path / "atlas.html"))


def test_the_declared_version_matches_the_package():
    from importlib.metadata import version
    assert version(DISTRIBUTION) == __version__


def executable():
    found = shutil.which(COMMAND, path=os.path.dirname(sys.executable))
    return found or shutil.which(COMMAND)


@pytest.mark.skipif(executable() is None,
                    reason="console script not on PATH")
def test_the_installed_command_reports_its_version():
    result = subprocess.run([executable(), "--version"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    assert __version__ in result.stdout


@pytest.mark.skipif(executable() is None,
                    reason="console script not on PATH")
def test_the_installed_command_scans_a_folder(tmp_path):
    root = str(tmp_path / "photos")
    for index in range(4):
        fx.write(root, "f%d.jpg" % index, fx.simple_jpeg())
    out = str(tmp_path / "atlas.html")
    result = subprocess.run([executable(), "scan", root, "-o", out],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "files per second" in result.stderr
    assert os.path.getsize(out) > 4000


def test_the_module_runs_as_a_script(tmp_path):
    """python -m exif_atlas.cli has to work for anyone without the script."""
    root = str(tmp_path / "photos")
    fx.write(root, "a.jpg", fx.simple_jpeg())
    result = subprocess.run(
        [sys.executable, "-m", "exif_atlas.cli", "scan", root,
         "-o", str(tmp_path / "a.html"), "--quiet"],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def test_the_package_has_no_runtime_dependencies():
    """The claim on the tin, checked against installed metadata."""
    from importlib.metadata import requires
    required = requires(DISTRIBUTION) or []
    hard = [line for line in required if "extra ==" not in line]
    assert hard == [], hard


def test_only_the_standard_library_is_imported():
    """Nothing under site-packages may be pulled in at import time."""
    import importlib
    import sysconfig

    site = sysconfig.get_paths()["purelib"]
    for name in ("exif_atlas.exif", "exif_atlas.analyze",
                 "exif_atlas.render", "exif_atlas.cli"):
        module = importlib.import_module(name)
        for attribute in vars(module).values():
            origin = getattr(attribute, "__file__", None)
            if isinstance(origin, str) and origin.startswith(site):
                assert "exif_atlas" in origin, origin
