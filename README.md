# Home Assistant App: App Migration Assistant

[![GitHub Release][releases-shield]][releases]
![Project Stage][project-stage-shield]
[![License][license-shield]](LICENSE.md)

![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

[![Github Actions][github-actions-shield]][github-actions]
![Project Maintenance][maintenance-shield]
[![GitHub Activity][commits-shield]][commits]

Move an installed app to its new repository, keeping its configuration and its
internal data.

## About

Home Assistant identifies an app by the repository it comes from. When an app
moves to a new repository, the new one is a different app: new slug, empty
configuration, empty data folder. For an app like Cloudflared or Tailscale that
means setting up the tunnel again, by hand, on every installation out there.

This app does the move from a panel in the sidebar:

| Step                              |                                              |
| --------------------------------- | -------------------------------------------- |
| Add the repository of the new app  | always                                       |
| Install the new app                | always                                       |
| Back up both apps                  | on by default                                |
| Copy the configuration             | mapped by the plan, unknown options reported |
| Copy the internal data folder      | always                                       |
| Set the old app to manual start     | always                                       |
| Start the new app                  | on by default                                |
| Uninstall the old app              | off by default                               |

Every step checks the current state first, so a migration that failed half way
can be started again and ends in the same state. Nothing is deleted unless you
ask for it.

Which apps can be migrated is described by small YAML plans in
[`migration-assistant/plans`](migration-assistant/plans). Supporting another
app means adding a plan, not changing code.

**Warning**: this app needs the Supervisor `admin` role and Docker access, and
only works with protection mode turned off. Install it, migrate, uninstall it.

[:books: Read the full app documentation][docs]

## Installation

Add this app repository to Home Assistant, install
"App Migration Assistant", switch protection mode off in its info panel, then
start it and open **Migration** in the sidebar.

## Credits

The migration procedure follows the work of [@lmagyar][lmagyar], who wrote and
tested the original migration script, and the discussion about it in the
[Unofficial Home Assistant Apps][org] organisation.

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[commits-shield]: https://img.shields.io/github/commit-activity/y/elcajon/app-migration-assistant.svg
[commits]: https://github.com/elcajon/app-migration-assistant/commits/main
[docs]: https://github.com/elcajon/app-migration-assistant/blob/main/migration-assistant/DOCS.md
[github-actions-shield]: https://github.com/elcajon/app-migration-assistant/workflows/CI/badge.svg
[github-actions]: https://github.com/elcajon/app-migration-assistant/actions
[license-shield]: https://img.shields.io/github/license/elcajon/app-migration-assistant.svg
[lmagyar]: https://github.com/lmagyar
[maintenance-shield]: https://img.shields.io/maintenance/yes/2026.svg
[org]: https://github.com/homeassistant-apps
[project-stage-shield]: https://img.shields.io/badge/project%20stage-experimental-yellow.svg
[releases-shield]: https://img.shields.io/github/release/elcajon/app-migration-assistant.svg
[releases]: https://github.com/elcajon/app-migration-assistant/releases
