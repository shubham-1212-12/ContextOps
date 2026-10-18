# ContextOps

ContextOps is an offline-first Human × AI review application for fictional scheduling actions. It shows contextual evidence before a person accepts or rejects a proposal, preserves the decision and rationale, and turns repeated errors into reusable quality rules.

The application uses Python 3.12, FastAPI, SQLite, server-rendered HTML, and minimal JavaScript. Its analyzer is deterministic and does not call an external AI service.

## Start locally on Linux or macOS

Run these commands from a terminal. Replace the Git URL with the URL for your copy of the repository.

```sh
git clone https://github.com/shubham-1212-12/ContextOps.git
cd ContextOps
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --requirement requirements.lock
make run
```

Open <http://127.0.0.1:8000> in a browser. Check the running application with:

```sh
curl --fail http://127.0.0.1:8000/health
```

The health endpoint returns `{"status":"ok","service":"contextops"}`. Stop the server with `Ctrl+C`.

The local workflow requires Python 3.12.8, recorded in `.python-version`, and `make`. Docker is optional for local development.

Before starting the application, configure the required administrator credentials in your shell. `.env.example` intentionally leaves both credential values empty and cannot start the application unchanged. Use private values and set `CONTEXTOPS_COOKIE_SECURE=false` explicitly for local HTTP development only:

```sh
export CONTEXTOPS_ADMIN_USERNAME=your_local_admin_username
export CONTEXTOPS_ADMIN_PASSWORD='your_local_long_random_password'
export CONTEXTOPS_COOKIE_SECURE=false
```

## Run and test on Windows PowerShell

Use these commands from the repository root. They do not require `make`.

Clone and enter the repository first if you have not done so:

```powershell
git clone https://github.com/shubham-1212-12/ContextOps.git
Set-Location ContextOps
```

Create the project environment and install the locked dependencies:

```powershell
py -3.12 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --requirement requirements.lock
```

Set the required administrator credentials before starting the application. `.env.example` intentionally leaves both credential values empty and cannot start the application unchanged:

```powershell
$env:CONTEXTOPS_ADMIN_USERNAME = "your_local_admin_username"
$env:CONTEXTOPS_ADMIN_PASSWORD = "your_local_long_random_password"
$env:CONTEXTOPS_COOKIE_SECURE = "false"
```

Start ContextOps:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) "src"
& ".\.venv\Scripts\python.exe" -m uvicorn contextops.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. In a second PowerShell window, run the health check:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
```

Run the test suite with the same source path:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) "src"
& ".\.venv\Scripts\python.exe" -m pytest -q
```

The tests use deterministic fixtures and isolated SQLite databases. They do not make network requests.

## Run and test with Make on Unix-like systems

From the repository root, run the supported commands on Linux or macOS:

```sh
make run
make test
```

`make run` starts Uvicorn on `127.0.0.1:8000`. `make test` runs the same offline test suite shown in the Windows instructions.

## Build and run with Docker

Build the image from the repository root:

```sh
docker build --tag contextops:local .
```

For a local Docker run, create an ignored `.env` file from `.env.example`, replace the administrator credential placeholders, and pass it to Docker. Docker loads this file into the container; the application does not load it itself:

```sh
cp .env.example .env
# Edit .env and replace the administrator credential placeholders.
```

Run the application without persistent storage:

```sh
docker run --rm --env-file .env --publish 8000:8000 contextops:local
```

Run the test suite in the same image:

```sh
docker run --rm --entrypoint python contextops:local -m pytest
```

The container runs as the unprivileged user `appuser` with UID `10001`. The no-volume command loses new cases, decisions, rules, and audit events when you remove the container.

## Deploy with persistent storage

Use a Docker volume so SQLite data survives container restarts and replacement:

```sh
docker volume create contextops-data
docker build --tag contextops:local .
docker run --detach \
  --name contextops \
  --env-file .env \
  --publish 8000:8000 \
  --volume contextops-data:/app/data \
  --restart unless-stopped \
  contextops:local
```

The application uses `/app/data/contextops.db` inside the image. The named volume persists that file. A host bind mount also works, but the host directory must be writable by UID `10001`.

Check the deployed process locally on the host:

```sh
curl --fail http://127.0.0.1:8000/health
```

Stop it with:

```sh
docker stop contextops
```

> **Security warning**
> ContextOps now requires one administrator login, but it still has no multi-user identity, roles, tenant isolation, or built-in TLS. Use HTTPS in any shared deployment. Keep the service behind an HTTPS reverse proxy or an authenticated private network, and restrict access to trusted clients.

SQLite supports this prototype when all workers use the same persistent database file. The repository does not include database migrations, backups, replication, or a production database configuration, and SQLite is not a substitute for a shared database across separate hosts or volumes. A new empty database also receives the fictional seeded cases automatically.

## Demonstration workflow

The seeded cases are fictional and deterministic:

1. Open **Quarterly design review proposed after hours**.
2. Inspect the original proposal, working-hours evidence, and the separate warning and unresolved-ambiguity findings.
3. Choose **Approve**, **Edit**, **Reject**, **Defer**, or **Escalate**, enter a rationale, and record the decision. For **Edit**, provide the revised proposal as JSON. The original proposal and audit event remain available.
4. Create a draft rule from the confirmed working-hours violation. The rule exposes its title, description, failure pattern, triggering condition, expected behavior, test case, evaluation result, and status.
5. Activate the rule explicitly from the review queue. Rules never become active automatically.
6. Open **Roadmap review proposed after hours**, choose **Evaluate active rules**, and verify that the activated rule is reported as `matched`.
7. If needed, disable the rule and verify that later evaluations no longer run it.

The review queue is the home page. Use <http://127.0.0.1:8000/rules> to manage reusable quality rules separately from the case queue.

The regression test in `tests/test_review_flow.py` executes this same path, including the pre-activation non-evaluation check and the post-activation match.

## Use the manual review workflow

ContextOps currently supports a manual, offline prototype workflow for reviewing user-submitted scheduling proposals:

1. Open the review queue and choose **New case**.
2. Enter the proposed event, an ISO-8601 start time with an explicit timezone offset, duration, timezone, and the context known to the reviewer.
3. Submit the case. ContextOps stores the original proposal, runs deterministic checks, records a `case_created` audit event, and opens the normal case-review page.
4. Review confirmed violations, warnings, and unresolved ambiguity. Missing calendar evidence is shown as uncertainty; ContextOps does not invent availability.
5. Record an approve, edit, reject, defer, or escalate decision with a rationale. Edited proposals are stored separately; the original proposal remains unchanged.
6. Inspect the collapsed audit history for case creation, decisions, quality-rule changes, and rule evaluations.

This manual prototype works without an AI API, live calendar, or external service after the administrator signs in. Quality rules can be created from confirmed issues and explicitly activated for later cases.

## Integrate through the API

An authenticated external application can submit a proposal, receive a generated case ID, and retrieve the stored findings. The API accepts JSON and does not require an AI service. First establish the administrator session through `/login`; send the session cookie on API requests and the session CSRF token in `X-CSRF-Token` for every state-changing request.

The `curl` examples below assume that `cookies.txt` contains the session cookie from a successful form login and that `session_csrf_token_here` is the CSRF token from an authenticated HTML page. Keep the administrator password and cookie jar out of source control.

Submit a scheduling proposal with `POST /api/cases`:

```sh
curl --fail \
  --request POST \
  --url http://127.0.0.1:8000/api/cases \
  --cookie cookies.txt \
  --header 'X-CSRF-Token: session_csrf_token_here' \
  --header 'Content-Type: application/json' \
  --data '{
    "title": "Planning review",
    "original_proposal": {
      "event": "Planning review",
      "start": "2026-10-20T14:00:00+05:30",
      "duration_minutes": 60,
      "timezone": "Asia/Kolkata"
    },
    "context": {
      "timezone": "Asia/Kolkata",
      "working_hours": {"start": "09:00", "end": "17:00"},
      "calendar_data": {"status": "not_provided"},
      "constraints": ["Keep the review within working hours."],
      "blocked_time": []
    }
  }'
```

The successful response has HTTP `201` and contains these fields:

- `case_id`: the generated case ID, such as `case-0123456789abcdef0123456789abcdef`
- `case`: the stored case object with `id`, `title`, `original_proposal`, `context`, `issues`, `decisions`, `audit`, and `evaluations`

The live response contains the submitted proposal and context, detected issue objects, an initial `case_created` audit event, and empty decision and evaluation arrays.

Retrieve the stored case with the returned ID:

```sh
curl --fail \
  --cookie cookies.txt \
  http://127.0.0.1:8000/api/cases/case-0123456789abcdef0123456789abcdef
```

Case creation returns HTTP `400` with a `detail` string when the JSON body or scheduling data is invalid. Validation runs before persistence, so an invalid request does not create a partial case.

The other current API routes are:

| Method and path | Purpose |
| --- | --- |
| `GET /health` | Check process liveness |
| `GET /login` | Render the administrator login form |
| `POST /login` | Create an authenticated session |
| `POST /logout` | End the authenticated session |
| `GET /api/cases/{case_id}` | Retrieve one case |
| `GET /api/rules` | List quality rules |
| `POST /api/cases/{case_id}/decision` | Record a human decision |
| `POST /api/cases/{case_id}/rules` | Draft a rule from an issue |
| `POST /api/rules/{rule_id}/activate` | Activate a rule |
| `POST /api/rules/{rule_id}/disable` | Disable a rule |
| `POST /api/cases/{case_id}/evaluate-rules` | Evaluate active rules |

FastAPI also exposes interactive documentation at <http://127.0.0.1:8000/docs>. The current handlers accept raw JSON requests, so the generated OpenAPI document does not describe typed request bodies for the mutation routes.

The API requires the administrator session cookie and a matching CSRF token for state-changing requests. It does not currently provide case listing, webhooks, live calendar access, or scheduling execution. An external agent must retain case IDs and implement the login/session flow, or use a separate integration layer for those needs.

## Configure the application

The application does not load `.env` files automatically. Copying `.env.example` to `.env` unchanged cannot start the application because the required credential values are empty. Set real administrator credentials through deployment secrets, export them in the shell, or pass them through Docker's `--env-file` option. Never commit real credentials.

`DATABASE_PATH` is the active application setting. It selects the SQLite file and defaults to `data/contextops.db`:

```sh
export DATABASE_PATH=data/contextops.db
make run
```

In PowerShell, set the same variable before starting Uvicorn:

```powershell
$env:DATABASE_PATH = "data/contextops.db"
```

Authentication requires these environment variables:

- `CONTEXTOPS_ADMIN_USERNAME`: the single administrator username
- `CONTEXTOPS_ADMIN_PASSWORD`: the single administrator password
- `CONTEXTOPS_COOKIE_SECURE`: set to `true` for every HTTPS deployment; use `false` only for explicitly local HTTP development
- `CONTEXTOPS_SESSION_TTL_SECONDS`: positive session lifetime in seconds; defaults to `28800`

The application fails during startup when either administrator credential is missing. It stores opaque sessions, login CSRF challenges, and failed-login throttling state in SQLite, so workers and process restarts can share authentication state when they use the same database file. Keep `DATABASE_PATH` on persistent storage. Separate hosts or containers with separate database files still require a shared session store or a single application instance.

`HOST` and `PORT` affect the `make run` shell command only. Direct Uvicorn commands use their `--host` and `--port` arguments. The Docker image currently binds to `0.0.0.0:8000`.

The following `.env.example` entries are present but are not currently implemented as application behavior:

- `APP_ENV`
- `LOG_LEVEL`
- `AI_PROVIDER`

For HTTPS deployments, `CONTEXTOPS_COOKIE_SECURE=true` is mandatory. Configure real administrator credentials through the deployment secret store; do not use the unchanged `.env.example` values. The MVP accepts fictional scheduling data, uses the deterministic analyzer, and does not require an API key or external service.

## Configure a Netrun deployment

For a source-based Python deployment, configure Netrun to install from the root `requirements.txt` file. It installs the pinned dependencies from `requirements.lock` and the local `contextops` package, which makes the `src/contextops` package importable without a `PYTHONPATH` override. `requirements.lock` remains the repository's reproducible dependency lockfile:

```sh
python -m pip install --requirement requirements.txt
```

After that install, use this startup command so the application listens on all interfaces and honors Netrun's `PORT` value:

```sh
python -m uvicorn contextops.main:app --host 0.0.0.0 --port "${PORT:-8000}"
```

For a source-based Netrun deployment, configure these exact names through Netrun's secret and runtime environment facilities:

```text
CONTEXTOPS_ADMIN_USERNAME=your_private_admin_username
CONTEXTOPS_ADMIN_PASSWORD=your_private_admin_password
CONTEXTOPS_COOKIE_SECURE=true
CONTEXTOPS_SESSION_TTL_SECONDS=28800
DATABASE_PATH=/data/contextops.db
CONTEXTOPS_CSRF_DIAGNOSTICS=false
```

Store both administrator credentials as Netrun secrets. Do not put them in GitHub, the Dockerfile, a committed `.env` file, or a public deployment command. `CONTEXTOPS_COOKIE_SECURE=true` is mandatory when Netrun serves the application through HTTPS; use `false` only for local HTTP development. Confirm that Netrun passes the variables to the source process and mounts the same persistent storage at `/data` for every worker or instance that serves the application; the repository cannot verify those platform behaviors.

For this source-based deployment, mount Netrun's persistent storage at `/data`; the existing `DATABASE_PATH` environment variable already supports that location. If you deploy the Dockerfile instead, it installs `requirements.lock`, starts Uvicorn on `0.0.0.0:8000`, and uses `/app/data` for SQLite. After deployment, verify `/health`, the login page, the same-origin `/static/style.css` response, and a protected page. Do not treat a successful health check as proof that secrets or persistence are configured correctly.

### Temporary CSRF diagnostics

CSRF diagnostics are disabled by default. To investigate a live login failure, add the following temporary runtime value through Netrun Secrets or its environment settings, then restart or redeploy the service:

```text
CONTEXTOPS_CSRF_DIAGNOSTICS=true
```

When enabled, rejected login requests write only fixed reason codes, a validation stage, and non-sensitive boolean fields to the application log. They never log usernames, passwords, CSRF tokens, cookie values, session IDs, request bodies, or authentication secrets. Review the logs, then remove the variable or set it to `false` and restart/redeploy again:

```text
CONTEXTOPS_CSRF_DIAGNOSTICS=false
```

Do not leave temporary diagnostics enabled longer than necessary. The setting does not disable CSRF validation or change authentication behavior.

## Implemented and tested features

The current test suite covers these features:

- Seeded fictional scheduling cases and a server-rendered review queue
- Manual case creation with ISO-8601 and context validation
- Confirmed violations, warnings, and unresolved ambiguity findings
- Human approve, edit, reject, defer, and escalate decisions with rationales
- Preservation of the original proposal when a reviewer edits it
- Audit events for case creation, decisions, rule changes, and evaluations
- Draft, activate, disable, and evaluate quality rules
- Duplicate-rule detection and explicit rule lifecycle changes
- HTTP `400` responses for invalid case and decision requests
- The New Case form's required-field indicators and inline validation feedback
- Single-administrator login, logout, session cookies, protected routes, CSRF checks, and failed-login throttling

Run `make test`, or use the Windows PowerShell command above, to verify the current behavior.

## Future integration work

The repository does not currently implement these capabilities:

- AI-agent proposal generation
- Live calendar or attendee availability checks
- Multi-user authentication, authorization roles, user identity, or collaboration
- Team or tenant isolation
- Case listing, filtering, webhooks, or event subscriptions
- Calendar event creation, notifications, or scheduling execution
- Database migrations, backups, replication, or a production database adapter

Treat these items as follow-up work, not as available ContextOps features.
