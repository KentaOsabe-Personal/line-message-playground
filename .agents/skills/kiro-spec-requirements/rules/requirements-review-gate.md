# Requirements Review Gate

Before writing `requirements.md`, review the draft requirements and repair local issues until the draft passes or a true scope ambiguity is discovered.

## Boundary Continuity

Use boundary terminology consistently across phases without turning requirements into design:

- **Discovery** identifies `Boundary Candidates`
- **Requirements** make inclusion, exclusion, and adjacent expectations explicit when scope could be misread
- **Design** turns those into `Boundary Commitments`
- **Tasks** use `_Boundary:_` to constrain executable work

Requirements should clarify the feature boundary in user- or operator-observable terms, not in architecture ownership or implementation detail.

## Spec Size Review

- Read and apply `.kiro/steering/spec-sizing.md` when it exists.
- Before writing the draft, produce a Spec Size Assessment from the distinct user journeys, responsibility seams, external workflows, failure/compensation paths, migrations, integration work, and expected validation work.
- Estimate the resulting executable 1-3 hour tasks; do not omit tests, migrations, setup, or integration to make the estimate smaller.
- Treat 40+ projected executable tasks as `SPLIT_REQUIRED` by default.
- Treat 30-39 projected tasks as a review-attention band: record internal workstreams and dependency order, but allow `PASS (single-spec)` when the requirements remain one coherent outcome and the bounded review converges.
- Do not split below 40 merely because multiple responsibilities can be delivered, changed, or reviewed independently. Require the compound boundary-risk or bounded review-instability evidence defined in `spec-sizing.md`.
- If the brief already contains an assessment, validate it against the expanded requirements instead of copying it uncritically.
- A size failure is not a local wording problem. Stop without writing `requirements.md`, report the evidence, and return to `$kiro-discovery` for roadmap decomposition.

## Scope and Coverage Review

- The draft must cover the feature's core user journeys, major scope boundaries, primary error cases, and meaningful edge conditions that are visible to the user or operator.
- If the feature touches adjacent systems, specs, or workflows, the draft must make clear what this feature expects from them and what it does not own when that distinction affects user-visible behavior or operator expectations.
- Business/domain rules, compliance constraints, security/privacy expectations, and operational constraints that materially shape user-visible behavior must be reflected explicitly when they are in scope.
- If coverage is missing because the draft is incomplete, repair the draft and review again.
- If coverage cannot be completed cleanly because the project description or steering context is ambiguous, contradictory, or underspecified, stop and ask the user to clarify instead of guessing.

## EARS and Testability Review

- Every acceptance criterion must follow the EARS rules defined in `ears-format.md`.
- Every requirement must be testable, observable, and specific enough that later design and validation can verify it.
- Remove implementation details that belong in `design.md` rather than `requirements.md`.
- Requirement headings must use numeric IDs only; do not mix numeric and alphabetic labels.

## Structure and Quality Review

- Group related behaviors into coherent requirement areas without duplicating the same obligation across multiple sections.
- Make inclusion/exclusion boundaries explicit when the feature scope could otherwise be misread.
- Keep boundary statements lightweight and observable: describe feature responsibility and adjacent expectations without prescribing components, layers, or internal ownership.
- Ensure non-functional expectations remain user-observable or operator-observable; move technology choices and internal architecture detail out of requirements.
- Normalize vague language such as "fast", "robust", or "secure" into concrete user-visible expectations whenever the source material supports it.

## Mechanical Checks

Before applying judgment, verify these mechanically:
- **Numeric IDs present**: Every requirement heading has a numeric ID (1, 1.1, 2, etc.). Scan the draft for headings without IDs.
- **Acceptance criteria exist**: Every requirement has at least one EARS-format acceptance criterion. Scan for requirements with no "When/If/While/Where" acceptance statements.
- **No implementation language**: Scan for technology-specific terms (database names, framework names, API patterns) that belong in design, not requirements. Flag any found.
- **Spec size assessed**: Record the verdict, projected executable task range, independent responsibility seams, and rationale in the review result.

## Review Loop

- Run mechanical checks first, then judgment-based review.
- If issues are local to the draft, repair the draft and re-run the review gate.
- Keep the loop bounded: no more than 2 review-and-repair passes before escalating a real ambiguity back to the user.
- If the same structural scope problem remains at the repair limit because the requirements cannot be reviewed coherently as one unit, return `SPLIT_REQUIRED` instead of continuing the loop.
- Write `requirements.md` only after the review gate passes.
- Do not use the review loop to merge or omit requirements solely to pass the size gate.
