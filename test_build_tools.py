"""Cross-platform regression checks for release tooling and CLI entry points."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

from build_exe import build_command
import trigger


def test_pyinstaller_arguments_and_asset_pairing():
    root = Path(__file__).resolve().parent
    cmd = build_command(root, python_executable="python")
    assert cmd[:3] == ["python", "-m", "PyInstaller"]
    assert cmd[cmd.index("--icon") + 1] == str(root / "assets" / "voxtype.ico")
    assert cmd[cmd.index("--add-data") + 1] == f"{root / 'assets'}{os.pathsep}assets"
    assert cmd[-1] == str(root / "run.py")


def test_build_never_packages_private_configuration():
    cmd = build_command(Path(__file__).resolve().parent, python_executable="python")
    assert not any("config.json" in part or ".env" in part for part in cmd)


def test_cli_entrypoint_dispatches_requested_action():
    with patch.object(sys, "argv", ["sadh-trigger", "cancel"]), patch.object(trigger, "trigger") as send:
        trigger.main()
        send.assert_called_once_with("cancel")


def test_cli_entrypoint_defaults_to_toggle():
    with patch.object(sys, "argv", ["sadh-trigger"]), patch.object(trigger, "trigger") as send:
        trigger.main()
        send.assert_called_once_with("toggle")
