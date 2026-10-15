# ContextOps

ContextOps is a small, offline-first Human × AI review application for fictional scheduling actions. It makes contextual evidence visible before a person accepts or rejects a proposed action, preserves the decision and rationale, and turns repeated errors into reusable quality rules.

The first implementation uses Python 3.12, FastAPI, SQLite, server-rendered HTML, and minimal JavaScript. The default analyzer is deterministic and does not call an external AI service.

## Prerequisites

- Python 3.12.8 (the required version is recorded in `.python-version`)
- `make`
- Docker 27 or newer for container builds

No global Python packages are required. Install dependencies into a project-local virtual environment.

## Local setup

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --requirement requirements.lock
cp .env.example .env
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1` and copy the example file with `Copy-Item .env.example .env`.

## Run

```sh
make run
```

Open <http://127.0.0.1:8000>. The health check is available at <http://127.0.0.1:8000/health>.

## Test

The one supported test command is:

```sh
make test
```

Tests use deterministic fixtures and an isolated SQLite database. They do not make network requests.

## Docker

Build the image:

```sh
docker build --tag contextops:local .
```

Run the application:

```sh
docker run --rm --publish 8000:8000 contextops:local
```

Run the test suite in the same image:

```sh
docker run --rm --entrypoint python contextops:local -m pytest
```

The container runs as an unprivileged user. Its SQLite data directory is writable inside the container; mount a local directory at `/app/data` only when persistence across container runs is needed.

## Demonstration workflow

The seeded cases are fictional and deterministic:

1. Open **Quarterly design review proposed after hours**.
2. Inspect the original proposal, working-hours evidence, and the separate warning and unresolved-ambiguity findings.
3. Select **Reject proposal**, enter a rationale, and record the decision. The original proposal and audit event remain available.
4. Create a draft rule from the confirmed working-hours violation. The rule exposes its title, description, failure pattern, triggering condition, expected behavior, test case, evaluation result, and status.
5. Activate the rule explicitly from the review queue. Rules never become active automatically.
6. Open **Roadmap review proposed after hours**, choose **Evaluate active rules**, and verify that the activated rule is reported as `matched`.
7. If needed, disable the rule and verify that later evaluations no longer run it.

The regression test in `tests/test_review_flow.py` executes this same path, including the pre-activation non-evaluation check and the post-activation match.

## Configuration

Copy `.env.example` to `.env` for local configuration. `.env` is ignored by Git. The MVP accepts only fictional scheduling data and does not require an API key or external service.

## Project status

The repository is being delivered in small working milestones. The completed workflow will load a fictional case, show confirmed violations separately from warnings and unresolved ambiguity, record a human rejection with rationale and audit history, create a draft quality rule, activate it explicitly, and evaluate it against a similar regression case.
