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
- [x] GitHub remote — github.com/drwjkirkpatrick-web/brickfolio (public), `main` pushed

### In Progress
- [ ] Prompt 3 — build catalog (subagent running)
- [ ] Prompt 7 — scan sessions (subagent running)
- [ ] Prompt 9 — constrained generator (subagent dispatched; dep P8 committed)

### Not Started
- [ ] Prompt 5 — recommender & reports (after 3)
- [ ] Prompt 10 — pipeline, export, docs
- [ ] CLI wiring (parent): `builds list|search|recommend`, `photo scan`, `generate`

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

> **Next:** Prompt 3 lands → dispatch P5; Prompt 9 lands → wire `generate` + `builds`/`photo scan` CLI; P10 last.

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

## 8. References

- Build plan: `PROMPTS.md`
- Research basis: session research 2026-10-17 (Brickit, Brickognize API, Rebrickable API v3 + CSVs, LDraw OMR, ldraw-nova, Prompt-to-Parts arXiv:2512.15743)