# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## 2026-11-03

No versioned release yet. This section records the repository as of 2026-11-03.

### Added

- Local prototype of Guetteur (Team Drift): design, tests, evaluation on synthetic scenes and an infrastructure draft (`DESIGN.md`, `docs/EVALUATION.md`, `eval/`, `INFRA/`) (8acbd2b).
- Technical report draft, PDF and diagrams (`docs/report/`), and a credential-free CloudShell deploy kit (`deploy/`) (a2b9f85).
- Infrastructure template: reserved-concurrency parameter, 7-day log retention on both log groups, and a `HealthUrl` output (a2b9f85).
- Opt-in native-depth recording and an operator run sheet (`scripts/record_real_depth.py`, `docs/REAL_DEPTH_CAPTURE.md`) (8c347ea).
- Depth capture teardown check, `scripts/check_depth_shutdown.py` (3a28344).
- Submission materials for the OpenCV AI Competition powered by AWS: submission story, evidence files and an updated report (6cc6f25); the narrated AWS demo with its recorded responses (a65bbb5); the submission receipt, captions and hosted demonstration (6b6dd1c).
- `CONTRIBUTING.md`: contributions are released under the repository's MIT licence, and commits carry a DCO sign-off (#1).

### Changed

- Review fixes: a single render endpoint, write routes that fail shut, phase 2 split out in the infrastructure notes, plainer wording, and a note on oak-events integration (a119bca).

### Fixed

- CloudShell deploy fails on a checksum mismatch, handles `REVIEW_IN_PROGRESS` and validates the concurrency quota (65ed788).
- The HTTP API access log format includes `$context.requestId`; without it the first real deploy ended in `CREATE_FAILED` (f542531).
- Depth recording no longer accumulates sampling drift or opens the camera twice (ea55597), and guards camera handoffs (3a28344).
