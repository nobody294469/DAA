# Home page overrides

Overrides `../MASTER.md` for `src/clindoc/web/static/index.html` and records where the shared
foundation (`assets/css/app.css`) deliberately departs from the generated master file.

## Foundation-wide departures (apply to every page)

- **Palette neutralised.** MASTER's sky background `#F0F9FF` / navy foreground `#0C4A6E` became
  clinical neutrals (`--color-bg #F6F8FA`, `--color-text #0E1A27`). Trust blue `#0369A1` stays the
  primary; `#A16207` is the CTA accent (`.btn--cta`), used once per page for the main action.
- **Dark mode is its own palette** (selected steps, not an inversion), declared twice in app.css:
  under `prefers-color-scheme: dark` guarded by `:root:not([data-theme="light"])`, and under
  `:root[data-theme="dark"]`. Keep the two blocks identical.
- **`--color-secondary #0EA5E9` is decorative only** (2.8:1 on white). Never text or meaningful UI.
- **No hover lift on cards/buttons.** MASTER's `translateY` hovers were replaced by border/shadow
  changes so dense tool pages do not jitter.
- **Numbers in the body sans.** Stat values use Source Sans 3 (proportional figures); Lexend is for
  headings only. Tables and axis ticks use tabular figures.

## Home specifics

- Section order: hero (claim + specimen) > benchmark KPI tiles > five-step pipeline > selective
  cascade routing > capability cards > "What this is not" > footer.
- Every number comes from `GET /api/results` (`headline`, `fhir`, `risk_coverage.operating_point`,
  `escalation.disagreement_share`, `verify_presets`). Nothing numeric is written into the HTML.
- The hero specimen shows a real V3 OOD preset with its **benchmark label**, never presented as live
  model output.
- Motion: `.reveal` fade-up on first scroll into view only; skipped entirely under
  `prefers-reduced-motion`.
