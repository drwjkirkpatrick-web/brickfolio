🔥 # brickfolio

[![CI](https://github.com/drwjkirkpatrick-web/brickfolio/actions/workflows/ci.yml/badge.svg)](https://github.com/drwjkirkpatrick-web/brickfolio/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-272%20passed%20%7C%203%20skipped-brightgreen.svg)](#tests)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-stdlib%20only-brightgreen.svg)](requirements.txt)
[![Offline-first](https://img.shields.io/badge/network-optional%20(photo%20ID%20%2F%20LLM%20only)-blue.svg)](#data-sources--licenses)
[![Catalog: Rebrickable](https://img.shields.io/badge/catalog-Rebrickable-orange.svg)](https://rebrickable.com)
[![Part ID: Brickognize](https://img.shields.io/badge/part%20ID-Brickognize%20API-ffcf00.svg)](https://brickognize.com)
[![Models: LDraw](https://img.shields.io/badge/models-LDraw%20(.ldr)-red.svg)](https://www.ldraw.org)
[![Tested on Jetson](https://img.shields.io/badge/tested%20on-Jetson%20ARM64-76B900.svg)](https://developer.nvidia.com/embedded/compute/jetson)
[![Stars](https://img.shields.io/github/stars/drwjkirkpatrick-web/brickfolio.svg)](https://github.com/drwjkirkpatrick-web/brickfolio/stargazers)
[![Last commit](https://img.shields.io/github/last-commit/drwjkirkpatrick-web/brickfolio.svg)](https://github.com/drwjkirkpatrick-web/brickfolio/commits/main)
[![Repo size](https://img.shields.io/github/repo-size/drwjkirkpatrick-web/brickfolio.svg)](https://github.com/drwjkirkpatrick-web/brickfolio)
[![Issues](https://img.shields.io/github/issues/drwjkirkpatrick-web/brickfolio.svg)](https://github.com/drwjkirkpatrick-web/brickfolio/issues)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/drwjkirkpatrick-web/brickfolio/pulls)

**Point your camera at a pile of loose bricks — and find out what you can build tonight.**

brickfolio is an open-source, offline-first companion for every bin of LEGO
you've ever owned. Photograph your parts, identify them automatically, keep a
tidy inventory, see which sets you can already build — and then do the one
thing the closed apps won't: **design brand-new builds constrained to the
parts you actually own**, validated by a parser and a collision checker
before you ever see them.

It's an open answer to the closed-source Brickit concept, built respectfully
on three great community ecosystems: the **Rebrickable** catalog, the free
**Brickognize** recognition API, and the **LDraw** model format.

## What you get

- **Identify parts from photos** — `photo identify` calls Brickognize's free,
  keyless API; `photo scan` turns pile photos into a reviewable candidate list
  you accept or reject, with pluggable region detection.
- **A real inventory** — a Rebrickable-compatible SQLite collection. Import
  the full catalog from Rebrickable's free CSV downloads; export your parts
  back out as a Rebrickable part-list CSV whenever you like.
- **"What can I build?"** — `builds recommend` ranks every set in your catalog
  by coverage, with color-aware matching, `READY` flags, coverage bars, and
  an exact shopping list of what's missing.
- **Generate new builds** — `generate` hands your inventory to a local,
  OpenAI-compatible LLM (llama-server, Ollama, LM Studio, anything) and asks
  for an LDraw model that uses *only the parts you own* — then proves it:
  every model is parsed, checked against your stock, and collision-tested
  before it's shown. Open the result in LeoCAD or LPub3D.
- **A 10-second demo** — `demo` runs the entire pipeline offline against
  bundled fixtures: import → scan → recommend. No network, no LLM, no LEGO
  required.

## Quick start

```bash
git clone https://github.com/drwjkirkpatrick-web/brickfolio
cd brickfolio
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m brickfolio.cli demo        # watch the whole pipeline run
```

Then make it yours:

```bash
# bring your catalog (free CSVs from rebrickable.com/downloads)
.venv/bin/python -m brickfolio.cli catalog import --dir path/to/csvs

# identify a part from a photo
.venv/bin/python -m brickfolio.cli photo identify IMG_4032.jpg

# scan a few pile photos and accept what's right
.venv/bin/python -m brickfolio.cli photo scan pile1.jpg pile2.jpg --all

# what can I build with what I have?
.venv/bin/python -m brickfolio.cli builds recommend --verbose

# design something new from exactly your parts (needs a local LLM endpoint)
BRICKFOLIO_LLM_BASE_URL=http://127.0.0.1:8080 \
  .venv/bin/python -m brickfolio.cli generate "a tiny lighthouse" --out lighthouse.ldr

# export your collection back to Rebrickable
.venv/bin/python -m brickfolio.cli inventory export my-parts.csv
```

## How it fits together

```
photos -> Brickognize API -> your inventory (SQLite, Rebrickable-compatible)
                                     |
              +----------------------+----------------------+
              v                                             v
   coverage matcher -> ranked builds               inventory-constrained LLM
   (READY / missing parts)                         -> LDraw model -> validator
                                                          |
                                                  .ldr file (LeoCAD / LPub3D)
```

## Tests

272 passed, 3 skipped (Pillow-only crop tests), fully offline:

```bash
.venv/bin/python -m pytest -q
```

The runtime is Python 3.10+ stdlib only — Pillow is an optional accelerator
for per-region crops, and the LLM endpoint is only needed for `generate`.

## Data sources & licenses

- **Rebrickable** — catalog CSVs and part numbering conventions (free
  account download). Rebrickable's data belongs to Rebrickable.
- **Brickognize** — free, keyless photo recognition API for single parts.
- **LDraw** — the open LEGO CAD format; generated models are plain `.ldr`.

brickfolio is MIT-licensed. LEGO® is a trademark of the LEGO Group; this is
an independent fan project with no affiliation.

## Contributing

Issues and PRs are welcome. The codebase is deliberately modular — one
module per pipeline stage — and `PROMPTS.md` documents the full 10-prompt
build plan; `Project_state.md` tracks live status.

Built by [drwjkirkpatrick-web](https://github.com/drwjkirkpatrick-web).