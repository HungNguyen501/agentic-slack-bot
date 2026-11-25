# Layered Architecture

How code inside this repo is organised: which layer a piece of logic belongs to and which way imports may point. This is separate from the service boundaries in [architecture.md](architecture.md) (receiver / worker / scheduler are *tiers* — separate processes; layers are about the code inside them). Generic method and review checklist: the `layered-architecture` skill in `.claude/skills/` (hub-managed, do not edit). **This file wins over the skill's defaults** where they differ (folder names, `common` instead of `shared`).

Applies to **all new code now**. Existing modules stay where they are until their feature is migrated (see "Existing code").

## Layers

Three layers, feature-first. Each feature is a top-level package under `src/` with the layers inside it:

```
src/
  <feature>/                  e.g. pr_reminders, access_control, dataset_sync, service_principal_secrets, schedules, bots, agent
    presentation/             agent-tool handlers and event/job handlers: parse input, call one use case, map the result and errors to a reply
    core/                     use cases, ports (Protocols), entities / value objects, pure rules, core errors, and `configs.py`: the feature's own configs class
    infra/                    adapters that implement the ports, on top of connectors/ (Supabase, Redis, Slack, GitHub, Databricks, OpenAI)
  receiver/ worker/ scheduler/   service entry points: transport glue (FastAPI route, RQ task, cron loop) + that service's composition root
  connectors/                 generic external clients; no feature logic, no feature imports (unchanged); each big client has its own configs class at the top of its module
  common/                     what several modules share: env-backed settings (`configs.py`), cron/crypto helpers; no feature rules
tests/                        mirrors src/
```

Create a layer folder only when it holds real code. A feature with no tool handler has no `presentation/`; a pure-rules feature may be only `core/`. A layer whose functions just forward to the next one with the same arguments should not exist.

| Layer | Owns | Must not |
|---|---|---|
| `presentation/` | Parse and validate the *shape* of input (tool arguments, Slack payload, job args), call one use case, turn the result or a core error into the reply text/response | Run queries, call Slack/GitHub/Databricks, decide business rules |
| `core/` | Use cases (one per user-visible action; owns orchestration, permission checks, and the unit of work), entities, value objects, pure rules (limits, status buckets, message building, validation), the **ports** it needs | Import `openai`, `redis`, `psycopg`, `httpx`, `connectors.*`, any `infra/`; read env vars; hold a connection or client |
| `infra/` | Implement the ports: SQL, HTTP calls, Redis keys, Slack/GitHub/Databricks calls, translating storage/client shapes to core types and driver errors to core errors | Make business decisions, branch on business state, call another feature's infra |

For plain CRUD with no rule of its own (`schedules`), a presentation handler may call a core port directly; add a use case only when orchestration, a rule or a permission check appears.

Service entry points (`receiver/app.py`, `worker/tasks.py`, `scheduler/app.py`) are the outermost presentation glue: verify, decode, enqueue or call a feature's presentation handler. They hold no rules.

## Dependency rule

- Imports point inward: `presentation → core ← infra`. `core` imports only the standard library, `common` (shared settings, pure helpers) and the `core` of features it genuinely needs. No cycles.
- A feature uses another feature only through that feature's `core/` (its use cases, models, ports). Never its `infra/` or `presentation/`.
- `connectors/` is imported only by `infra/` and by composition roots. `connectors/` and `common/` import no feature.
- **Ports live in `core/`**, as `typing.Protocol` classes, named for the capability, not the vendor (`ReminderStore`, `PrStatusSource`, `ThreadReader`; not `SupabaseStore`). The adapter in `infra/` implements it.
- **Composition root.** One module per service wires adapters to use cases — `src/<service>/wiring.py` (worker: bot config, history, tool handlers; receiver: only what its documented exceptions need; scheduler: schedule store + enqueue). Nothing else constructs an `infra/` class or a connector client, and use cases never build their own dependencies (no module-level clients, no `connect()` inside `core/`). Agent tool handlers receive their use cases from the worker's wiring, not by importing infra. Wiring modules expose `@functools.cache` factory functions (`bot_repository()`), never import-time instances, so importing a service builds nothing.
- Strict, not relaxed: `core/` never sees a vendor SDK or connector, even for "just one call". Add or extend a port.

## Crossing a boundary

- **Data.** Each side owns its shape. `core/` types are plain frozen dataclasses (`frozen=True` unless something mutates them). `infra/` has its own persistence/payload models (a Slack message dict, a GraphQL response, a table row) and maps them to core types before returning; core never receives a row, a raw API dict, or a driver object. A separate row dataclass is only needed when the row's shape differs from the core type; when it maps one-to-one, the adapter builds the core type straight from the row and the dict never leaves the adapter. Presentation does not return core entities to Slack or the LLM directly — it formats them.
- **Errors.** `core/` defines the errors it raises and handles (`DatasetNotSyncedError`, `ReminderRequestError` style). `infra/` catches only the boundary errors that can actually occur (`httpx.HTTPError`, `psycopg.Error`, Slack API errors) and re-raises a core error where the use case can act on it. Anything unexpected still propagates to RQ ([code-style.md](code-style.md) "Error Handling"). A driver exception type must not appear in a `core/` `except`.
- **Transactions and locks.** The use case decides what is atomic; it never holds a connection. One port method is one transaction (`add_reminder_if_absent`, `replace_snapshot`), or the port declares a context manager for a span (`dataset_lock` style) that the adapter implements with advisory locks. Adapters do not decide on their own to commit across several port calls.
- **Validation.** Shape in `presentation/` (and the receiver's pure view-submission validation, which is allowed there because Slack needs the answer synchronously); business rules in `core/`; permission (admin / channel / reviewer eligibility) in the use case. A tool handler passes the caller's identity (`user_id`, `bot`, `channel`) to the use case; it does not check `admin_users` itself.

## Where does this go?

| You are adding… | It goes in |
|---|---|
| A new agent tool | `<feature>/presentation/tools.py` handler → a use case in `<feature>/core/`. Schema and `dispatch` branch still follow architecture.md "Adding Tools to the Agent" |
| A rule: limits, status bucketing, cron checks, message wording, validation | `<feature>/core/`, pure, no I/O |
| A Supabase table | Row model + queries in `<feature>/infra/` (adapter for a `core/` port). Migration and `CLAUDE.md` Data Models entry as in database.md |
| A call to Slack / GitHub / Databricks / OpenAI for a feature | Generic call in `connectors/` (only if reusable and task-agnostic); the feature-specific use of it in `<feature>/infra/` behind a port |
| A new scheduled job or RQ task | Use case in `<feature>/core/`; thin task in `worker/tasks.py` (job path string stays stable) that calls the wired handler |
| A new env var | `common/configs.py`; passed into `core/` as a parameter or via an adapter, never read inside `core/` |
| A new constant: limit, timeout, retry cap, regex, format marker, message wording, set of tool names | An attribute of the owning module's configs class (`<Feature>Configs` in `<feature>/core/configs.py`; see "Configs"), not a module-level name |
| A table or capability used by several features (`bots`, `schedules`) | Its own feature; others depend on its `core/` |

Feature-specific models, limits and wording that are not env-backed live in that feature's `core/` (the constants in its configs class, see below), not in `common/configs.py`.

## Configs

Every big module (a feature package, a connector, a service) has **its own configs class**. `common/configs.py` is for what several modules share; it never holds one feature's limits or wording.

- **`common/configs.py`** — settings read from the environment (hosts, tokens, model names, switches) and tunables that more than one module really uses. It imports no feature. A value that two features need goes here, or into the `core/` of the one feature that owns the concept and the other uses it through that `core/`.
- **A feature's class** — `<Feature>Configs` in `<feature>/core/configs.py` (`ChannelAnalysisConfigs`, `DatasetSyncConfigs`, `CharactersConfigs`; the unmigrated `agent/` features keep theirs beside the code: `AccessControlConfigs`, `PrRemindersConfigs`). It holds everything that is the feature's own and not env-backed: size limits, timeouts and retry caps, formats it writes or parses (a prefix, a marker), regexes, wording, sets of tool names. Several small classes by concern are fine when one would grow past a screen (`ToolPolicy`, `ReadGuardMessages`), each named for what it holds.
- **A connector's or service's class** — at the top of its own module (`SlackConfigs` in `connectors/slack.py`); nothing outside imports a connector's internals, only the class's attributes where a test needs them.
- **Shape** — a class with `UPPER_CASE` attributes that is never instantiated, no methods, a short comment for each value whose reason is not obvious. Compiled regexes and `frozenset`s are attributes too. A feature module has no other module-level constants: its logger and its private helper functions are the exceptions.
- **No environment in a feature's class.** `core/` may not read env vars, so an env-backed value stays in `common/configs.py` and reaches the feature as a parameter or through an adapter. To make one of a feature's tunables env-configurable, add the env field to `common/configs.py` and pass it in from the wiring; the feature's class keeps the default.
- **Tests** change a value with `monkeypatch.setattr(ChannelAnalysisConfigs, "MAX_RETRY_AFTER_SECONDS", 5)`, never by editing module state.

## Testing per layer

Extends [code-style.md](code-style.md) "Testing" (tests still mirror `src/`):

- `core/`: plain unit tests with hand-written in-memory fakes of the ports (in the folder's `conftest.py`, exposed as fixtures; helpers shared across features go in `tests/support/`, importable as `tests.support.<module>`). No `patch`, no `autospec` of vendor clients — if a core test needs a mock of a connector, a port is missing.
- `infra/`: patch the connector / `connect` where the adapter looks it up, `autospec=True`, assert SQL fragments, params and the core type returned (the database.md rules).
- `presentation/`: stub the use case; assert argument parsing and reply formatting.

## Existing code

The tree is still organised by task under `agent/` and `connectors/db/`, with I/O and rules mixed in the same modules (e.g. `pr_reminders/service.py`, `access_control/`). Do not rewrite it in one pass.

- New features, and new tools, tables and adapters for them, use the layout above immediately. Do not add new modules under `agent/` or `connectors/db/` (generic clients excepted).
- Edits to an existing feature stay in its current layout unless you are migrating that feature. Migrate one whole feature at a time, as its own change with **no behaviour change**, tests moved along with it. Do not mix a migration with a feature change.
- Moves must keep stable: RQ job paths (`worker.tasks.<fn>`), the Redis key patterns, the `access_form_options` / button `value` contract, and migrations.
- **Migrated so far:** `bots/`, `schedules/`, `dataset_sync/`, `characters/` and `channel_analysis/` (written in this layout from the start; the first two are shared foundations, which is why they went first: a `core/` can't import `connectors.db.*`). They are the reference for the pattern: adapters are classes named for the store (`SupabaseScheduleRepository`) that implement the port; `core/` services take their ports in `__init__` (`DatasetSyncService`); tool handlers take the service/port as an argument; `worker/wiring.py` builds everything and hands the tools their dependencies as `agent.dispatch.ToolServices`; tests are `tests/<feature>/{core,infra,presentation}/`, with core tests on the in-memory fakes in that folder's `conftest.py` (exposed as fixtures) and adapter tests on the shared `tests/support/db.py::fake_connect`.
- Legacy features that still need a migrated feature call into its adapter (`pr_reminders` builds a `SupabaseScheduleRepository`, `access_control` a `SupabaseDatasetRepository`) with a comment saying so. Each of those lines goes away when its feature is migrated and gets the dependency from the wiring.
- Indicative targets for the rest (decide the exact split when each feature is migrated):

| Today | Target |
|---|---|
| `agent/pr_reminders/{pr_links,queries,status,message,limits}.py` | `pr_reminders/core/` (already pure) |
| `agent/pr_reminders/service.py` | use cases in `pr_reminders/core/`; Slack/GitHub I/O in `pr_reminders/infra/` behind ports |
| `agent/*/tools.py` | `<feature>/presentation/tools.py` |
| `agent/access_control/`, `agent/forms/`, `agent/pr/` | `access_control/`: core (requests, review rules, `rules_v2` rendering, validation), infra (Supabase stores, Databricks principals, GitHub PR workflow), presentation (modal view, tools) |
| `agent/service_principal_secrets/store.py` | `service_principal_secrets/infra/` |
| `agent/{agent,dispatch,history}.py`, `agent/skills/` | `agent/` (loop in core with LLM / history / skill-source ports), `skills/` |
| `connectors/db/agent_skills.py` | `skills/infra/`; `connectors/db/connection.py` stays |
| `ReminderConfig`, `ReminderMessages` in `common/configs.py` | `pr_reminders/core/` (with its `PrRemindersConfigs`) |
| `AgentConfigs` in `common/configs.py` (the tool schemas, reasoning effort, TTLs of the access flows) | The agent's own class beside `agent/` when it is migrated; the env-backed fields (`GPT_MODEL`) stay in `common/configs.py` |

## Enforcement

By review for now: apply the skill's "Reviewing for violations" checklist to any diff touching these layers (`/code-review` is expected to flag a `core/` import of a connector or SDK, a handler with business conditionals, a use case constructing its own client, an adapter that branches on business state, and a module-level constant in a feature module or a feature's limit or wording added to `common/configs.py`). Planned follow-up, once the first features are migrated: an `import-linter` layers contract encoding the dependency rule above, with today's violations frozen so it blocks only new ones. Until it exists, the dependency rule is checked by hand.
