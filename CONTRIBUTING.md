# Contributing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

Do not commit API keys, `SESSDATA`, or `.env` files. Secrets belong in the
local config file created by `bili-fact-checker setup --save`, or in
environment variables.

Pull requests should keep `pytest -q` and `ruff check src tests` green. Prefer
small, test-backed changes to the evidence contract over new provider surface
area.
