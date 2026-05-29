# AGENTS.md

This file provides guidance to Codex (codex.ai/code) when working with code in this repository.

The architecture, commands, game rules, coordinate conventions, and constraints
are documented in **[CLAUDE.md](CLAUDE.md)** — read that file; it is the single
source of truth and is kept in sync with the code.

Key point to remember: this project was deliberately simplified to **basic Python
only** (dicts, lists, functions, if/for/while — no classes, dataclasses,
Protocols, dependency injection, caching, or test suite). Do not refactor it back
into OOP. For a line-by-line Chinese explanation, see `程式講解.md`.

Quick commands:

```bash
pip install -e .            # install
python -m compileall app    # syntax check
python -m app.web           # run dev server at http://127.0.0.1:5000
```
