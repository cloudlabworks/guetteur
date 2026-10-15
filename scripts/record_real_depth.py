#!/usr/bin/env python3
"""Opt-in, bounded native-depth capture. Never opens an RGB stream.

Run in the separately installed DepthAI 3.11 environment. Output stays outside
this repository. Native geometry is retained; replay needs calibrated adaptation.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

SCENES = ('empty', 'walkthrough', 'item-removal')


def validate_frame(frame):
    if not isinstance(frame, np.ndarray) or frame.ndim != 2 or frame.dtype != np.uint16:
        raise ValueError('Expected a 2D uint16 depth frame; colour and silent conversion are rejected')
    if min(frame.shape) < 1 or frame.size > 1920 * 1200:
        raise ValueError('Unsupported frame dimensions')
    return frame


def capture_slot(elapsed, fps):
    """Use absolute time bins so sensor/host jitter cannot accumulate cadence drift."""
    return int(elapsed * fps)


def check_destination(path: Path):
    path = path.expanduser().resolve()
    repo = Path(__file__).resolve().parents[1]
    if path == repo or repo in path.parents:
        raise ValueError('Real camera data must be written outside the source repository')
    if path.exists():
        raise FileExistsError('Capture destination already exists; never overwrite evidence')
    return path


class Capture:
    def __init__(self, folder, scene, seconds, fps):
        if scene not in SCENES or not 15 <= seconds <= 300 or not 1 <= fps <= 10:
            raise ValueError('Scene, duration or frame rate outside supported bounds')
        self.folder = check_destination(Path(folder))
        self.folder.mkdir(parents=True, mode=0o700)
        self.scene, self.seconds, self.fps = scene, seconds, fps
        self.limit = int(seconds * fps) + 1
        self.data = None
        self.timestamps = []
        self.valid_fractions = []
        self.last_t = -1.0
        self.started = dt.datetime.now(dt.timezone.utc).isoformat()

    def add(self, frame, elapsed):
        validate_frame(frame)
        if not np.isfinite(elapsed) or elapsed < 0 or elapsed <= self.last_t:
            raise ValueError('Capture timestamps must be finite, nonnegative and strictly increasing')
        if len(self.timestamps) >= self.limit:
            raise ValueError('Frame budget exhausted')
        if self.data is None:
            self.data = np.lib.format.open_memmap(self.folder/'depth.npy', mode='w+', dtype='<u2', shape=(self.limit, *frame.shape))
        if frame.shape != self.data.shape[1:]:
            raise ValueError('Camera geometry changed during capture')
        self.data[len(self.timestamps)] = frame
        self.timestamps.append(float(elapsed))
        self.valid_fractions.append(float(np.count_nonzero(frame) / frame.size))
        self.last_t = elapsed

    def finish(self, complete, calibration=None, error=None, shutdown_clean=None):
        if self.data is not None:
            self.data.flush()
        count = len(self.timestamps)
        # Hash only valid native frames, never the unused allocation tail.
        digest = hashlib.sha256()
        if self.data is not None:
            for frame in self.data[:count]:
                digest.update(np.ascontiguousarray(frame, dtype='<u2').tobytes())
        meta = dict(schema='guetteur.real-depth.v1', synthetic=False,
                    label='RECORDED REAL DEPTH — native geometry, not yet calibrated for evaluation',
                    scene=self.scene, started_utc=self.started, units='millimetres', invalid_value=0,
                    complete=bool(complete and count >= self.seconds*self.fps*.9),
                    requested_duration_s=self.seconds, requested_fps=self.fps,
                    valid_frames=count, allocated_frames=self.limit,
                    shape=list(self.data.shape[1:]) if self.data is not None else None,
                    elapsed_seconds=self.timestamps, valid_pixel_fraction=self.valid_fractions,
                    depth_sha256=digest.hexdigest() if count else None,
                    camera_intrinsics=calibration, baseline_seconds=12,
                    labels_status='operator event-time labels required',
                    evaluation_ready=False, error=error, shutdown_clean=shutdown_clean)
        (self.folder/'capture.json').write_text(json.dumps(meta, indent=2)+'\n')
        return meta


def require_clean_camera(device):
    """Reject inherited watchdog/crash reports before creating a recording.

    SDK close preserves the report locally using the existing no-upload policy.
    Do not attribute an inherited report to this capture or silently clear it.
    """
    try:
        if device.hasCrashDump():
            raise RuntimeError('Pre-existing camera crash report; no recording started. Check the previous camera owner.')
    except BaseException:
        device.close()
        raise


def build_depth_pipeline(dai, device):
    """Build depth-only streams with explicit right/left shutdown controls."""
    pipeline = dai.Pipeline(device)
    try:
        left = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B, sensorFps=15)
        right = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C, sensorFps=15)
        stereo = pipeline.create(dai.node.StereoDepth)
        left.requestOutput((640, 400)).link(stereo.left)
        right.requestOutput((640, 400)).link(stereo.right)
        controls = (right.inputControl.createInputQueue(), left.inputControl.createInputQueue())
        queue = stereo.depth.createOutputQueue(maxSize=2, blocking=False)
        return pipeline, queue, controls
    except BaseException:
        device.close()
        raise


def shutdown_depth_pipeline(dai, pipeline, device, queue, controls):
    """Quiesce CAM_C then CAM_B before tearing down the host output path.

    DepthAI 3.11.0 / RVC2 can report a firmware crash on immediate teardown.
    Keep consuming in-flight depth while each stop takes effect. This sequence
    passed a physical-camera check but does not cure inherited watchdog reports.
    Always check startup state and never suppress SDK logs or crash checks.
    Related upstream camera-order defect: luxonis/depthai-core#2034.
    """
    try:
        if pipeline.isRunning() and not device.isClosed():
            for control in controls:
                stop = dai.CameraControl()
                stop.setStopStreaming()
                control.send(stop)
                drain_until = time.monotonic() + 0.5
                while time.monotonic() < drain_until:
                    queue.tryGet()  # discard; never write shutdown frames
                    time.sleep(0.005)
    finally:
        try:
            pipeline.stop()
        finally:
            try:
                pipeline.wait()
            finally:
                device.close()
    if device.hasCrashed() or not device.isClosed() or pipeline.isRunning():
        raise RuntimeError('DepthAI teardown failed: device crash or unclosed pipeline')


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--scene', choices=SCENES, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--device-id', required=True)
    ap.add_argument('--seconds', type=int, default=60)
    ap.add_argument('--fps', type=int, default=5)
    ap.add_argument('--confirm-depth-recording', action='store_true')
    args=ap.parse_args(argv)
    if not args.confirm_depth_recording:
        ap.error('Recording is opt-in: arrange the scene, then pass --confirm-depth-recording')
    check_destination(args.out)
    if not 15 <= args.seconds <= 300 or not 1 <= args.fps <= 10:
        ap.error('Use 15–300 seconds and 1–10 fps')
    for k,v in {'DEPTHAI_TELEMETRY':'0','DEPTHAI_DISABLE_CRASHDUMP_COLLECTION':'1','DEPTHAI_AUTOCALIBRATION':'OFF'}.items():
        os.environ[k]=v
    import depthai as dai
    if not str(dai.__version__).startswith('3.11.'):
        ap.error('Prepared against DepthAI 3.11; verify compatibility before using another version')
    info=next((d for d in dai.Device.getAllAvailableDevices() if d.getDeviceId()==args.device_id),None)
    if info is None:
        print('Camera unavailable or owned by another process. No capture started.',file=sys.stderr)
        return 2
    cap=None;calibration=None;shutdown_clean=None
    try:
        device = dai.Device(info)
        device.setMaxReconnectionAttempts(0)
        require_clean_camera(device)
        pipeline, queue, controls = build_depth_pipeline(dai, device)
        try:
            pipeline.start()
            cap=Capture(args.out,args.scene,args.seconds,args.fps)
            started=time.monotonic();last_frame=started;last_slot=-1
            print('Recording depth only. Keep the scene still for the first 12 seconds.',flush=True)
            while pipeline.isRunning() and time.monotonic()-started < args.seconds:
                now=time.monotonic();message=queue.tryGet()
                if message is not None:
                    last_frame=now
                    slot=capture_slot(now-started,args.fps)
                    if slot > last_slot:
                        frame=validate_frame(message.getFrame())
                        if calibration is None:
                            h,w=frame.shape
                            calibration=device.readCalibration().getCameraIntrinsics(dai.CameraBoardSocket.CAM_C,w,h)
                        cap.add(frame,now-started);last_slot=slot
                if now-last_frame > 10:
                    raise TimeoutError('No depth frames for ten seconds')
                time.sleep(.005)
            complete=time.monotonic()-started >= args.seconds
        finally:
            shutdown_clean=False
            shutdown_depth_pipeline(dai, pipeline, device, queue, controls)
            shutdown_clean=True
        # Finalize success only after teardown has actually completed.
        meta=cap.finish(complete,calibration,shutdown_clean=shutdown_clean)
        print(json.dumps({k:meta[k] for k in ['complete','valid_frames','shape','evaluation_ready','shutdown_clean']}))
        return 0 if meta['complete'] else 2
    except (Exception,KeyboardInterrupt) as e:
        if cap is not None:
            cap.finish(False,calibration,type(e).__name__,shutdown_clean=shutdown_clean)
            print('Capture incomplete: '+type(e).__name__+'. Any partial data is retained and labeled incomplete.',file=sys.stderr)
        else:
            print('No capture started: '+type(e).__name__+': '+str(e),file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
