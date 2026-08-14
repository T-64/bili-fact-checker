# Release checklist

Tag `v1.0.0` only when all of the following are true:

1. `pytest -q` is green on Python 3.11+.
2. `python -m compileall -q src server` succeeds.
3. Wheel install smoke: `bili-fact-checker doctor --json` with a fixture key.
4. Docker image user is `10001:10001`, and Compose `/health` responds.
5. README five-minute path matches the packaged CLI (`setup --save`, `doctor`,
   `serve`).
6. `CHANGELOG.md` describes the tagged version.
7. No API keys, `SESSDATA`, or `.env` files are in the tree.

Version lives in `pyproject.toml` and `src/bili_fact_checker/__init__.py`.
Keep them identical.
