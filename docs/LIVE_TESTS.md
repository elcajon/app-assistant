# Live migration verification

Status: not yet executed for this implementation. Unit tests use a simulated
Supervisor and real temporary filesystem transfers; they do not establish
Supervisor, Docker or HAOS compatibility.

Use a disposable Home Assistant installation with a full system backup.
The devcontainer's **Start Home Assistant** task launches Supervisor/Core.
The post-start script only prepares networking and does not start Core.

- [ ] Install locally; verify ingress opens and direct requests are rejected.
- [ ] Confirm installed-app/store listing and target schema shapes.
- [ ] Check backup job completion, reference, child errors and backup contents.
- [ ] Migrate to an absent target; verify data, options and startup settings.
- [ ] Migrate to a running existing target; prove it stops before copying.
- [ ] Update an outdated target only after its backup completes.
- [ ] Interrupt staging; verify the original target remains intact.
- [ ] Interrupt between directory renames; resume the same transfer.
- [ ] Restart the assistant after copying; verify it does not recopy data.
- [ ] Disconnect the browser and reconnect to the durable progress display.
- [ ] Fail backup/update/start; verify no source uninstall is performed.
- [ ] Confirm manually with both keep-source and remove-source choices.
- [ ] Inspect remaining target data, backup IDs and recovery instructions.
- [ ] Verify a real Cloudflared tunnel keeps its identity and works externally.
- [ ] Repeat on HAOS to cover host security and storage behavior.
- [ ] Test both amd64 and aarch64, recording which platform actually ran.

Record Supervisor/Core versions, architecture, source/target versions and the
plan fingerprint with every result. Redact credentials before sharing logs.

## Automated UI smoke test

`tests/ui-smoke.cjs` uses Playwright with simulated HTTP responses. CI installs
Playwright 1.62.1 and Chromium and runs it alongside the Python test job. It
checks the ingress URL prefix, preview, explicit confirmation, page reload,
mobile overflow and reconnection. This is not a live Supervisor test.
