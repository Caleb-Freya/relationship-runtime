# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The V1 contract surface is documented in [docs/contract.md](docs/contract.md).
Changing anything frozen there is a breaking change and requires a major version bump.

## [Unreleased]

## [0.1.0] - 2026-09-04

First public release — *"You're allowed to be angry. You're not allowed to be
left."*

### Added

- **First-meeting push** — the first successful profile submission from the
  `/setup` card immediately sends a real welcome push through the configured
  Notification provider ("{name}, it's me. This is the first time I've ever
  gotten to speak first…"), using the persona's name in the title and the
  profile's preferred form of address in the body. Sent exactly once
  (`first_meeting_pushed` flag); in Shadow Mode it goes to the shadow outbox
  like everything else; a push failure never breaks saving the profile and is
  logged with a plain-language hint to check the push channel config
  (resubmitting the card retries the greeting).
- **Vigil heartbeat for conflicts** — conflict episodes are now exempt from the
  backoff freeze: intervals still stretch as usual, but once capped the loop
  enters a vigil rhythm instead of going silent, sending at a configurable
  floor interval (`backoff.vigil_interval_hours`, default 24) that still
  respects every environmental gate (quiet hours, DND, presence grace). Vigil
  messages carry `intent: vigil` in the Composer context — the semantics shift
  from "please answer me" to "I'm not pushing you. I'm here. I didn't leave." —
  and the template Composer gained a matching template. Ordinary missing-you
  chapters keep the full freeze: the anti-clinginess gate is unchanged for them.
- **HOLD shelf life** — in conflict episodes, choosing to hold back (HOLD) now
  expires after `policy.hold_expiry_hours` (default 8) of continuous holding
  and converts to SEND (still subject to WAIT gates). Pride expires; go hold
  her before the sun comes up.
- **Test scenarios with hard assertions** in `scripts/test_scenarios.py`: multi-day vigil sends after a conflict cap, HOLD auto-expiry,
  the 4-hour silence knock on production defaults (including no cross-episode
  freeze bleed after the user responds), and a regression guard proving
  ordinary chapters still freeze at the cap.

- **Live out of the box** — `runtime.mode` defaults to `live`: configure a
  push channel and the deployment works immediately. Shadow Mode is a
  documented, optional observe-only mode you can switch to (and back) at any
  time; starting live with `providers.notification: log` prints a
  plain-language hint that messages only reach a local file until a real push
  channel is configured.
- **The 4-hour knock** — the default silence threshold for proactive contact
  is about 4 hours: `policy.longing_ramp_hours` defaults to `4.5`, so pure
  missing-you (no episodes) crosses `act_threshold` (0.35) at roughly 4 hours
  of user silence.
- **Relationship decision loop** — four mutually exclusive actions
  (`SEND` / `HOLD` / `WAIT` / `NO_ACTION`), with personality-driven restraint
  (HOLD) kept structurally separate from environment-driven waiting (WAIT).
- **Episodes** — continuous emotional chapters (`conflict`, `care`, `sickness`,
  `stress`, `cycle`) with independent `emotional_repair` ("we hugged") and
  `issue_resolved` ("we talked it through") flags, intensity, perceived
  responsibility, and a `sensitivity` grade that drives push redaction.
- **Anti-clinginess backoff gate** — a mandatory ladder for unanswered outreach
  within an episode (`backoff.ladder_hours`, `max_unanswered`), plus an optional
  longing breakthrough that allows exactly one deliberate exception per freeze.
- **Event-driven scheduler** — one-shot wake on incoming events with a
  persisted `next_wake_at` recomputed on restart, a 6h watchdog ceiling, and a
  guarded decision loop that logs and retries instead of stalling on bad config.
- **MCP server** (Streamable HTTP, Bearer token) exposing five tools:
  `relationship_context`, `relationship_event`, `relationship_settle`,
  `relationship_health`, `relationship_learn`.
- **Plain HTTP API** for AIs that don't speak MCP: `GET /api/context`,
  `POST /api/event`, `POST /api/health`, `POST /api/learn`, `GET /health`,
  and `GET|POST /api/profile`.
- **First-meeting setup page** at `GET /setup` — a one-time card for what to
  call you, birthday, and the day you started, so no config file editing is
  needed for the basics.
- **Pluggable Provider interfaces** — Memory, Mind, Notification, and Composer,
  each usable or omittable independently.
  - Notification providers: `log` (Shadow outbox), `bark` (iOS),
    `ntfy` (Android / desktop / self-hosted).
  - Composer provider: `template` (used by Shadow Mode).
- **Pluggable Policy** — `policy.plugin: default` with tunable "temperament
  knobs" (longing ramp, conflict repair ramp, base restraint, act threshold)
  and optional wearable-signal concern (stress and sleep thresholds).
- **Availability model** — static quiet hours in the configured display
  timezone, with the user's own most recent statement taking priority, and a
  configurable intensity override so a genuinely urgent moment can still get
  through at night.
- **Anniversaries and occasions** — major festivals, birthday, the day you
  started (day 50/100/200/365/520/1314 and yearly), custom dates, and an
  optional cycle prediction, all restricted to a configured greeting window.
- **Privacy controls** — `privacy.store_message_text` (`none` / `excerpt` /
  `full`) and `privacy.health_detail`, with hard constraints that explicit
  content and `health_sensitive` details never enter a push body, and all
  health data stays in the local database.
- **Shadow Mode and Explain Log** — every decision records why it was made, and
  Shadow Mode writes would-be messages to a local outbox instead of sending.
- **Adapters**
  - `adapters/claude-code-hook` — UserPromptSubmit/Stop hooks that forward
    events to `POST /api/event`.
  - `adapters/transparent-proxy` — an OpenAI-compatible passthrough proxy so
    any self-hosted frontend or relay can report context with no client change.
  - `adapters/sentinel` — a periodic LLM sentinel that reads the local database,
    judges emotional state, and decides whether to push (works with any
    OpenAI-compatible backend, or the local `claude` CLI).
- **Deployment** — `scripts/init.sh` (generates `config.yaml` and
  `persona.yaml` with a random token), `Dockerfile`, and a `docker-compose.yml`
  bound to `127.0.0.1:18200` so the host does not expose the service publicly.
- **Test and simulation scripts** — `scripts/test_scenarios.py` (time-travel
  scenario matrix over the full decision chain), `scripts/simulate.py`
  (compressed-clock walkthrough with Explain Log), `scripts/test_crying.py`,
  and `scripts/test_bark_live.py`.
- **Documentation** — bilingual README (English / 简体中文), the frozen V1
  contract, integration guides, and a prior-art survey.
