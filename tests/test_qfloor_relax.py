"""Quality-floor auto-relax ladder tests (encode/size_controller.py).

Opt-in feature: when the min-VMAF floor is unreachable in-budget, the cap
may grow in 1.25x steps toward a user-set maximum. Default OFF - the
regression suite separately proves the ceiling invariant is untouched.
"""
import BitCrusherV9 as bc
from encode.size_controller import next_relaxed_target


def test_ladder_steps_multiplicatively():
    assert next_relaxed_target(8, 25) == 10.0
    assert next_relaxed_target(10, 25) == 12.5
    assert next_relaxed_target(20.48, 25) == 25.0   # clamped to max


def test_ladder_exhausts_at_max():
    assert next_relaxed_target(25, 25) is None
    assert next_relaxed_target(26, 25) is None


def test_invalid_inputs_return_none():
    assert next_relaxed_target(0, 25) is None
    assert next_relaxed_target(8, 0) is None
    assert next_relaxed_target(-1, 25) is None
    assert next_relaxed_target(8, 25, step=1.0) is None
    assert next_relaxed_target(8, 25, step=0.5) is None
    assert next_relaxed_target("junk", 25) is None


def test_ladder_terminates():
    mb, hops = 5.0, 0
    while True:
        nxt = next_relaxed_target(mb, 100.0)
        if nxt is None:
            break
        assert nxt > mb
        mb = nxt
        hops += 1
        assert hops < 20  # log_1.25(20) ~ 14 steps max
    assert mb == 100.0


def test_defaults_off():
    assert bc.ADVANCED_DEFAULTS["qfloor_autorelax"] is False
    assert bc.ADVANCED_DEFAULTS["qfloor_relax_max_mb"] == 0


def test_cli_flag_wires_adv():
    p = bc.build_arg_parser()
    args = p.parse_args(["in.mp4", "--min-vmaf", "92",
                         "--min-vmaf-relax-max", "25"])
    adv = bc._build_adv_from_args(args)
    assert adv["qfloor_autorelax"] is True
    assert adv["qfloor_relax_max_mb"] == 25.0
    args2 = p.parse_args(["in.mp4"])
    adv2 = bc._build_adv_from_args(args2)
    assert adv2["qfloor_autorelax"] is False
