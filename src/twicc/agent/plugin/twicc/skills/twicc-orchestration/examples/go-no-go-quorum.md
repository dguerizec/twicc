# Risky go/no-go decision

Patterns: `quorum` (with cross-provider diversity).
Shape: leader → N independent advisors → majority.

## Walkthrough
1. Frame one crisp question: e.g. "Is this migration safe to run on production now?
   Answer GO or NO-GO with the single biggest risk."
2. Spawn an odd N of independent advisors with the identical brief and the same context
   (the migration, the system state). Keep them isolated — no sibling chat. Vary the
   voices on purpose: some Claude, some Codex, maybe different presets.
3. Barrier; collect each verdict + its cited risk.
4. Take the majority. Report the split — a unanimous GO is not a 3-2 GO; surface that
   to the user.

## Notes
- Odd N avoids ties; independence is everything — never let advisors see each other.
- The value is the diversity: cross-provider voices catch model-specific blind spots.
- No consensus is itself a signal — escalate to the user rather than forcing a call.
