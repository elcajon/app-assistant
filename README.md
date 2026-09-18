# Home Assistant App: App Assistant

Move an app to another repository while preserving its options and internal
state. The repository, installation slug and image namespace use `app-assistant`.

**Experimental:** automated regression tests cover simulated Supervisor calls
and actual filesystem transfers. A complete migration on a real Supervisor and
Home Assistant OS still needs verification before production use.

## Installation

Add [the edge repository](https://github.com/elcajon/repository-edge), install
App Assistant, disable protection mode and open its web interface.
Changes in this checkout become available there only after publication.

## Workflow

1. Review the source, target version and option changes. Values stay hidden.
1. Confirm migration. The assistant stops automatic starts, verifies backups,
   updates an existing target if needed and validates transformed options.
1. Both apps remain stopped during the data copy. The assistant stages and
   verifies the data before replacing the target directory. Original target
   data is retained separately.
1. Check the running target and its logs. Confirm success separately, choosing
   whether to keep or remove the old app.

A durable journal prevents completed transfers from running again. Interrupted
runs can resume completed checkpoints. Unknown mutation outcomes require
manual recovery rather than automatically repeating a potentially unsafe call.

[Read the documentation](app-assistant/DOCS.md) for plans, limitations,
recovery and development. Only explicitly installed local plans are supported;
there is no automatic downloading or execution of external plans.

## Development

The devcontainer includes a **Start Home Assistant** task. Bootstrap prepares
the workspace; the task runs `supervisor_run` to start Supervisor and Core.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Install `jq` locally to run transformation tests. CI runs the same regression
suite plus Python lint and formatting checks. See
[the live test checklist](docs/LIVE_TESTS.md) for checks that mocks cannot prove.

## Credits

The migration procedure was inspired by
[@lmagyar](https://github.com/lmagyar). The initial prototype was created with
Claude; subsequent hardening must be reviewed and tested like any other code.
