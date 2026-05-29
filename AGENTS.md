# AGENTS.md

This file provides guidance to Codex (codex.ai/code) when working with code in this repository.

The architecture, commands, game rules, coordinate conventions, and constraints
are documented in **[CLAUDE.md](CLAUDE.md)** — read that file; it is the single
source of truth and is kept in sync with the code.

Key point to remember: this is a first-year student's coursework, written at the
level the course taught — **plain OOP** (`class` + `__init__` + methods +
`@classmethod`), `requests`, `with open`, `json`, comprehensions, `try/except`,
lambda, Flask, Folium. Do NOT add things beyond that level: no `@dataclass`,
`Protocol`, dependency injection, type hints, `httpx`, caching, or a pytest suite
(use `check_rules.py` instead). For a line-by-line Chinese explanation (incl. an
OOP-from-zero section), see `程式講解.md`.

Quick commands:

```bash
pip install -e .            # install
python -m compileall app    # syntax check
python -m app.web           # run dev server at http://127.0.0.1:5000
```
