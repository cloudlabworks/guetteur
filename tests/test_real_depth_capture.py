import importlib.util
from pathlib import Path
import numpy as np
import pytest
spec=importlib.util.spec_from_file_location('depth_capture',Path(__file__).parents[1]/'scripts/record_real_depth.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_opt_in_required(tmp_path):
    with pytest.raises(SystemExit):m.main(['--scene','empty','--out',str(tmp_path/'recording'),'--device-id','test'])
    assert not (tmp_path/'recording').exists()

@pytest.mark.parametrize('frame',[np.zeros((2,2,3),np.uint16),np.zeros((2,2),np.uint8),np.zeros((0,2),np.uint16)])
def test_reject_non_depth(frame):
    with pytest.raises(ValueError):m.validate_frame(frame)

def test_no_overwrite(tmp_path):
    with pytest.raises(FileExistsError):m.Capture(tmp_path,'empty',15,1)

def test_reject_repo_storage():
    with pytest.raises(ValueError):m.check_destination(Path(__file__).parents[1]/'private-depth')

def test_real_capture_preserves_units_timestamps_and_incomplete_status(tmp_path):
    c=m.Capture(tmp_path/'clip','item-removal',15,1);a=np.array([[0,1400],[2000,4000]],dtype=np.uint16)
    c.add(a,.2);c.add(a+1,1.3);r=c.finish(False,error='Interrupted')
    assert r['synthetic'] is False and r['complete'] is False and r['evaluation_ready'] is False
    assert r['valid_frames']==2 and r['elapsed_seconds']==[.2,1.3]
    assert r['units']=='millimetres' and r['valid_pixel_fraction'][0]==.75
    assert np.array_equal(np.load(c.folder/'depth.npy',mmap_mode='r')[0],a)

def test_timestamp_and_shape_guard(tmp_path):
    c=m.Capture(tmp_path/'clip','empty',15,1);c.add(np.zeros((2,2),np.uint16),.1)
    with pytest.raises(ValueError):c.add(np.zeros((2,2),np.uint16),.1)
    with pytest.raises(ValueError):c.add(np.zeros((3,2),np.uint16),1.)

@pytest.mark.parametrize('seconds,fps',[(0,5),(301,5),(60,11)])
def test_bounded_capture(tmp_path,seconds,fps):
    with pytest.raises(ValueError):m.Capture(tmp_path/'clip','empty',seconds,fps)


def test_sampling_does_not_accumulate_sensor_jitter():
    # 15-Hz sensor arrivals straddle each 5-Hz boundary by a few ms.
    arrivals=[i/15 + .004*(i%3) for i in range(900)]
    slots={m.capture_slot(t,5) for t in arrivals}
    assert len(slots)==300

class FakePipeline:
    def __init__(self, events, fail_stop=False):
        self.events, self.running, self.fail_stop = events, True, fail_stop
    def isRunning(self):return self.running
    def stop(self):
        self.events.append('pipeline-stop'); self.running=False
        if self.fail_stop:raise RuntimeError('stop failed')
    def wait(self):self.events.append('pipeline-wait')

class FakeDevice:
    def __init__(self, events, crashed=False):
        self.events, self.closed, self.crashed = events, False, crashed
    def close(self):self.events.append('device-close');self.closed=True
    def isClosed(self):return self.closed
    def hasCrashed(self):return self.crashed

class FakeControl:
    def __init__(self, events, name, fail=False):self.events,self.name,self.fail=events,name,fail
    def send(self, msg):
        assert msg.stopping
        self.events.append(self.name)
        if self.fail:raise RuntimeError('control failed')

class FakeStop:
    def setStopStreaming(self):self.stopping=True

class FakeDai:
    CameraControl=FakeStop

class FakeQueue:
    def __init__(self, events):self.events=events
    def tryGet(self):self.events.append('drain')

def shutdown_fakes(monkeypatch, *, crashed=False, fail_control=False, fail_stop=False):
    events=[]
    clock=iter(i*.25 for i in range(100))
    monkeypatch.setattr(m.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(m.time,'sleep',lambda _:None)
    pipeline=FakePipeline(events,fail_stop)
    device=FakeDevice(events,crashed)
    controls=(FakeControl(events,'CAM_C',fail_control),FakeControl(events,'CAM_B'))
    return events,(FakeDai,pipeline,device,FakeQueue(events),controls)

def test_shutdown_stops_right_then_left_before_output_teardown(monkeypatch):
    events,args=shutdown_fakes(monkeypatch)
    m.shutdown_depth_pipeline(*args)
    assert events==['CAM_C','drain','CAM_B','drain','pipeline-stop','pipeline-wait','device-close']

@pytest.mark.parametrize('failure',['control','stop'])
def test_shutdown_still_closes_device_when_cleanup_step_fails(monkeypatch,failure):
    events,args=shutdown_fakes(monkeypatch,fail_control=failure=='control',fail_stop=failure=='stop')
    with pytest.raises(RuntimeError):m.shutdown_depth_pipeline(*args)
    assert events[-3:]==['pipeline-stop','pipeline-wait','device-close']

def test_sdk_crash_flag_is_not_silent_success(monkeypatch):
    events,args=shutdown_fakes(monkeypatch,crashed=True)
    with pytest.raises(RuntimeError,match='teardown failed'):m.shutdown_depth_pipeline(*args)
    assert events[-1]=='device-close'

def test_failed_shutdown_preserves_depth_and_marks_incomplete(tmp_path):
    c=m.Capture(tmp_path/'clip','empty',15,1)
    for i in range(15):c.add(np.ones((2,2),np.uint16),i+.1)
    meta=c.finish(False,error='RuntimeError',shutdown_clean=False)
    assert meta['valid_frames']==15 and meta['complete'] is False
    assert meta['shutdown_clean'] is False and meta['depth_sha256']
    assert np.load(c.folder/'depth.npy').shape==(16,2,2)

@pytest.mark.parametrize('has_dump',[False,True])
def test_startup_rejects_inherited_report_before_recording(has_dump):
    events=[]
    device=FakeDevice(events)
    device.hasCrashDump=lambda:has_dump
    if has_dump:
        with pytest.raises(RuntimeError,match='Pre-existing camera crash report'):
            m.require_clean_camera(device)
        assert events==['device-close']
    else:
        m.require_clean_camera(device)
        assert events==[]
