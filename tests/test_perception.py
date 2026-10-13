import numpy as np

from guetteur import synth
from guetteur.common import CALIB_FRAMES, ZONES
from guetteur.perception import Perception


def _p(name, seed=0):
    d = synth.render(synth.scenarios()[name], seed)
    P = Perception(ZONES)
    P.calibrate(d[:CALIB_FRAMES])
    return P, d


def test_empty_scene_has_no_changes_and_nominal_noise():
    P, d = _p("empty")
    for t in range(CALIB_FRAMES + 5, len(d), 7):
        for z in ("shelf", "doorway"):
            o = P.observe(d[t], t, z)
            assert not o.changes and not o.occupants
            assert 0.8 < o.noise_ratio < 1.3 and abs(o.shift) < 0.003 and o.valid_frac > 0.97


def test_removed_item_is_a_confident_static_blob_with_the_right_depth_change():
    P, d = _p("shelf_item_removed")
    t = 95 + 12
    for tt in range(95, t):
        o = P.observe(d[tt], tt, "shelf")
    b = o.changes[0]
    assert abs(b.dz_mm - 300) < 15 and b.snr > 10 and not b.occupant and o.persist >= 10
    assert 50_000 < b.area_mm2 < 150_000


def test_person_is_flagged_as_occupant_not_as_change():
    P, d = _p("person_walkthrough")
    seen = [P.observe(d[t], t, "doorway") for t in range(CALIB_FRAMES + 40, CALIB_FRAMES + 60)]
    mid = [o for o in seen if o.occupants]
    assert mid, "person never recognised"
    b = mid[0].occupants[0]
    assert b.dz_mm < -300 and 300 < b.width_mm < 1100 and b.height_mm > 900


def test_integrating_frames_raises_confidence_without_false_alarms():
    P, d = _p("subtle_item_removed")
    single = P.observe(d[130], 130, "shelf").changes[0]
    win = P.observe_window(d[130:137], 137, "shelf", 2.0)
    assert win.changes and win.changes[0].snr > single.snr + 0.8
    Pe, e = _p("empty")
    assert not Pe.observe_window(e[100:107], 107, "shelf", 2.0).changes


def test_sensor_loss_and_noise_burst_and_drift_are_visible_as_health_not_blobs():
    P, d = _p("lens_obstructed")
    assert P.observe(d[120], 120, "shelf").valid_frac < 0.2
    P, d = _p("noise_burst")
    assert P.observe(d[70 + 35 + 2], 107, "shelf").noise_ratio > 1.6
    P, d = _p("slow_drift")
    assert P.observe(d[160], 160, "doorway").shift > 0.02


def test_drift_compensation_rescales_the_baseline():
    P, d = _p("slow_drift")
    s = P.observe(d[150], 150, "doorway").shift
    P.compensate_drift("doorway", s)
    assert abs(P.observe(d[150], 150, "doorway").shift) < 0.004


def test_evidence_summary_is_small_json_without_pixels():
    import json
    P, d = _p("shelf_item_removed")
    o = P.observe(d[110], 110, "shelf")
    s = json.dumps(o.summary())
    assert len(s) < 2000
    assert len(o.grid) == 6 and len(o.grid[0]) == 8
