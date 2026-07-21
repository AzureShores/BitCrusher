# size_controller.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class _Obs:
    v_bps: int
    actual_bytes: int


@dataclass
class SizeController:
    """
    API compatibility:
      - set_initial(seed_v_bps:int, seed_bytes:int) -> None
      - should_retry(actual_bytes:int) -> bool
      - next(actual_bytes:int) -> tuple[int,int]  (video_bps, audio_bps)

    Controller:
      - keeps monotone bounds:
          under_bound = highest bitrate that produced <= target
          over_bound  = lowest bitrate that produced  > target
      - estimates bytes-per-bit k via multi-observation regression
      - uses aggressive upscaling to force bracket creation when only undershoots exist
      - once bracketed, uses deterministic bisection toward the upper edge
      - can enforce no-overshoot policy with near-target acceptance window
    """

    target_bytes: int
    duration_s: float
    audio_bps: int
    container_overhead: float = 1.03
    min_v_bitrate: int = 140_000
    safety: float = 0.985
    close_tol: float = 0.005
    min_close_bytes: int = 131072
    max_iter: int = 4
    quality_mode: str = "balanced"
    # Default to the never-over policy: the size target is a hard ceiling, so a
    # default-constructed controller must not accept an overshoot. "legacy" is
    # still selectable but is likewise clamped strictly under the target below.
    target_policy: str = "no_overshoot_near_max"
    target_tolerance_pct: float = 0.25
    target_tolerance_min_bytes: int = 32768
    max_target_attempts: int = 8

    # internal deterministic state
    _iter: int = 0
    _obs: List[_Obs] = field(default_factory=list)
    _under_bound: int = 0
    _over_bound: int = 0
    _pending_v_bps: int = 0
    _last_actual_bytes: int = -1
    _same_size_runs: int = 0
    _stall_stop: bool = False
    _k_low: float = 0.55   # bytes/bit lower bound
    _k_high: float = 1.25  # bytes/bit upper bound

    def _hard_target(self) -> int:
        return int(self.target_bytes)

    def _is_quality_first(self) -> bool:
        return str(self.quality_mode or "").strip().lower() == "quality_first"

    def _is_no_overshoot_policy(self) -> bool:
        policy = str(self.target_policy or "").strip().lower()
        if policy == "no_overshoot_near_max":
            return True
        return self._is_quality_first()

    def _max_attempts(self) -> int:
        if self._is_no_overshoot_policy():
            return max(1, int(self.max_target_attempts or 1))
        return max(1, int(self.max_iter or 1))

    def _acceptance_tolerance(self) -> int:
        if self._is_no_overshoot_policy():
            pct = max(0.0, float(self.target_tolerance_pct)) / 100.0
            return int(max(int(self.target_bytes * pct), int(self.target_tolerance_min_bytes)))
        return int(max(int(self.target_bytes * float(self.close_tol)), int(self.min_close_bytes)))

    def _soft_target(self) -> int:
        win = int(self._acceptance_tolerance())
        if self._is_no_overshoot_policy():
            # Keep seed target slightly below hard target; bisection then maximizes under target.
            return int(max(1, int(self.target_bytes) - max(1, win // 4)))
        # Legacy behavior: center around target with slight under-bias.
        return int(max(1, int(self.target_bytes) - win // 2))

    def _audio_bytes(self, a_bps: Optional[int] = None) -> int:
        abps = int(self.audio_bps if a_bps is None else a_bps)
        return int(max(0.0, (abps * float(self.duration_s)) / 8.0))

    def _video_bytes_from_total(self, total_bytes: int, a_bps: Optional[int] = None) -> int:
        overhead = max(1.0, float(self.container_overhead))
        core = float(total_bytes) / overhead
        vbytes = int(max(0.0, core - float(self._audio_bytes(a_bps=a_bps))))
        return vbytes

    def _k_from_obs(self, v_bps: int, actual_bytes: int) -> float:
        # k = video_bytes / video_bits
        vbytes = self._video_bytes_from_total(int(actual_bytes))
        vbits = max(1.0, float(v_bps) * float(self.duration_s))
        return float(vbytes) * 8.0 / vbits

    def _predict_total_bytes(self, v_bps: int, k: float, a_bps: Optional[int] = None) -> int:
        v_bps = int(max(self.min_v_bitrate, int(v_bps)))
        k = float(min(self._k_high, max(self._k_low, float(k))))
        vbytes = (float(v_bps) * float(self.duration_s)) / 8.0 * k
        total = (vbytes + float(self._audio_bytes(a_bps=a_bps))) * max(1.0, float(self.container_overhead))
        return int(max(0.0, total))

    def set_initial(self, seed_v_bps: int, seed_bytes: int) -> None:
        self._iter = 0
        self._obs.clear()
        self._under_bound = 0
        self._over_bound = 0
        self._same_size_runs = 0
        self._last_actual_bytes = -1
        self._stall_stop = False

        # Initialize k bounds from first observation if possible.
        try:
            k0 = self._k_from_obs(int(seed_v_bps), int(seed_bytes))
            self._k_low = max(0.35, min(self._k_low, k0 * 0.97))
            self._k_high = min(1.80, max(self._k_high, k0 * 1.03))
        except Exception:
            pass

        seed_v = int(max(self.min_v_bitrate, int(seed_v_bps)))
        self._pending_v_bps = seed_v
        self._record_observation(seed_v, int(seed_bytes))
        self._last_actual_bytes = int(seed_bytes)

    def _within_close_window(self, actual_bytes: int) -> bool:
        hard = self._hard_target()
        actual = int(actual_bytes)
        win = int(max(0, self._acceptance_tolerance()))

        if self._is_no_overshoot_policy():
            low = max(0, hard - win)
            return low <= actual <= hard

        if self.close_tol <= 0.0:
            return actual <= hard
        # Strictly under the ceiling: an overshoot is never "close enough" to
        # stop the retry loop (a symmetric window used to accept files OVER the
        # target by up to `win` bytes, violating the never-over invariant).
        return (actual <= hard) and (hard - actual) <= win

    def should_retry(self, actual_bytes: int) -> bool:
        if self._within_close_window(int(actual_bytes)):
            return False
        if self._stall_stop:
            return False
        return self._iter < self._max_attempts()

    def _record_observation(self, v_bps: int, actual_bytes: int) -> None:
        hard = self._hard_target()
        v_bps = int(max(self.min_v_bitrate, int(v_bps)))
        actual = int(actual_bytes)

        self._obs.append(_Obs(v_bps, actual))

        # Monotone bitrate bounds.
        if actual > hard:
            if self._over_bound <= 0 or v_bps < self._over_bound:
                self._over_bound = v_bps
        else:
            if v_bps > self._under_bound:
                self._under_bound = v_bps

        # k bounds (bytes/bit) with directional widening.
        try:
            k = self._k_from_obs(v_bps, actual)
            if actual > hard:
                self._k_high = min(1.80, max(self._k_high, k))
                self._k_low = max(0.35, min(self._k_low, k * 0.985))
            else:
                self._k_low = max(0.35, min(self._k_low, k))
                self._k_high = min(1.80, max(self._k_high, k * 1.015))

            if self._k_low > self._k_high:
                mid = (self._k_low + self._k_high) * 0.5
                self._k_low = max(0.35, mid * 0.98)
                self._k_high = min(1.80, mid * 1.02)
        except Exception:
            pass

    def _fit_k(self) -> float:
        ks: List[float] = []
        for o in self._obs:
            try:
                ks.append(self._k_from_obs(o.v_bps, o.actual_bytes))
            except Exception:
                continue
        if not ks:
            return float(min(self._k_high, max(self._k_low, 0.85)))

        ks_sorted = sorted(ks)
        if len(ks_sorted) >= 5:
            ks_sorted = ks_sorted[1:-1]  # trim extremes deterministically
        k_hat = sum(ks_sorted) / float(len(ks_sorted))
        return float(min(self._k_high, max(self._k_low, k_hat)))

    def _apply_audio_guard(self, target_bytes: int) -> int:
        # Keep audio stable unless it consumes too much of the total budget.
        # In quality-first mode, be stricter to preserve more video bits.
        a = int(max(48_000, min(int(self.audio_bps), 384_000)))
        if self.duration_s <= 0:
            self.audio_bps = a
            return a
        total_bits = int(target_bytes) * 8
        total_bps = float(total_bits) / max(1.0, float(self.duration_s))
        share_cap = 0.035
        if self._is_quality_first():
            share_cap = 0.025 if total_bps < 2_000_000 else 0.030
        cap = int(max(48_000, min(384_000, (total_bits * share_cap) / max(1.0, float(self.duration_s)))))
        if a > cap:
            a = cap
        self.audio_bps = int(max(48_000, min(a, 384_000)))
        return self.audio_bps

    def _next_when_bracketed(self) -> int:
        lo = int(max(self.min_v_bitrate, self._under_bound))
        hi = int(max(self.min_v_bitrate, self._over_bound))
        if hi <= lo:
            return int(max(self.min_v_bitrate, int(lo * 1.01)))

        mid = lo + max(1, (hi - lo) // 2)
        if mid <= lo:
            mid = int(max(self.min_v_bitrate, int(lo * 1.003)))
        if mid >= hi:
            mid = int(max(self.min_v_bitrate, int(hi * 0.997)))
        return int(max(self.min_v_bitrate, mid))

    def _constrained_next_vbps(self, k_hat: float, actual_bytes: int, current_v_bps: int) -> int:
        soft = self._soft_target()
        hard = self._hard_target()
        current_v_bps = int(max(self.min_v_bitrate, int(current_v_bps)))
        actual = int(actual_bytes)

        a_bps = self._apply_audio_guard(hard)
        tol = int(self._acceptance_tolerance())

        # Bracketed: deterministic bisection toward max-under-target.
        if self._under_bound > 0 and self._over_bound > 0:
            return self._next_when_bracketed()

        # Only undershoots seen: push up until an overshoot sample exists.
        if self._under_bound > 0 and self._over_bound <= 0:
            deficit = max(0, hard - actual)
            ratio = float(hard) / max(1.0, float(actual))
            if deficit > max(tol, int(hard * 0.05)):
                # Far below target: jump aggressively to bracket quickly.
                scale = min(1.85, max(1.08, ratio * 1.03))
            else:
                # Near the target: a forced +8% minimum step used to overshoot
                # by design (e.g. 1.5% under -> +8% -> 5% over -> 3 bisection
                # re-encodes). Step proportionally to what's actually missing.
                scale = min(1.20, max(1.004, ratio * 1.015))
            vbps = int(max(self.min_v_bitrate, int(self._under_bound * scale)))
            pred = self._predict_total_bytes(vbps, k_hat, a_bps=a_bps)
            if pred <= hard - tol:
                boost = min(1.35, max(1.015, float(hard) / max(1.0, float(pred))))
                vbps = int(max(self.min_v_bitrate, int(vbps * boost)))
            return int(vbps)

        # Only overshoots seen: reduce aggressively to force an under sample.
        if self._over_bound > 0 and self._under_bound <= 0:
            ratio = float(hard) / max(1.0, float(actual))
            scale = max(0.55, min(0.97, ratio * 0.97))
            vbps = int(max(self.min_v_bitrate, int(self._over_bound * scale)))
            return int(vbps)

        # Fallback: model-based estimate before bounds are formed.
        overhead = max(1.0, float(self.container_overhead))
        core_budget = float(soft) / overhead - float(self._audio_bytes(a_bps=a_bps))
        core_budget = max(1.0, core_budget)

        vbytes_budget = core_budget
        vbits_budget = (vbytes_budget * 8.0) / max(1e-9, float(k_hat))
        vbps = int(vbits_budget / max(1e-9, float(self.duration_s)))
        vbps = int(max(self.min_v_bitrate, vbps))
        if vbps <= current_v_bps:
            vbps = int(max(self.min_v_bitrate, int(current_v_bps * 1.02)))
        return int(vbps)

    # --- Public helpers for external search loops (e.g. Max-Quality packing) ---
    def estimate_k(self) -> float:
        """Current bytes-per-bit estimate from all observations."""
        return float(self._fit_k())

    def record_external(self, v_bps: int, actual_bytes: int) -> None:
        """Feed an observation produced outside the retry loop into the model."""
        self._record_observation(int(v_bps), int(actual_bytes))

    def next(self, actual_bytes: int) -> tuple[int, int]:
        current_v = int(max(self.min_v_bitrate, int(self._pending_v_bps or self.min_v_bitrate)))
        actual = int(actual_bytes)

        self._record_observation(current_v, actual)
        if self._last_actual_bytes >= 0 and int(self._last_actual_bytes) == actual:
            self._same_size_runs += 1
        else:
            self._same_size_runs = 0
        self._last_actual_bytes = actual
        self._iter += 1

        k_hat = self._fit_k()
        next_v = self._constrained_next_vbps(k_hat=k_hat, actual_bytes=actual, current_v_bps=current_v)
        step_pct = abs(float(next_v - current_v)) / max(1.0, float(current_v))
        if step_pct < 0.005 and self._same_size_runs >= 1:
            self._stall_stop = True

        self._pending_v_bps = int(max(self.min_v_bitrate, next_v))
        return int(next_v), int(self.audio_bps)


def next_relaxed_target(current_mb: float, max_mb: float,
                        step: float = 1.25) -> float | None:
    """Quality-floor auto-relax ladder (opt-in feature).

    When the min-VMAF floor is unreachable within the size cap, the caller
    may raise the cap multiplicatively toward a user-set maximum. Returns
    the next cap in MB, or None when the ladder is exhausted (current
    already at/above max). The user-set max is the new hard ceiling - the
    default-off behavior of "never exceed the target" is unchanged.
    """
    try:
        cur = float(current_mb)
        cap = float(max_mb)
        s = float(step)
    except Exception:
        return None
    if cur <= 0 or cap <= 0 or s <= 1.0:
        return None
    if cur >= cap:
        return None
    nxt = cur * s
    return round(min(nxt, cap), 3)

