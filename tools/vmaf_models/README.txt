VMAF model drop-in folder
=========================

BitCrusher measures quality with libvmaf. By default it uses "auto":
prefer VMAF v1 when your ffmpeg/libvmaf build provides it, otherwise fall
back to the classic vmaf_v0.6.1 model (so your calibrated numbers don't
shift until v1 is actually available).

VMAF v1 (Netflix, June 2026) is NEG-by-default and adds banding + chroma
awareness — it pairs well with artifact-aware preprocessing. There are two
OFFLINE ways to enable it (BitCrusher never downloads anything itself):

1. Update ffmpeg/libvmaf to a build that embeds the v1 model. BitCrusher
   probes for it automatically and switches over (you'll see a one-time
   "VMAF model: VMAF v1 (...)" line in the log).

2. Drop the VMAF v1 model .json file into THIS folder. Any *.json here with
   "v1" in its filename is picked up and loaded via libvmaf's model=path=.
   Get the model file from Netflix's VMAF repo (github.com/Netflix/vmaf,
   the resource/model directory) on a machine that has internet, then copy
   it here.

You can also force a model explicitly:
  - Environment variable:  BC_VMAF_MODEL=v1   (or neg | 4k | default | a raw
    "version=..." / "path=..." value)
  - CLI flag:              --vmaf-model v1
  - settings.json:         "vmaf_model": "v1"

IMPORTANT — recalibration: v1 and NEG score LOWER/stricter than v0.6.1 for
the same clip. When you switch, your reference VMAF numbers, the Min-VMAF
target, and the size-targeting accept-window tolerances were all tuned to
the v0.6.1 scale, so re-check them or the packing loop may over-spend
chasing a number the new metric won't reach.
