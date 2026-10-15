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
