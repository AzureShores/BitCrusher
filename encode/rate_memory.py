# rate_memory.py
"""Job-scoped memory of measured rate -> size behaviour.

`k` (output bytes per bit of video budget) depends on source, encoder and
delivery resolution but not on the target size, so a multi-target export
(`--also-target`) measures it once instead of rediscovering it per target.

Only seeds the first attempt's bitrate: the size controller still encodes,
measures and enforces the ceiling, so a bad estimate costs a retry, never a
breach. Not a quality predictor - picks no encoder, resolution or CRF.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# SizeController's own clamp range. Outside it the encode was not rate-targeted
# (passthrough, stream copy, failed pass) and must not seed anything.
K_MIN = 0.35
K_MAX = 1.80

# Gate for fitting k's drift with bitrate. See RateMemory._drift_fit.
MIN_FIT_OBS = 4
MIN_FIT_SPREAD = 1.40  # max/min observed bitrate


@dataclass(frozen=True)
class RateKey:
    """Everything `k` depends on; equal keys are comparable encodes.

    `source` includes size+mtime so editing the input invalidates the memory.
    """
    source: Tuple[str, int, int]
    encoder: str
    width: int
    height: int
    flags: Tuple[Tuple[str, str], ...] = ()


def make_key(input_path: str, encoder: str, width: int, height: int,
             flags: Optional[dict] = None) -> RateKey:
    """Build a RateKey, tolerating a missing/unstatable source file."""
    try:
        st = os.stat(input_path)
        src = (os.path.normcase(os.path.abspath(input_path)),
               int(st.st_size), int(st.st_mtime_ns))
    except Exception:
        src = (os.path.normcase(os.path.abspath(str(input_path or ""))), 0, 0)
    norm = tuple(sorted((str(k), str(v)) for k, v in (flags or {}).items()
                        if v not in (None, "", False)))
    return RateKey(source=src, encoder=str(encoder or "").lower(),
                   width=int(width or 0), height=int(height or 0), flags=norm)


@dataclass
class _Obs:
    v_bps: int
    total_bytes: int
    k: float


def compute_k(v_bps: int, total_bytes: int, duration_s: float,
              audio_bps: int, overhead: float) -> Optional[float]:
    """Bytes of video per bit of video budget, or None if not derivable.

    Mirrors SizeController._k_from_obs so both mean the same thing.
    """
    try:
        v_bps = int(v_bps)
        dur = float(duration_s)
        if v_bps <= 0 or dur <= 0 or int(total_bytes) <= 0:
            return None
        ov = max(1.0, float(overhead or 1.0))
        core = float(total_bytes) / ov
        audio_bytes = max(0.0, float(audio_bps or 0) * dur / 8.0)
        vbytes = core - audio_bytes
        if vbytes <= 0:
            return None
        k = (vbytes * 8.0) / (float(v_bps) * dur)
    except Exception:
        return None
    if not (K_MIN <= k <= K_MAX):
        return None
    return float(k)


@dataclass
class RateMemory:
    """Observations for one job, bucketed by operating point."""

    _by_key: Dict[RateKey, List[_Obs]] = field(default_factory=dict)
    max_per_key: int = 12

    def observe(self, key: RateKey, v_bps: int, total_bytes: int,
                duration_s: float, audio_bps: int, overhead: float) -> bool:
        """Record one measured (bitrate -> produced bytes) point.

        Out-of-range points are dropped, not clamped; a clamped outlier would
        bias every later estimate.
        """
        k = compute_k(v_bps, total_bytes, duration_s, audio_bps, overhead)
        if k is None:
            return False
        obs = self._by_key.setdefault(key, [])
        obs.append(_Obs(int(v_bps), int(total_bytes), k))
        if len(obs) > int(self.max_per_key):
            del obs[0:len(obs) - int(self.max_per_key)]
        return True

    def count(self, key: RateKey) -> int:
        return len(self._by_key.get(key, ()))

    def k_estimate(self, key: RateKey) -> Optional[float]:
        """Median k for this operating point, or None if nothing measured.

        Median so one aborted/emergency encode cannot move the estimate.
        """
        ks = sorted(o.k for o in self._by_key.get(key, ()))
        if not ks:
            return None
        n = len(ks)
        return ks[n // 2] if n % 2 else (ks[n // 2 - 1] + ks[n // 2]) / 2.0

    def _drift_fit(self, key: RateKey) -> Optional[Tuple[float, float]]:
        """Least-squares k = A + B*log2(v_bps), or None if not worth fitting.

        k creeps up with bitrate (~+2.3% per doubling measured), so a flat
        median extrapolated upward is biased high and costs retries. The slope
        is only recoverable from a wide enough spread - inside one target's
        narrow retry band the fit recovers noise, hence MIN_FIT_SPREAD.
        """
        obs = self._by_key.get(key, ())
        if len(obs) < MIN_FIT_OBS:
            return None
        rates = [float(o.v_bps) for o in obs if o.v_bps > 0]
        if len(rates) < MIN_FIT_OBS:
            return None
        lo, hi = min(rates), max(rates)
        if lo <= 0 or (hi / lo) < MIN_FIT_SPREAD:
            return None
        xs = [math.log2(float(o.v_bps)) for o in obs if o.v_bps > 0]
        ys = [float(o.k) for o in obs if o.v_bps > 0]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs)
        if den <= 0:
            return None
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        return (my - b * mx, b)

    def _v_bps_for_k(self, target_bytes: int, duration_s: float,
                     audio_bps: int, overhead: float, k: float) -> Optional[float]:
        try:
            dur = float(duration_s)
            if dur <= 0 or int(target_bytes) <= 0 or not k or k <= 0:
                return None
            ov = max(1.0, float(overhead or 1.0))
            vbytes = (float(target_bytes) / ov) - max(0.0, float(audio_bps or 0) * dur / 8.0)
            if vbytes <= 0:
                return None
            v = (vbytes * 8.0) / (dur * float(k))
        except Exception:
            return None
        if not (v > 0) or v != v:  # guards inf and NaN
            return None
        return v

    def suggest_v_bps(self, key: RateKey, target_bytes: int, duration_s: float,
                      audio_bps: int, overhead: float,
                      min_v_bps: int = 140_000) -> Optional[int]:
        """Video bitrate predicted to land at `target_bytes`, or None.

        `target_bytes` is the aim point itself; no safety margin is applied.
        """
        k = self.k_estimate(key)
        if k is None:
            return None
        v = self._v_bps_for_k(target_bytes, duration_s, audio_bps, overhead, k)
        if v is None:
            return None

        # Refine against the drift model. k depends on the bitrate being solved
        # for, so iterate; the dependence is weak and settles in a few rounds.
        fit = self._drift_fit(key)
        if fit is not None:
            A, B = fit
            for _ in range(6):
                k_v = A + B * math.log2(max(v, 1.0))
                if not (K_MIN <= k_v <= K_MAX):
                    break  # fit wandered out of the physical range; keep median
                nxt = self._v_bps_for_k(target_bytes, duration_s, audio_bps, overhead, k_v)
                if nxt is None:
                    break
                if abs(nxt - v) / max(v, 1.0) < 1e-4:
                    v = nxt
                    break
                v = nxt

        return int(max(int(min_v_bps), int(v)))
