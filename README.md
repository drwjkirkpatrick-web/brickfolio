# brickfolio

Photograph a LEGO pile → identify the parts → see what you can build — and
generate new builds constrained to the parts you actually own.

Open-source take on the photo-inventory concept popularized by the closed
Brickit app, built around the Rebrickable catalog, the free Brickognize
recognition API, and LDraw models that validate against your inventory.

> Status: scaffold (Prompt 1 of 10) — see `PROMPTS.md` for the build plan.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # run the test suite
.venv/bin/python -m brickfolio.cli inventory add 3001 0 5
.venv/bin/python -m brickfolio.cli inventory list
```

*(Full README arrives with Prompt 10.)*