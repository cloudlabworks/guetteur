## Inspiration
Guetteur is French for "lookout". We wanted a vision agent that could explain why it acted, and let somebody check the evidence. Keeping colour footage out of the workflow was another design constraint: a shelf or doorway should not need a cloud video archive just to report a change.

## What it does
Guetteur uses OpenCV 5 to analyse depth changes, motion and sensor health. Its rule-based planner can integrate more frames, pause an occupied zone, compensate for drift, or request human approval. Every claim carries a receipt checked against the pinned input by NumPy and OpenCV estimators.

The live AWS dashboard runs built-in synthetic depth scenes. Visitors can compare planners, simulate approval or timeout, inspect decision traces and deliberately alter a claim to test the verifier. This is a working cloud demo, not a live camera stream or an authenticated remote-approval system.

Separately, an opt-in OAK-D recorder has captured native depth-only baseline, walkthrough and item-removal takes. Archive integrity and clean shutdown are verified. Calibrated replay and real-world detection accuracy are not yet measured.

## How we built it
OpenCV 5 supplies inpainting, median filtering, morphology, connected components and Farneback optical flow. The planner sees compact JSON evidence and returns schema-checked actions. The executor enforces the policy; no response to an approval request means no outward alert.

The AWS deployment uses API Gateway, Lambda arm64 running OpenCV, and CloudWatch. AWS performs the perception loop and receipt verification, not just static hosting. The repository includes the SAM template, pinned dependencies, local runner, evaluation fixtures and technical report. DynamoDB, S3 and authenticated real-camera approval routes remain phase-2 work, not deployed features.

The physical recorder uses a separate depth-only DepthAI pipeline. It preserves native frame geometry, timestamps and hashes. The project draws on our oak-events camera work and the open prove-it-done verification pattern. AI coding assistance was used; the planner evaluated here is deterministic code, not an LLM.

## Challenges we ran into
The first AWS deployment failed because the HTTP API access-log format lacked the required request ID. Fixing the format and checking the live endpoint closed a gap that local template validation had not caught. The account quota could not support the requested concurrency reservation; the deployment records that limitation and retains API throttling.

A physical capture could reach its frame target and still crash the SDK during shutdown. A hardware A/B check isolated the camera stop order. Stopping CAM_C before CAM_B and draining the queues produced clean shutdowns and restored the camera's usual owner. Successful recording and successful recovery needed separate checks.

Our initial tamper test rejected 23 of 24 cases. Its missed case moved a whole-frame sensor fault to another region, where the fault was still true. We corrected that test to use the wrong time window and kept the original failure in the report.

## Accomplishments that we're proud of
On 33 synthetic runs, the rule planner succeeded on 30, with event precision 1.00 and recall 0.86. The raw threshold baseline succeeded on 12, with precision 0.25 and recall 0.43. The three misses are the same sub-noise scenario across three seeds, and remain reported as misses. These are development-set synthetic results, not a real-world accuracy claim.

The verifier proved 21 of 21 honest ledgers and rejected 24 of 24 tampered cases under the corrected test definition. Fresh AWS checks reproduced the subtle-change detection, naive-baseline miss, timeout with zero published alerts, sensor-fault detection and documented subthreshold miss. Doubling a claim was rejected by the deployed verifier.

Three physical takes each contain 300 integrity-checked native 640x400 depth frames, with clean shutdown. This establishes a capture foundation; it does not establish detector accuracy.

## What we learned
Asking for more evidence can be better than lowering a threshold. In the synthetic subtle-removal case, integrating seven frames lets the planner resolve a change that the naive baseline misses.

A hash proves which bytes were checked, not whether the claim is true. Re-measuring the claim matters. Even two estimators share the same input, so neither can prove that an honestly pinned recording depicts the intended event.

Hardware lifecycle belongs in the acceptance criteria. "Recorded 300 frames" was insufficient while shutdown could still crash the device. We now check capture integrity, clean teardown and recovery separately.

Recording, labelling and evaluation are different stages. An operator confirming "I removed the item" is a useful label, not a detector result. Native 640x400 data cannot silently inherit the synthetic 320x240 calibration.

Deployment evidence has to come from the deployed service. A template passing a linter did not prove that AWS would accept the log format, and a configured concurrency limit did not mean the account could reserve it. The report now distinguishes planned controls from controls actually available.

## What's next for Guetteur
Calibrate the real depth recordings, label exact event intervals and evaluate on held-out takes. Then complete authenticated edge-to-cloud approvals and their negative tests. The public synthetic demo and reproducible receipt checks remain the reference while that work proceeds. Multi-camera support comes after those measurements.
