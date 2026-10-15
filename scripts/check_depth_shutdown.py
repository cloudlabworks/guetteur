#!/usr/bin/env python3
"""Opt-in physical depth lifecycle check. No frame files, RGB, or recording."""
import argparse
import json
import os
import time

from record_real_depth import build_depth_pipeline, shutdown_depth_pipeline, validate_frame, require_clean_camera


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device-id', required=True)
    parser.add_argument('--seconds', type=int, default=5, choices=range(5, 61))
    parser.add_argument('--confirm-camera-test', action='store_true')
    args = parser.parse_args()
    if not args.confirm_camera_test:
        parser.error('Pause the known camera owner, then pass --confirm-camera-test')
    for key, value in {'DEPTHAI_TELEMETRY': '0', 'DEPTHAI_DISABLE_CRASHDUMP_COLLECTION': '1',
                       'DEPTHAI_AUTOCALIBRATION': 'OFF'}.items():
        os.environ[key] = value
    import depthai as dai
    if not str(dai.__version__).startswith('3.11.'):
        parser.error('Verify compatibility before testing another SDK version')
    info = None
    deadline = time.monotonic() + 100
    while info is None and time.monotonic() < deadline:
        info = next((d for d in dai.Device.getAllAvailableDevices()
                     if d.getDeviceId() == args.device_id), None)
        if info is None:
            time.sleep(2)
    if info is None:
        raise RuntimeError('Selected camera unavailable after 100 seconds')
    device = dai.Device(info)
    device.setMaxReconnectionAttempts(0)
    startup_dump = device.hasCrashDump()
    print(json.dumps({'phase': 'startup', 'preexisting_crash_dump': startup_dump}), flush=True)
    require_clean_camera(device)
    pipeline, queue, controls = build_depth_pipeline(dai, device)
    count, shapes = 0, set()
    try:
        pipeline.start()
        started = time.monotonic()
        while pipeline.isRunning() and time.monotonic() - started < args.seconds:
            message = queue.tryGet()
            if message is not None:
                frame = validate_frame(message.getFrame())
                shapes.add(frame.shape)
                count += 1
                del frame, message
            time.sleep(0.005)
    finally:
        shutdown_depth_pipeline(dai, pipeline, device, queue, controls)
    result = {'sdk': str(dai.__version__), 'seconds': args.seconds, 'depth_messages': count,
              'shapes': sorted(shapes), 'shutdown_clean': True,
              'device_crashed': device.hasCrashed(), 'device_closed': device.isClosed(),
              'pipeline_running': pipeline.isRunning(), 'frames_saved': 0, 'preexisting_crash_dump': startup_dump}
    print(json.dumps(result), flush=True)
    return 0 if count >= args.seconds * 10 and len(shapes) == 1 else 2


if __name__ == '__main__':
    raise SystemExit(main())
