# Home Assistant App: App Assistant

## Before migration

This experimental app has Supervisor admin and Docker access. Use a disposable
installation for evaluation and keep a full system backup. Its automatic
backups cover the selected apps, not the entire installation.

Install from the edge repository, disable protection mode and open the web UI.
Source and target must have different full app slugs. Repository URLs must use
HTTPS without credentials. The technical slug is `app-assistant`.

The preview shows changed, removed, unknown and missing option names; it never
shows their values. Unknown options block migration until the plan explicitly
removes or transforms them. Supervisor validates the final options against the
installed target after installation/update and before the source is stopped.
When target metadata is unavailable, complete validation is deferred until
installation. No data is copied before validation succeeds.

## Migration and confirmation

1. Record the original versions and startup settings in a durable journal.
1. Add the target repository and install the target if absent.
1. Disable target boot, watchdog and automatic updates, then stop it.
1. Create and verify a target backup, including the expected app identity.
1. Update the target if an update is available; stop it again and check its
   version against the plan bounds.
1. Validate transformed options.
1. Disable source automatic starts and stop it; verify its backup.
1. Copy options; stage the internal data in the Supervisor data directory.
1. Compare file hashes, names, ownership, modes and symlink targets. Retain the
   original target directory, then move the verified stage into place.
1. Start the target. Leave the source stopped and wait for user confirmation.

A successful start proves only that the container is running. Verify tunnel
connectivity or the app's actual function, then confirm in the UI. Removing the
source is a separate, explicit choice in that final confirmation.

After confirmation the target inherits automatic boot when the source used it
and the target permits it. Watchdog and automatic updates remain disabled;
review and enable them yourself after testing. The source remains disabled if
kept. Retained target data and backups are not automatically deleted.

Only app options and internal `/data` are transferred. External mapped folders,
port mappings and other app-specific settings are not automatically migrated.
Special files such as sockets and devices in `/data` block the transfer.
Extended attributes, ACLs and hardlink relationships are not verified; apps
that depend on them require additional support before migration.

## Resume and recovery

The journal is stored under `/data/migrations/<plan-id>.json` with restricted
permissions and atomic writes. It contains the plan fingerprint, original
startup settings, checkpoint history and backup identifiers, not option values.

Browser reconnects reload this journal automatically. Restarting the app marks
an unfinished run as interrupted. Resume uses the same job and skips completed
checkpoints; it refuses a changed plan or a completed migration. A completed
migration cannot be repeated from the UI.

A partially staged copy can be rebuilt without touching the existing target.
If interruption happens between directory renames, the transfer receipt allows
completion of the same switch. If the target changed after the switch, the
assistant refuses to overwrite it.

An interrupted install, update or repository request may have an unknown
outcome. Such runs stop for manual inspection. The assistant does not guess
whether to repeat the operation. Backup requests with an unknown receipt are
also blocked. Backup polling times out after 30 minutes and resumes the same
job rather than silently starting another one.

For manual recovery:

- Keep both apps stopped; inspect Supervisor logs and the recorded backup IDs.
- Restore the affected app from its verified backup using Home Assistant.
- Review original boot/watchdog/update settings in the journal.
- Retained target data is under the Supervisor data root as
  `.assistant-<job-id>-previous`; do not remove it until recovery is complete.
- Do not delete a journal to bypass an uncertainty check. Resolve and document
  the actual app and data state first. There is no automatic rollback button.

## Configuration

`log_level` controls startup logging. `extra_plans` enables YAML plans from
`/config/plans` in this app's configuration folder. Restart after changing app
configuration. Local plan authors are trusted administrators.

## Plan format

```yaml
---
version: 1
id: example
name: Example
source:
  slug: 12345678_example
  min_version: 1.0.0
target:
  slug: abcdef12_example
  repository: https://github.com/example/repository
  min_version: 2.0.0
  max_version: 2.9.9
options:
  rename:
    old_name: new_name
  remove:
    - obsolete
  defaults:
    enabled: true
  jq: ".port |= tonumber"
notes: Check connectivity before removing the source.
```

Transformation order: remove/rename, defaults, then optional `jq`. The filter
receives only the options object on stdin. It runs without a shell, without the
Supervisor token, with a five-second timeout, output and CPU limits, and a Linux
memory limit. Exactly one JSON object must be returned. Module loading from the
working directory is disabled. Invalid output blocks the migration.

Version bounds currently accept numeric stable versions (up to four parts,
with optional `v` prefix). Bounded prereleases and hash versions are rejected.

Remote plan discovery and the store wayback machine remain future work. Plans
are shipped with this app or installed locally; no repository scripts execute.

## API and verification

The Supervisor adapter is our own Python standard-library client in
`supervisor.py`, not bashio or an external SDK. Absence is determined from a
successful app/store listing; connection and authorization failures are errors.
Backup completion requires a successful job and matching backup contents.

The API implementation follows the
[Supervisor endpoint documentation](https://developers.home-assistant.io/docs/api/supervisor/endpoints/).
Runtime compatibility still needs the live checks in `docs/LIVE_TESTS.md` in
the source repository. Passing lint and image builds does not prove migration
correctness.
