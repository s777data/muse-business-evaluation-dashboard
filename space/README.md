# Space dashboard source — MU Mini Dashboard

This folder is a mirror of the actual source code of the Muse Space
**MU Mini Dashboard** (slug `mu-mini-dashboard`), added at BIG SANDY's
request so all dashboard code lives in this repository.

## What's here

- `client/src/` — the dashboard UI (`App.tsx` is the main component:
  score cards, KPI and valuation tables, peer section, moat section with
  replicability checks, customer-concentration panel, DCF-vs-price bar,
  and the bottom "Why these scores?" explanations)
- `client/index.html`, `client/build.mjs`, `client/tsconfig.json`
- `server/src/` — Space server actions (including the latest-close
  price refresh) and the data schema
- `drizzle/` — database migrations for the Space's snapshot storage
- `package.json`, `space.json`, `tsconfig.base.json`, `bunfig.toml`
- `DATA-PLAN.md` — the builder's data plan and sources for the Space

## What's not here, and why

Generated or runtime files are not source code and are excluded:
`client/dist*`, `.space-build*`, `app.db*` (the Space's live database),
`.harness/`, `audits/`, `.bun-cache/`, and the icon image.

## Relationship to the rest of this repo

- `dashboard/` (repo root) is the standalone static dashboard that
  renders the deterministic engine's `output/scores.json`.
- This `space/` folder is the Muse Space version. The Space currently
  displays analyst-judgment scores; the deterministic engine in
  `src/evaluator/` produces the formula scores. Which set the Space
  displays is BIG SANDY's decision — see the repo README.
