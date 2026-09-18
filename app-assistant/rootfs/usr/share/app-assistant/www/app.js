// Polling reads the durable journal, including after a browser or app restart.
const page = new URL(location.href);
page.search = "";
page.hash = "";
if (page.pathname.endsWith("/index.html"))
  page.pathname = page.pathname.slice(0, -10);
if (!page.pathname.endsWith("/")) page.pathname += "/";
const base = page;
const dialog = document.getElementById("migration");
let state;
let selected;
let busy = false;

const node = (tag, text, className) => {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
};
const api = async (route, payload) => {
  const response = await fetch(
    new URL(route, base),
    payload
      ? {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-App-CSRF": state.csrf,
          },
          body: JSON.stringify(payload),
        }
      : {},
  );
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
};
const button = (text, action, disabled = false) => {
  const item = node("button", text, "primary");
  item.type = "button";
  item.disabled = disabled;
  item.onclick = action;
  return item;
};
const jobFor = (id) => state.jobs.find((job) => job.plan === id);
const report = (text) => {
  document
    .getElementById("errors")
    .replaceChildren(node("p", text, "banner error"));
};

async function act(route, payload) {
  if (busy) return;
  busy = true;
  try {
    await api(route, payload);
    await refresh();
  } catch (error) {
    document.getElementById("dialog-notes").textContent = error.message;
  } finally {
    busy = false;
  }
}

function renderDialog() {
  const plan = state.plans.find((item) => item.id === selected);
  if (!plan) return;
  const job = jobFor(plan.id);
  const title = document.getElementById("dialog-title");
  const summary = document.getElementById("dialog-summary");
  const notes = document.getElementById("dialog-notes");
  const choices = document.getElementById("dialog-choices");
  const steps = document.getElementById("dialog-steps");
  const log = document.getElementById("dialog-log");
  const actions = document.getElementById("dialog-actions");
  title.textContent = job
    ? `${plan.name}: ${job.state.replaceAll("_", " ")}`
    : `Review ${plan.name}`;
  summary.textContent = `${plan.source.slug} → ${plan.target.slug}`;
  notes.textContent = job?.error || plan.notes || "";
  choices.replaceChildren();
  steps.replaceChildren();
  actions.replaceChildren();
  log.hidden = !job;
  steps.hidden = !job;
  choices.hidden = Boolean(job);

  if (!job) {
    choices.append(
      node("legend", "Preview — option values are never displayed"),
    );
    const lines = [
      `Target version: ${plan.target_version || "available after repository installation"}`,
      plan.target_exists
        ? "Existing target data will be replaced; a verified backup and original data copy are retained."
        : "The target will be installed.",
      `Changed/added options: ${(plan.changed || []).join(", ") || "none"}`,
      `Removed options: ${(plan.removed || []).join(", ") || "none"}`,
      `Unknown options: ${(plan.dropped || []).join(", ") || "none"}`,
      `Missing required options: ${(plan.missing || []).join(", ") || "none detected"}`,
      "Both apps will be stopped. Backups are mandatory. Only internal /data and app options are copied; external folders are not migrated.",
    ];
    lines.forEach((text) => choices.append(node("p", text)));
    const upcoming = node("ol");
    for (const step of plan.steps || []) {
      if (step.id === "target_install" && plan.target_exists) continue;
      if (step.id === "target_update" && !plan.update_available) continue;
      upcoming.append(node("li", step.label));
    }
    choices.append(upcoming);
    actions.append(
      button(
        "Confirm and migrate",
        () => act("api/migrate", { plan: plan.id, confirmed: true }),
        busy ||
          !state.environment.ready ||
          Boolean(plan.blocked) ||
          Boolean(plan.dropped?.length) ||
          Boolean(plan.missing?.length),
      ),
    );
  } else {
    for (const step of plan.steps || []) {
      const status = job.completed.includes(step.id)
        ? "done"
        : job.current === step.id
          ? job.state === "failed"
            ? "failed"
            : "running"
          : "pending";
      const row = node(
        "li",
        `${status === "done" ? "✓" : status === "running" ? "…" : "·"} ${step.label}`,
      );
      row.dataset.status = status;
      steps.append(row);
    }
    log.textContent = job.events
      .map(
        (event) =>
          `${new Date(event.time * 1000).toLocaleTimeString()} ${event.message}`,
      )
      .join("\n");
    if (["failed", "interrupted"].includes(job.state)) {
      actions.append(
        button(
          "Resume existing migration",
          () =>
            act("api/migrate", {
              plan: plan.id,
              resume: true,
              confirmed: true,
            }),
          busy || Boolean(job.uncertain),
        ),
      );
      if (job.uncertain)
        notes.textContent +=
          " The previous request has an unknown outcome. Manual recovery is required; do not delete the journal.";
    }
    if (job.state === "awaiting_confirmation") {
      notes.textContent =
        "The target has started. Check its functionality and logs before confirming. A started container alone does not prove a working tunnel.";
      const link = node("a", "Open target app and logs");
      link.href = `https://my.home-assistant.io/redirect/supervisor_addon/?addon=${encodeURIComponent(plan.target.slug)}`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      actions.append(link);
      actions.append(
        button(
          "It works — keep old app",
          () =>
            act("api/finish", {
              plan: plan.id,
              confirmed: true,
              remove: false,
            }),
          busy,
        ),
      );
      actions.append(
        button(
          "It works — remove old app",
          () => {
            if (
              window.confirm(
                "Remove the old app? Only continue after verifying the new app works.",
              )
            ) {
              act("api/finish", {
                plan: plan.id,
                confirmed: true,
                remove: true,
              });
            }
          },
          busy,
        ),
      );
    }
  }
}

async function refresh() {
  state = await api("api/state");
  document
    .getElementById("errors")
    .replaceChildren(
      ...state.errors.map((text) => node("p", text, "banner warn")),
    );
  document
    .getElementById("environment")
    .replaceChildren(
      ...(state.environment.ready
        ? []
        : [
            node(
              "p",
              "Docker access is unavailable. Disable protection mode in this app's info panel and restart it.",
              "banner warn",
            ),
          ]),
    );
  const list = document.getElementById("plans");
  list.replaceChildren();
  for (const plan of state.plans) {
    const card = node("article", undefined, "plan");
    const job = jobFor(plan.id);
    card.append(node("h2", plan.name), node("p", plan.description));
    card.append(node("p", `${plan.source.slug} → ${plan.target.slug}`));
    if (plan.blocked && !job)
      card.append(node("p", plan.blocked, "banner warn"));
    card.append(
      button(
        job
          ? `View migration: ${job.state.replaceAll("_", " ")}`
          : "Review migration",
        () => {
          selected = plan.id;
          renderDialog();
          dialog.showModal();
        },
        !job && (Boolean(plan.blocked) || !state.environment.ready),
      ),
    );
    list.append(card);
  }
  if (dialog.open) renderDialog();
}
async function poll() {
  try {
    await refresh();
  } catch (error) {
    report(
      `${error.message}. Reconnecting automatically; an existing migration may still be running.`,
    );
  }
  setTimeout(poll, 3000);
}
poll();
