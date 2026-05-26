# UI/UX Quality Bar

The rebuilt UI should feel like a focused trading research workstation:
practical, attractive, modern, friendly, and restrained. It should avoid both
raw developer-tool roughness and decorative overdesign.

## Product Principles

- Workflow first: organize screens around user tasks, not backend modules.
- Data density with hierarchy: show enough information for comparison without
  turning every screen into a spreadsheet.
- Clear status: every long-running task shows progress, current step, logs,
  artifacts, failures, and retry/cancel actions.
- Inspectable evidence: candidates, reviews, charts, archive rows, and analysis
  decisions are easy to trace.
- Progressive disclosure: advanced config is available without overwhelming
  routine operation.
- Not overdesigned: avoid marketing-page composition, decorative blobs,
  excessive animation, and ornamental cards.

## Required Surfaces

- run setup and configuration;
- run monitor with logs and status;
- candidate table/detail view;
- review evidence view;
- chart/evidence inspection;
- history/archive browser;
- settings and data-source health;
- error and recovery screens.

## Interaction Requirements

- Accessible contrast, focus states, labels, and keyboard navigation.
- Responsive layouts for desktop and mobile-width inspection.
- Touch targets at least 44x44px where touch interaction is expected.
- Loading states for work over 300ms; progress states for long jobs.
- Empty states with next action; error states near the failed action.
- Destructive actions require confirmation and clear rollback expectation.
- Charts include legends, tooltips, readable axes, and do not rely on color
  alone.

## Visual Direction

- Use a restrained modern dashboard style, not a landing-page hero.
- Prefer semantic color tokens over scattered raw colors.
- Use consistent spacing, typography, icon style, and elevation.
- Keep animation short, meaningful, and reduced-motion aware.
- Use cards only for repeated items, panels, or focused tools; do not nest
  cards inside cards.

## Verification

Each frontend slice should provide:

- Playwright or browser smoke for the touched workflow;
- desktop screenshot evidence;
- mobile-width screenshot evidence for the same workflow;
- keyboard navigation spot check;
- loading, empty, and error-state evidence when the workflow can produce them;
- no horizontal scroll or text overlap in standard viewports.
