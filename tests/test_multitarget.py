"""Two-target export tests: size_tag output naming, also-target parsing,
CLI flag plumbing."""
import os

import BitCrusherV9 as bc
from encode.output_paths import _build_output_path


def test_size_tag_appended_to_output_name(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    out = _build_output_path("video", str(src), str(tmp_path),
                             {"size_tag": "25MB"}, "mp4")
    assert os.path.basename(out) == "clip_25MB.mp4"


def test_size_tag_stacks_with_prefix_suffix(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    out = _build_output_path(
        "video", str(src), str(tmp_path),
        {"output_prefix": "sm_", "output_suffix": "_x", "size_tag": "8MB"},
        "mp4")
    assert os.path.basename(out) == "sm_clip_x_8MB.mp4"


def test_no_size_tag_unchanged(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    out = _build_output_path("video", str(src), str(tmp_path / "o"), {}, "mp4")
    assert os.path.basename(out) == "clip.mp4"


def test_parse_also_targets_string_forms():
    assert bc._parse_also_targets("25") == [25.0]
    assert bc._parse_also_targets("25, 8") == [8.0, 25.0]
    assert bc._parse_also_targets("8,8,25") == [8.0, 25.0]      # deduped
    assert bc._parse_also_targets("abc, 25, -3, 0") == [25.0]   # junk dropped
    assert bc._parse_also_targets("") == []
    assert bc._parse_also_targets(None) == []


def test_parse_also_targets_list_forms():
    assert bc._parse_also_targets([25, 8.5]) == [8.5, 25.0]
    assert bc._parse_also_targets([]) == []


def test_cli_flag_repeatable():
    p = bc.build_arg_parser()
    args = p.parse_args(["in.mp4", "-t", "8",
                         "--also-target", "25", "--also-target", "50"])
    assert args.also_target == [25.0, 50.0]
    args2 = p.parse_args(["in.mp4"])
    assert args2.also_target is None


def test_normalize_drop_path_strips_wrappers():
    # The quote-stripping branch was mojibake-corrupted into startswith()
    # with no args, which raised and skipped normpath entirely.
    assert bc._normalize_drop_path('{C:/x/y.mp4}') == os.path.normpath("C:/x/y.mp4")
    assert bc._normalize_drop_path('"C:/x/y.mp4"') == os.path.normpath("C:/x/y.mp4")
    assert bc._normalize_drop_path("'C:/x/y.mp4'") == os.path.normpath("C:/x/y.mp4")
    assert bc._normalize_drop_path("C:/x/y.mp4") == os.path.normpath("C:/x/y.mp4")
