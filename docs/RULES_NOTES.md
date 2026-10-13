# Rules notes: what the two official pages say, and where they disagree

Read in full on 2026-10-12 (Devpost pages needed a real browser: plain HTTP fetches get a Cloudflare challenge).
Sources, cited by page and section heading:

* **DP-Overview** = https://opencv26.devpost.com/ (sections: "ABOUT THE COMPETITION", "JOIN, BUILD, AND WIN", "BUILD PHASE — TWO MONTHS", "FINAL JUDGING AND WINNERS", "WHAT AWS GRANT PROPOSALS MUST INCLUDE", "FREE AWS CREDITS FOR NEW MEMBERS"; page header "Deadline")
* **DP-Rules** = https://opencv26.devpost.com/rules (sections: "PRIZES", "JUDGING", "OVERALL RUBRIC", "BEST USE OF COOL AWARD RUBRIC", "AGENTIC VISION AWARD RUBRIC", "OWNERSHIP, USE, AND PRIVACY", "ELIGIBILITY AND FAIR PLAY", "RESPONSIBLE AND ETHICAL USE", "CHANGES, DISQUALIFICATION, AND AWARD ADMINISTRATION")
* **DP-Resources** = https://opencv26.devpost.com/resources (links to OpenCV 5 docs, COOL overview, COOL on AWS Marketplace, AWS Free Tier, AWS Promotional Credit Terms)
* **OCV** = https://opencv.org/opencv-ai-competition-2026/ (sections: "Competition at a Glance", "Focus Paths", "How the Competition Works", "Grant Proposals", "Free AWS Credits for New Accounts", "Build Phase Details", "Final Judging Guidelines", "Final Submission Package", "Competition Terms & Conditions" > "Requirements for Participation")

Both pages say the "Devpost Official Rules" control if the overview conflicts (DP-Rules, "CHANGES, DISQUALIFICATION, AND AWARD ADMINISTRATION", last bullet; OCV, "Eligibility and Fair Play", last bullet).

## What we design against

| Requirement | Source | How Guetteur meets it |
| --- | --- | --- |
| OpenCV 5 for *substantive* image/video analysis; analysis central, not incidental | DP-Overview "ABOUT THE COMPETITION"; OCV "Requirements for Participation" | `perception.py`: inpaint, medianBlur, morphologyEx, connectedComponentsWithStats, Farneback, resize; `check_cv.py` |
| A meaningful component runs on AWS | same | Lambda arm64 runs the OpenCV 5 loop and receipt checks behind API Gateway (`INFRA/`, draft) |
| Judge-accessible working app: a web endpoint is preferred, a scheduled screen-share is acceptable | OCV "Requirements for Participation"; "Final Submission Package" | HTTPS endpoint (Lambda dashboard), plus `guetteur serve` and a screen-share fallback |
| Final package: technical report, judge-accessible repo, pinned deps + build/deploy/test instructions, architecture diagram, endpoint or screen-share, video <= 5 min (team, app working, architecture, principal results), evaluation evidence incl. failure cases/limitations | OCV "Final Submission Package" | `DESIGN.md` -> report, `README.md`, `uv.lock`, `INFRA/`, `docs/EVALUATION.md` |
| Agentic Vision Award: an agent uses OpenCV 5 tools in a multi-step perception-decision-action loop; image/video results must change a later plan, tool call, action, or request for human approval; a chatbot explaining a fixed result does not qualify | OCV "Focus Paths" > "Agentic Vision with OpenCV 5" | `agent.py` + traces; ablation vs a fixed-threshold baseline |
| Agentic extra evidence: agent workflow diagram (perception, decision/orchestration, action); a trace/demo showing OpenCV 5 output changes a later decision/tool call/action; evaluation of task success, failure handling, observability, human control | OCV "Final Submission Package" > "Additional Evidence for the Agentic Vision Award" | `DESIGN.md` section 4.2; `runs/*/trace.jsonl`; `docs/EVALUATION.md` |
| Agentic rubric: 30 OpenCV 5 + agent integration, 25 orchestration/autonomy, 20 task effectiveness/evaluation, 15 failure handling/observability/security/human control, 10 UX/docs/demo | DP-Rules "AGENTIC VISION AWARD RUBRIC"; OCV "Final Judging Guidelines" | `DESIGN.md` section 9 maps evidence to each line |
| Overall rubric: technical execution 30, innovation 20, real-world impact 20, UX 10, documentation 10, cloud delivery/reproducibility/responsible operation 10; COOL/agentic methods are credited only to the extent they improve the project | DP-Rules "OVERALL RUBRIC" | same |
| An entry can win at most one Overall award and may also win one or both special awards | DP-Rules "PRIZES" | we opt in to Agentic Vision only |
| Responsible use: judges may reject entries with unaddressed privacy/surveillance risk, or that misrepresent capabilities, results, benchmarks or the role of human review | DP-Rules "RESPONSIBLE AND ETHICAL USE" | depth-only privacy design, default-deny human gate, synthetic data labelled as synthetic, failure cases reported |
| Disclosed agents and automated programs integral to the project are allowed | DP-Rules "ELIGIBILITY AND FAIR PLAY" | the agent is the project; AI coding assistance is disclosed in the report (using an assistant to write the entry does not itself qualify as Agentic Vision: OCV "Focus Paths") |
| License grant to OpenCV and AWS over submitted materials (report, presentation, video) | DP-Rules "OWNERSHIP, USE, AND PRIVACY" | no third-party material in the video; MIT code; nothing confidential |
| Use of AWS credits: $100 on a Free Plan sign-up (+ up to $100 more via activities), subject to Free Tier terms and expiry | DP-Overview "FREE AWS CREDITS FOR NEW MEMBERS"; OCV same | everything is pay-per-use and capped; see DESIGN.md section 7 |

## Conflicts and open questions (need Alex or the organisers)

1. **Are we eligible to submit at all?** OCV "Competition at a Glance" says "Registration closes Aug 17, 2026" and OCV "Build Phase Details" says the build phase is for "all teams that submitted the required proposal"; OCV "Requirements for Participation" lists "Register on the official competition Devpost page" and "Submit the cloud compute grant proposal" as requirements. DP-Overview still shows a "Join hackathon" button, and DP-Overview "ABOUT THE COMPETITION" says teams that do not get a grant "remain eligible to continue to the two-month build phase". Whether Team Drift registered and submitted a proposal is not something these pages can tell us. **Top item for Alex.** (Nothing was registered or submitted by this work.)
2. **Deadline time.** DP-Overview page header ("Deadline: Oct 26, 2026 @ 11:45pm PDT") and the Devpost countdown say **Oct 26, 2026 @ 11:45pm PDT**; DP-Overview "ABOUT THE COMPETITION" / "BUILD PHASE" and OCV "Build Phase Details" / "Full Official Schedule" say **11:59 p.m. Pacific**. Plan to the earlier (11:45pm) and target submitting on **Oct 25**.
3. **Team size.** OCV "Competition at a Glance": "Teams of 1-5 members allowed"; OCV "Requirements for Participation": "no more than four". Team Drift is 1 (+ agents), so not binding; note it in case the team grows.
4. **Grant size/count and check-in window.** DP-Overview: 50 teams, $150, check-in Oct 7-14; OCV: up to 55, check-in Sep 21-Oct 2. Only matters if we hold a grant (unknown). If we do, the 30-minute Zoom check-in is a condition for the second 50% (DP-Overview "BUILD PHASE").
5. **Prize pool wording.** "up to $12,000 in cash" (DP-Rules "PRIZES") vs "$20,250 in prizes and AWS credits" (OCV "Rewards"): the difference is the grant credits. Not binding for us.
6. **Official Rules document.** Both pages defer to the "Devpost Official Rules" for governing-law, venue and eligibility terms; the /rules page we read is the overview. If a separate Official Rules PDF exists, it should be read before submission.

## What the pages do NOT say (so we do not claim it)

* No page defines a required web-endpoint uptime, a video format, or a maximum repo size.
* The Agentic Vision "qualifying requirements" are the "Focus Paths" text; if no entry satisfies them the award "may remain unawarded" (DP-Rules "PRIZES").
* COOL (Cloud-Optimized OpenCV Library, via AWS Marketplace) is an optional path. We do not use it (see DESIGN.md section 7).
