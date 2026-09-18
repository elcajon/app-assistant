# Home Assistant App: App Migration Assistant

Moves an installed app to its new repository, together with its configuration
and its internal data, from a panel in the Home Assistant sidebar.

Home Assistant identifies an app by the repository it comes from. The same app
in a new repository is a new app: new slug, empty configuration, empty data
folder. Re-authenticating a tunnel or a service by hand is what this app takes
off your hands.

**Warning**: this app runs with the Supervisor `admin` role, needs Docker
access and only works with protection mode turned off. Install it, migrate,
and uninstall it again.

## Installation

1. Add the app repository to Home Assistant, install "App Migration Assistant"
   and **do not start it yet**.
1. Open the app, switch **Protection mode** off in the info panel.
1. Start the app and open **Migration** in the sidebar.

Protection mode has to be off because the internal data folder of an app can
only be reached from inside the Supervisor container. The Supervisor has no API
for it. Without Docker access the app can copy the configuration of an app, but
not its state, which is the part that saves you the re-authentication.

## Migrating an app

The panel lists every migration plan it knows, with the state of both apps. A
plan that cannot run says why, for example because the old app is too old for
the plan.

Press **Migrate** and choose which of the optional steps should run:

| Step                    | Default | What it does                    |
| ----------------------- | ------- | ------------------------------- |
| Add the repository      | always  | Adds the store repository       |
| Install the new app     | always  | Only if it is not installed yet |
| Back up the new app     | on      | Only if the new app existed     |
| Stop the old app        | always  | Before anything is copied       |
| Back up the old app     | on      | Partial backup of the old app   |
| Copy the configuration  | always  | Options, mapped by the plan     |
| Copy the internal data  | always  | `/data` of the old app          |
| Old app to manual start | always  | Turns off boot and watchdog     |
| Start the new app       | on      | Starts the new app              |
| Uninstall the old app   | off     | Removes the old app             |

The steps run in this order, with a live log. Each step checks the current
state before it acts and reports "already done" instead of failing, so a
migration that stopped half way can be started again and ends in the same
state.

After a migration, check the log of the new app before you uninstall the old
one. Nothing is deleted unless you ask for it.

### Options that the new app does not know

Options are copied through the plan: it can rename options, drop options and
add defaults. An option the new app does not have in its schema is left out
and reported in the log, because the Supervisor rejects options it does not
know.

## Configuration

**Note**: _Restart the app after changing the configuration._

### Option: `log_level`

Sets the log level of the app: `trace`, `debug`, `info` (default), `notice`,
`warning`, `error` or `fatal`.

### Option: `extra_plans`

When on, the app also reads plans from `plans` in its configuration folder
(`/addon_configs/<slug>/plans`), next to the plans it ships. Off by default.

## Writing a migration plan

A plan is a YAML file. Nothing in this app has to change to support another
app:

```yaml
---
version: 1
id: my-app
name: My App
description: Moves My App to its new home.
source:
  slug: 9074a9fa_my_app
  name: My App
  repository: https://github.com/old-owner/repository
  min_version: 7.0.0
  max_version: ""
target:
  slug: 396f0234_my_app
  name: My App
  repository: https://github.com/new-owner/repository
options:
  rename:
    old_option_name: new_option_name
  remove:
    - option_that_is_gone
  defaults:
    new_option: true
notes: >-
  Shown in the dialog before the migration starts.
```

The slug is the full slug, including the repository hash, as it appears in the
URL of the app in Home Assistant. `min_version` and `max_version` are optional
and are checked against the installed version of the old app.

Send a pull request to add your plan to the shipped ones, or drop it in the
`plans` folder of this app's configuration and turn on `extra_plans`.

## Known limitations

- The app needs `/data/addons/data` inside the Supervisor container. If a
  future Supervisor renames that path, the data copy fails with a clear error
  and nothing is changed.
- Only one migration runs at a time.
- Restoring an older version of an app ("store wayback machine") is not part
  of this app.

## Credits

The migration procedure follows the work of [@lmagyar][lmagyar], who wrote and
tested the original migration script, and the discussion about it in the
Unofficial Home Assistant Apps organisation.

[lmagyar]: https://github.com/lmagyar
