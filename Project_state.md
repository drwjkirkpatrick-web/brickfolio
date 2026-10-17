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
- [x] Prompt 1 — foundation: models, schema, Database, `inventory` CLI, tests

### In Progress
- [ ] Wave A: Prompts 2 (catalog importer), 4 (matcher), 6 (Brickognize), 8 (LDraw parser/validator)

### Not Started
- [ ] Wave B: Prompts 3 (builds), 5 (recommender), 7 (scan sessions), 9 (generator)
- [ ] Prompt 10 — pipeline, export, docs
- [ ] GitHub remote (name/visibility to confirm with Walker)

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

## 5. Next Step (only ONE)

> **Next:** Wave A subagents (Prompts 2, 4, 6, 8) land → parent verifies suite, wires CLI, commits per prompt.

## 6. Environment & Tooling Notes

- Python 3.14; project venv `.venv`; tests: `.venv/bin/python -m pytest -q`
- DB default: `data/brickfolio.db` (gitignored); tests use tmp_path DBs
- Shared files parent-owned: `models.py`, `db.py`, `cli.py`, README, Project_state, PROMPTS
- Skill: software-development-workflows (prompts-first build pattern)

## 7. Recent Session Log

- 2026-10-17: Researched open-source landscape → 10 prompts authored → Prompt 1 built & committed → Wave A dispatched (2, 4, 6, 8).

## 8. References

- Build plan: `PROMPTS.md`
- Research basis: session research 2026-10-17 (Brickit, Brickognize API, Rebrickable API v3 + CSVs, LDraw OMR, ldraw-nova, Prompt-to-Parts arXiv:2512.15743)