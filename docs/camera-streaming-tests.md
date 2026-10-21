# OV3660 camera clock comparison - 2026-10-16

Hardware: original ESP32-D0WD-V3 camera carrier, OV3660, about 4 MB PSRAM.
Transport: authenticated HTTP MJPEG through the shared laptop preview, Pixel
2.4 GHz hotspot. All runs: 320x240 JPEG quality 12, two driver framebuffers,
latest-frame cache, Wi-Fi sleep disabled, 500 ms socket enqueue deadline.
The deadline does not bound TCP delivery time.

| Clock MHz | Capture limit | Receive window | Received FPS | Median frame age ms | p95 age ms | Longest gap s |
| --- | --- | --- | --- | --- | --- | --- |
| 20 (first) | 10 | 30 s | 9.63 | 176.18 | 317.84 | 0.34 |
| 8 | 10 | 30 s | 10.03 | 185.30 | 261.25 | 0.25 |
| 20 (repeat) | 10 | 30 s | 0.17 | 1606.25 | 1970.46 | 10.70 |
| 8 (longer run) | 20 | 60 s | 11.10 | 129.64 | 177.92 | 0.30 |

Age is estimated capture-to-laptop receipt, excluding browser rendering.
Clock uncertainty (half best measured RTT) was approximately 47 ms, 15.5 ms,
187.5 ms, and 15.5 ms respectively. The repeated 20 MHz run contained only
five frames; its latency statistics are correspondingly weak. FPS windows
include time lost reconnecting. JPEG sizes averaged about 7.9 KB at 8 MHz.

The evidence favors 8 MHz for this board during this session. It does not prove
that clock-related RF interference caused every previous stall, nor guarantee
identical results with other boards, scenes, lighting, power, or networks.

Active runtime profile after testing: 8 MHz, quality 12, capture limit 20 FPS.
This is RAM-only: the currently flashed firmware boots at 20 MHz / quality 12 /
10 FPS. Reapply `/tune` after restarting, or flash changed defaults to persist.

Raw per-frame records are ignored local files:
`data/hardware-backups/camera-benchmark-8mhz.json` and
`data/hardware-backups/camera-benchmark-8mhz-20fps.json`.


## Resolution follow-up

A subsequent build reserves larger JPEG driver buffers at initialization and
adds runtime resolution selection. It boots at 8 MHz, quality 12, capture limit
20 FPS, 320x240. Measured 30-second receive windows at 8 MHz:

| Resolution | Quality | FPS | Median age ms | p95 age ms | Longest gap s |
| --- | --- | --- | --- | --- | --- |
| 320x240 (first) | 12 | 8.67 | 144.93 | 187.91 | 6.77 |
| 480x320 | 12 | 12.27 | 123.06 | 213.06 | 0.42 |
| 640x480 | 12 | 6.37 | 278.37 | 427.31 | 2.47 |
| 320x240 (repeat) | 12 | 11.17 | 132.97 | 169.04 | 0.20 |
| 640x480 | 20 | 8.90 | 143.08 | 256.60 | 4.62 |
| 480x320 | 20 | 12.83 | 122.81 | 148.04 | 0.17 |

Scene contents and camera position changed during these runs, so these are
observations rather than a strict controlled comparison. Clock-offset estimate
uncertainty per run was approximately 15.5-23.5 ms. The max-gap figures include
reconnections. Higher JPEG quality numbers reduce detail/compressed size.

For shelf dataset readiness inspection, selected 640x480 / quality 20 / 8 MHz
at runtime to retain more object detail. The lower-latency alternative was
480x320 / quality 20. All three demo objects were fully visible at 640x480,
whereas taller objects were cropped at the top in the earlier 480x320 view.
An additional scene-specific measurement is saved locally in
`data/hardware-backups/shelf-readiness-640x480.json`. Runtime resolution and
quality selection revert to the above startup defaults on camera reset.
