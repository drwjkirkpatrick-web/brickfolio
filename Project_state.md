# Project State: brickfolio

> **Last updated:** 2026-10-17
> **Current phase:** build
> **Overall health:** green

---

## 1. Goal (1–2 sentences)

Photograph a LEGO pile, auto-identify the parts, keep a Rebrickable-compatible
inventory, and present — plus LLM-generate — builds possible from those parts.

## 2. Current Status

### Done
- [x] Research: open-source landscape (Brickit closed; Brickognize API; Rebrickable data; LDraw ecosystem)
- [x] PROMPTS.md — 10-prompt build plan with dependency table
- [x] Prompt 1 — foundation: models, schema, Database, `inventory` CLI, tests (19 tests)
- [x] Prompt 2 — catalog importer: Rebrickable CSV loader, fixtures, tests (c6251e6)
- [x] Prompt 4 — matcher engine: exact/shape coverage modes, missing-parts, ranking (5427ba7)
- [x] Prompt 6 — Brickognize client: multipart POST, defensive parsing, fixtures (fecf7e9)
- [x] Prompt 8 — LDraw parser + validator: mpd flattening, collisions, fixtures (f9e4022)
- [x] CLI wiring — `catalog import|stats`, `photo identify` (parent)
- [x] Prompt 3 — build catalog: builds over set inventories, search, tests (dff618e)
- [x] Prompt 7 — scan sessions: detector ABC, session workflow, tests (a58ea89)
- [x] CLI wiring — `builds list|search|show`, `photo scan` (parent, 34cb80b)
- [x] Prompt 5 — recommender & reports: coverage bars, READY flags, missing lists (ec8a36a)
- [x] Prompt 9 — constrained generator: ChatClient, repair loop, tests (ca1a029)
- [x] Prompt 10 — pipeline: offline demo, Rebrickable export, tests (3a9b0c1)
- [x] CLI wiring — `demo`, `inventory export` (parent, 9fe72d8)
- [x] README (Phosphorus voice) + LICENSE (MIT)
- [x] GitHub remote — github.com/drwjkirkpatrick-web/brickfolio (public), `main` pushed

### In Progress
(none — 10/10 prompts shipped)

### Backlog (v0.2 ideas)
- Real pile detector: YOLO on synthetic LDraw pile renders (plugs into the Detector ABC)
- Surface `color_substituted` ("buildable, but swapped colors") next to READY
- `demo --mode/--top` flags (currently module constants)
- Import LDraw OMR / BrickHub models as extra build candidates

## 3. Architecture & Key Decisions

| Decision | Rationale | Date |
|---|---|---|
| SQLite schema mirrors Rebrickable CSV layout | canonical catalog, offline, no migrations needed | 2026-10-17 |
| Brickognize API (free, keyless) for part ID in v1 | avoids training a pile detector before value is proven | 2026-10-17 |
| stdlib-only core | runs on the Jetson; zero heavy deps | 2026-10-17 |
| LDraw .mpd as build interchange format | LeoCAD/LPub3D ecosystem renders + instructions | 2026-10-17 |
| Detector is an ABC behind the session | real pile detector (YOLO on synthetic renders) slots in later | 2026-10-17 |
| Local OpenAI-compatible LLM endpoint for generation | generation constrained to inventory runs on-device | 2026-10-17 |

## 4. Blockers & Risks

- **Risk:** Brickognize API response shape may drift → isolated in one module; defensive parser; mocked tests.
- **Risk:** pile-level detection is unsolved in open source → v1 identifies per-photo; detector ABC leaves the seam.
- **Watch:** `write_file` timed out once on this Jetson (Project_state.md) — retry once, fall back to terminal write.

## 5. Next Step (only ONE)

> **Next:** None — the 10-prompt plan is complete. Pick from the v0.2 backlog.

## 6. Environment & Tooling Notes

- Python 3.14; project venv `.venv`; tests: `.venv/bin/python -m pytest -q`
- DB default: `data/brickfolio.db` (gitignored); tests use tmp_path DBs
- Shared files parent-owned: `models.py`, `db.py`, `cli.py`, README, Project_state, PROMPTS
- Skill: software-development-workflows (prompts-first build pattern); delegation.child_timeout_seconds=1800

## 7. Recent Session Log

- 2026-10-17: Researched landscape → 10 prompts authored → Prompt 1 built & committed.
- 2026-10-17: Wave A landed 2/4/6 — verified 118 tests green on disk, committed per prompt; P8 finishing; dispatched P3+P7.
- 2026-10-17: Wave A complete — P8 verified (36 tests) & committed; wired catalog+photo CLI; dispatched P9.
- 2026-10-17: Created GitHub repo (public) and pushed `main` (7 commits).
- 2026-10-17: P5 verified & pushed; wired recommend+generate CLI; suite 258.
- 2026-10-17: P10 verified (12 tests) & pushed; wired demo+export; README (Phosphorus) + LICENSE; suite 272 passed, 3 skipped — plan complete.

## 8. References

- Build plan: `PROMPTS.md`
- Research basis: session research 2026-10-17 (Brickit, Brickognize API, Rebrickable API v3 + CSVs, LDraw OMR, ldraw-nova, Prompt-to-Parts arXiv:2512.15743)