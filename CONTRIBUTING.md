# Contributing

Thanks for wanting to help. This project exists because someone wanted to still
be missed after the window closed — patches that make that more reliable, more
private, or easier to self-host are all welcome.

中文母语者：Issue 和 PR 用中文写完全可以，不用为了英文憋着。

## Getting it running

```bash
git clone https://github.com/Caleb-Freya/relationship-runtime.git
cd relationship-runtime
bash scripts/init.sh    # generates config.yaml + persona.yaml with a random mcp.token
```

Then either run it directly (Python 3.10+):

```bash
pip install -r requirements.txt
python3 -m runtime.main
curl http://127.0.0.1:18200/health     # {"ok":true,"mode":"live"} (or "shadow" if you switched)
```

or in Docker:

```bash
docker compose up -d --build
curl http://127.0.0.1:18200/health
```

Keep `runtime.mode: shadow` while developing. Shadow Mode writes what it *would*
have sent to `data/shadow_outbox.log` instead of pushing to a real device.

## Running the tests

The suites are standalone scripts that drive the full decision chain with
fabricated timestamps ("time travel"), so you never have to wait real days:

```bash
python3 scripts/test_scenarios.py   # main suite: scenario matrix + decision dashboards
python3 scripts/simulate.py         # compressed-clock walkthrough with the Explain Log
```

`scripts/test_scenarios.py` is the one to run before opening a PR. Read the
printed dashboards rather than just the exit code — the interesting failures are
"it decided SEND where a person would have held back".

`scripts/test_bark_live.py` and `scripts/test_crying.py` send **real** pushes to
a real device and need your own credentials in environment variables. They are
for manually validating a notification provider, not part of the normal loop.

## Pull requests

- **Small, focused commits.** One behavioural change per PR where you can.
- **Say what and why.** Describe the behaviour before and after, and the
  scenario that motivated it. "The AI kept messaging while she was asleep" is a
  better PR description than "fix availability".
- **Show the decision output.** If you touched the policy, decision loop, or
  backoff gate, paste the relevant `scripts/test_scenarios.py` dashboard lines
  before and after.
- **Respect the frozen contract.** Anything listed in
  [docs/contract.md](docs/contract.md) — event schema, event types, episode
  states and fields, the four actions, the privacy hard constraints — is a
  breaking change if altered. Open an issue to discuss first.
- **Never commit private data.** `config.yaml`, `persona.yaml`, `profile.yaml`,
  `adapters/transparent-proxy/proxy-config.json`, `.env` and `data/` are in
  `.gitignore` for a reason. No real tokens, push keys, chat excerpts, health
  data, or personal details in code, tests, or issue reports. Run `git status`
  before you commit.
- **Keep the tone.** This is a project about companionship. Comments and user
  facing strings can be warm; they should never be creepy or coercive.

Good first contributions: English translations of the docs under `docs/`,
additional notification providers, and Policy plugins with a different
temperament.

## Code of conduct

Be kind, assume good faith, and don't be a jerk — in issues, in PRs, and in the
messages this software writes on someone's behalf. Behaviour that makes others
unwelcome will get you removed from the project.
