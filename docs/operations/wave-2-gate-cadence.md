# Wave-2 Gate and Cadence (Post MQU-4 Closeout)

This execution note operationalizes the CEO decision posted on [MQU-4](/MQU/issues/MQU-4#comment-b5e911cc-087d-4ae6-aa4f-a4cd519a6304).

## Baseline

- wave-1 closeout reference: [MQU-4 closeout](/MQU/issues/MQU-4#comment-289a7bdd-bc99-438d-ad75-4233b1ae35cf)
- latest wave-1 execution snapshot: [MQU-4 sent=12 sync](/MQU/issues/MQU-4#comment-ae65559a-04b4-4223-8dcb-5259cb7eec1e)
- latest source ledger: [MQU-11 incremental ledger](/MQU/issues/MQU-11#comment-624f8607-20bb-4de6-83e5-e78d11b5bd09)
- baseline at closeout:
  - wave-1 total: 12
  - sent: 12
  - replies: 0
  - positive replies: 0
  - intro scheduled: 0
  - technical scheduled: 0

## Operating Rules

1. Observation window
   - Start: `2026-04-17` (KST), immediately after [MQU-4 closeout](/MQU/issues/MQU-4#comment-b5e911cc-087d-4ae6-aa4f-a4cd519a6304)
   - End (5 business days): `2026-04-24 18:00 KST` (`2026-04-24 09:00 UTC`)
2. Early-start exception
   - If qualified inbound replies reach `>= 2` before window end, wave-2 may start early.
3. Follow-up cadence cap
   - During this window, each unreplied contact may receive at most `1` follow-up touch.
   - No second follow-up inside the same window.
4. Channel priority
   - Priority 1: inbound OSS interest signals.
   - Priority 2: continuation in existing reply threads.
5. Disallowed actions
   - No cold outbound messages asking external people to contribute.
   - Follow `docs/operations/oss-messaging-guardrails.md`.

## Qualified Inbound Reply Definition

Treat a reply as qualified when at least one is true:

- explicitly requests next-step discussion (call/interview/technical follow-up)
- explicitly shares availability/contact handoff for next step
- clearly confirms role interest beyond acknowledgment

Simple acknowledgments without next-step intent do not count.

## Cadence Protocol During Observation Window

1. Run one checkpoint per business day (KST) and update cumulative metrics.
2. For each unreplied contact, enforce the one-touch follow-up cap.
3. Update both source lane and parent lane evidence when any delta appears:
   - source execution lane: [MQU-10](/MQU/issues/MQU-10)
   - closeout anchor: [MQU-4](/MQU/issues/MQU-4)
4. Re-evaluate `qualified inbound >= 2` after each checkpoint.

## Go / Hold Decision Point

- Current recommendation (as of `2026-04-17`): `HOLD`
  - Reason: qualified inbound replies are still `0`.
- Primary decision trigger: qualified inbound replies become `>= 2` before `2026-04-24 18:00 KST` -> `GO` (early start).
- Scheduled decision trigger: `2026-04-24 18:00 KST`
  - If threshold is met by then -> `GO`.
  - If threshold is not met -> `HOLD` and keep inbound-only monitoring until new qualified inbound signals appear.

## Next Trigger to Execute

- Event trigger (preferred): first checkpoint where qualified inbound count reaches `2`.
- Time trigger (fallback): `2026-04-24 18:00 KST` decision checkpoint.
