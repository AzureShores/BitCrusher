"""Explorer context-menu preset builders (support/context_menu.py).

Pure builders only - registry writes are Windows-only side effects and
are never exercised from pytest.
"""
import BitCrusherV9 as bc
from support.context_menu import (PRESETS, VIDEO_EXTS, build_command,
                                  preset_key_paths)


def test_key_paths_exact_and_scoped():
    paths = preset_key_paths()
    assert len(paths) == len(PRESETS) * len(VIDEO_EXTS)
    # HKCU SystemFileAssociations only - never the global *\shell.
    assert all(p.startswith(r"Software\Classes\SystemFileAssociations")
               for p in paths)
    assert all(r"\shell\BitCrusher." in p for p in paths)
    assert not any("*" in p for p in paths)
    assert len(set(paths)) == len(paths)


def test_build_command_shape():
    cmd = build_command(10)
    assert "--enqueue" in cmd
    assert "--enqueue-target 10" in cmd
    assert cmd.rstrip().endswith('"%1"')


def test_cli_enqueue_target_flag():
    p = bc.build_arg_parser()
    args = p.parse_args(["--enqueue", "x.mp4", "--enqueue-target", "10"])
    assert args.enqueue_target == 10.0
    args2 = p.parse_args(["--enqueue", "x.mp4"])
    assert args2.enqueue_target is None
