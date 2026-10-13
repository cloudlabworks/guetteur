# Trace example: OpenCV 5 output changes the next tool call

SYNTHETIC scene `subtle_item_removed`, seed 0. A low item (25 mm relief) is taken at frame 95. Steps where the planner acted (full trace: `runs/subtle_item_removed_s0_rule/trace.jsonl`).

### frame 95, zone `shelf`: `continue` {}

* OpenCV 5 observation: integrated frames = 1, blob contrast (mean |z|) = 3.71, depth change = 27.0 mm, persisted 1 frames, valid depth 1.003, noise ratio 1.12
* Why: ambiguous blob, not yet persistent enough to spend a recheck
* Outcome: {'status': 'ok'}

### frame 96, zone `shelf`: `continue` {}

* OpenCV 5 observation: integrated frames = 1, blob contrast (mean |z|) = 3.46, depth change = 25.0 mm, persisted 2 frames, valid depth 1.0, noise ratio 1.18
* Why: ambiguous blob, not yet persistent enough to spend a recheck
* Outcome: {'status': 'ok'}

### frame 97, zone `shelf`: `recheck_zone` {"n": 7, "k": 2.0}

* OpenCV 5 observation: integrated frames = 1, blob contrast (mean |z|) = 3.34, depth change = 24.0 mm, persisted 3 frames, valid depth 1.002, noise ratio 1.12
* Why: contrast 3.3 is between 2.0 and 4.5: integrate 7 frames and look again
* Outcome: {'status': 'ok', 'detail': 'collecting 7 frames'}

### frame 104, zone `shelf`: `request_approval` {"kind": "object_removed"}

* OpenCV 5 observation: integrated frames = 7, blob contrast (mean |z|) = 5.23, depth change = 24.0 mm, persisted 10 frames, valid depth 1.021, noise ratio 1.19
* Why: clear change (+24 mm) held for 10 frames
* Outcome: {'status': 'approved', 'detail': 'approved by scripted-operator: alert published', 'claim': 'c1', 'request': 'a1'}

### frame 108, zone `doorway`: `continue` {}

* OpenCV 5 observation: integrated frames = 1, blob contrast (mean |z|) = 2.57, depth change = 73.5 mm, persisted 1 frames, valid depth 1.0, noise ratio 0.99
* Why: ambiguous blob, not yet persistent enough to spend a recheck
* Outcome: {'status': 'ok'}

Reading it: at the first step the evidence (contrast about 3.5) sits between 'ignore' and 'confident', so the planner chose `recheck_zone` with `n` computed from that number.
The next perception call is an OpenCV 5 run on the *integrated* window; its contrast is higher, it persists, and only then does a human get asked. With the naive baseline the same scene produces no alert at all.
