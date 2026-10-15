# Real-depth capture: operator run sheet

Status: a native empty-scene baseline has been captured; calibrated replay and real-scene evaluation remain open. Existing evaluation scores remain synthetic.

## What to record

Use one fixed camera position and the same clearly bounded tabletop zone for all three 60-second takes. Keep the scene still for the first 12 seconds of every take; that is the baseline, not an event. Note event start/end times on a clock relative to capture start.

1. **empty**: leave the scene unchanged for the full take. This measures noise and false alarms.
2. **walkthrough**: after the baseline, walk across the view without moving the target object, then leave. This tests presence versus lasting scene change.
3. **item-removal**: after the baseline, enter, remove one clearly visible object, and leave the changed scene visible. Note entry, removal and exit times separately.

No RGB stream is opened. Depth silhouettes still describe the scene: keep bystanders out and retain recordings locally. Do not publish the raw archive by default.

## Before capture

- Arrange the scene and confirm everyone present knows recording is about to start.
- Coordinate a temporary pause of any camera owner (on the lab Mac, Kestrel); do not kill an unidentified process or fight for the device. Record its restart method before pausing, and restore it after capture, including on failure.
- Use the separate DepthAI **3.11** environment, with numpy installed. The project runtime intentionally does not acquire a camera or install DepthAI on startup.
- Obtain the device ID from your device inventory. It is passed at runtime, never embedded in source.

## Run one take

From this repository, the following prompts for your local Python interpreter and device ID. The interpreter must have DepthAI 3.11 and numpy. Change `--scene` for the later takes; each destination must be new.

```sh
printf 'DepthAI Python interpreter path: '
read -r capture_python
printf 'OAK device ID: '
read -r capture_device
capture_root="$HOME/guetteur-captures/$(date +%Y%m%d-%H%M%S)"
"$capture_python" scripts/record_real_depth.py \
  --device-id "$capture_device" \
  --scene empty \
  --seconds 60 --fps 5 \
  --out "$capture_root/empty" \
  --confirm-depth-recording
```

The recorder refuses an existing destination or one inside this source repository. It retains partial data on failure and labels it incomplete. A successful process exit requires capture completion and checked camera teardown, **not** that perception or detection passed. New metadata includes `shutdown_clean`; earlier archives without that field retain their original evidence and must not be relabeled retroactively.

## Archive and evaluation boundary

`depth.npy` stores uint16 millimetres, zero invalid, at the camera's native output geometry. It is preallocated: only `capture.json.valid_frames` entries are data. The metadata records frame times, valid-pixel fractions, calibration intrinsics and a SHA-256 over valid native frames. Keep both files together.

The current synthetic pipeline assumes a different raster and nominal camera geometry. **Do not feed this archive into it by silently resizing.** First validate rectification/alignment and calibration for the recorded output, implement a calibrated replay adapter with zone mapping and invalid-depth handling, then independently annotate the event intervals. Preserve the source archive and version any derived data and labels. A supplied calibration matrix alone does not prove its geometry matches the rectified depth stream.

For each real run report: capture hash, scene and labels, baseline interval, valid-depth coverage, event outcomes, false positives, misses and timing. Do not combine synthetic and real results into one score. Retain at least one failure case and show whether approval timeout suppresses the external action.

## Final submission recordings

These depth takes are evidence inputs, not the final video. Separately record a dashboard demonstration of at most five minutes: architecture, a labeled real replay, synthetic baseline comparison, approval and timeout, receipt verification, a documented miss, and the limits. Do not label a local replay as a live AWS camera stream.

## Clean camera handoff and DepthAI 3.11.0 teardown

The observed "shutdown crash" can be an **inherited crash report**, not a crash
caused by the current take. In the lab reproduction, the device reported a dump
immediately after connection, before the depth pipeline started. Kestrel previously
used Python's default SIGTERM behavior; stopping its supervisor terminated the
process without closing the PoE device. Kestrel now handles SIGTERM/SIGINT by
leaving its loop and closing the pipeline normally.

The recorder checks for an inherited crash dump before creating a capture. If one
exists, it rejects the attempt without recording; SDK close preserves the report
locally under the no-upload policy. Fix the previous owner's handoff, inspect the
report, and obtain readiness again. Do not silently clear or disable crash checks.

For orderly stereo teardown, the recorder sends stop-streaming to **CAM_C
(right), then CAM_B (left)**, draining in-flight depth messages for 0.5 seconds
after each command. Pipeline stop/wait and device close run even if a stop command
fails. Success is finalized only after checking the device crash flag and closed
pipeline/device state. Failure retains frames with `complete=false` and
`shutdown_clean=false`.

The [upstream synchronized camera-stop issue](https://github.com/luxonis/depthai-core/pull/2034)
was still open when inspected on 2026-10-14. Camera ordering was the initial
hypothesis, but changing that alone did not fix the inherited report. This is an
application lifecycle/handoff fix, not an SDK upgrade or a firmware patch.

For a non-recording check, pause the known camera owner as above, then run:

```sh
printf 'DepthAI Python interpreter path: '
read -r capture_python
printf 'OAK device ID: '
read -r capture_device
"$capture_python" scripts/check_depth_shutdown.py \
  --device-id "$capture_device" --seconds 60 --confirm-camera-test
```

The check uses the recorder's production pipeline and teardown functions. It
checks native uint16 frames in memory, writes no frame files, and reports startup
crash state, frame count, shape, device crash/closed flags, and pipeline state.
Inspect the SDK log as well as the exit code. Restore the previous camera owner
afterward. Test **fresh handoffs**, not just consecutive runs while monitoring is
paused: a clean second run can conceal a bad first handoff.
