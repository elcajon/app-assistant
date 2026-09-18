// Frontend of the App Migration Assistant. It draws the state the backend
// reports and follows a running migration through a server sent event stream.

const base = location.pathname.endsWith("/")
  ? location.pathname
  : `${location.pathname}/`;

const dialog = document.getElementById("migration");
const dialogTitle = document.getElementById("dialog-title");
const dialogSummary = document.getElementById("dialog-summary");
const dialogNotes = document.getElementById("dialog-notes");
const dialogChoices = document.getElementById("dialog-choices");
const dialogSteps = document.getElementById("dialog-steps");
const dialogLog = document.getElementById("dialog-log");
const dialogStart = document.getElementById("dialog-start");
const dialogCancel = document.getElementById("dialog-cancel");

let state = null;
let stream = null;

const api = async (path, options) => {
  const response = await fetch(base + path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || `Request failed with ${response.status}`);
  }
  return body;
};

const element = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const banner = (level, title, text) => {
  const node = element("div", `banner ${level}`);
  node.append(element("h3", null, title));
  if (text) node.append(element("p", null, text));
  return node;
};

// -- rendering --------------------------------------------------------------

const renderEnvironment = () => {
  const container = document.getElementById("environment");
  container.replaceChildren();
  const environment = state.environment;

  if (!environment.supervisor) {
    container.append(
      banner(
        "error",
        "The Supervisor is not reachable",
        "Restart this app and check its log.",
      ),
    );
    return;
  }

  if (environment.protected) {
    container.append(
      banner(
        "warn",
        "Protection mode is enabled",
        "The internal data of an app can only be copied with Docker access. " +
          "Open the info panel of this app, switch protection mode off, " +
          "restart the app and reload this page.",
      ),
    );
  } else if (!environment.docker) {
    container.append(
      banner(
        "error",
        "Docker is not reachable",
        "Protection mode is off, but the Docker socket does not answer. " +
          "Restart this app.",
      ),
    );
  }
};

const renderErrors = () => {
  const container = document.getElementById("errors");
  container.replaceChildren();
  if (!state.errors.length) return;

  const node = banner("warn", "Some migration plans could not be read");
  const list = element("ul");
  for (const error of state.errors) list.append(element("li", null, error));
  node.append(list);
  container.append(node);
};

const side = (label, endpoint, status) => {
  const node = element("div", "side");
  node.append(element("span", null, label));
  node.append(element("strong", null, endpoint.name || endpoint.slug));
  node.append(element("code", null, endpoint.slug));
  if (status.installed) {
    node.append(
      element(
        "span",
        "tag ok",
        `installed ${status.version} · ${status.state}`,
      ),
    );
  } else {
    node.append(element("span", "tag", "not installed"));
  }
  return node;
};

const renderPlans = () => {
  const container = document.getElementById("plans");
  container.replaceChildren();

  if (!state.plans.length) {
    container.append(
      banner(
        "warn",
        "There are no migration plans",
        "Turn on 'extra_plans' in the app configuration to load your own " +
          "plans from /config/plans.",
      ),
    );
    return;
  }

  for (const plan of state.plans) {
    const card = element("article", "plan");
    const head = element("header");
    head.append(element("h3", null, plan.name));
    head.append(element("span", "tag", plan.origin));
    card.append(head);

    if (plan.description) {
      card.append(element("p", "description", plan.description));
    }

    const transfer = element("div", "transfer");
    transfer.append(side("from", plan.source, plan.source_state));
    transfer.append(element("div", "arrow", "→"));
    transfer.append(side("to", plan.target, plan.target_state));
    card.append(transfer);

    if (plan.blocked) {
      card.append(banner("warn", "This plan cannot run", plan.blocked));
    }

    const button = element("button", "primary", "Migrate");
    button.disabled = Boolean(plan.blocked) || !state.environment.ready;
    button.addEventListener("click", () => openDialog(plan));
    card.append(button);

    container.append(card);
  }
};

// -- the migration dialog ---------------------------------------------------

const openDialog = (plan) => {
  dialogTitle.textContent = `Migrate ${plan.name}`;
  dialogSummary.textContent = `${plan.source.slug} → ${plan.target.slug}`;
  dialogNotes.textContent = plan.notes || "";

  dialogChoices.replaceChildren(element("legend", null, "Steps"));
  for (const step of state.steps) {
    const label = element("label");
    const input = element("input");
    input.type = "checkbox";
    input.name = step.id;
    input.checked = !(step.id in state.optional) || state.optional[step.id];
    input.disabled = !(step.id in state.optional);
    label.append(input, ` ${step.label}`);
    dialogChoices.append(label);
  }

  dialogSteps.hidden = true;
  dialogLog.hidden = true;
  dialogLog.textContent = "";
  dialogChoices.hidden = false;
  dialogStart.hidden = false;
  dialogStart.disabled = false;
  dialogStart.textContent = "Start migration";
  dialogCancel.textContent = "Cancel";
  dialogStart.onclick = () => startMigration(plan);

  dialog.showModal();
};

const showProgress = (plan) => {
  dialogChoices.hidden = true;
  dialogStart.hidden = true;
  dialogCancel.textContent = "Close";
  dialogSteps.hidden = false;
  dialogLog.hidden = false;

  dialogSteps.replaceChildren();
  for (const step of state.steps) {
    const item = element("li");
    item.dataset.status = "pending";
    item.dataset.step = step.id;
    item.append(element("span", "icon", "·"));
    item.append(element("span", "label", step.label));
    item.append(element("span", "detail", ""));
    dialogSteps.append(item);
  }

  if (plan) {
    dialogTitle.textContent = `Migrating ${plan.name}`;
    dialogSummary.textContent = `${plan.source.slug} → ${plan.target.slug}`;
    dialogNotes.textContent = "";
  }
  if (!dialog.open) dialog.showModal();
};

const ICONS = {
  running: "…",
  done: "✓",
  skipped: "–",
  failed: "✕",
};

const applyEvent = (event) => {
  if (event.type === "step") {
    const item = dialogSteps.querySelector(`[data-step="${event.step}"]`);
    if (item) {
      item.dataset.status = event.status;
      item.querySelector(".icon").textContent = ICONS[event.status] || "·";
      item.querySelector(".detail").textContent = event.message
        ? `— ${event.message}`
        : "";
    }
  } else if (event.type === "log") {
    const prefix = event.level === "info" ? "" : `${event.level}: `;
    dialogLog.textContent += `${prefix}${event.message}\n`;
    dialogLog.scrollTop = dialogLog.scrollHeight;
  } else if (event.type === "state" && event.state !== "running") {
    dialogCancel.textContent = "Close";
    refresh();
  }
};

const follow = (from) => {
  if (stream) stream.close();
  stream = new EventSource(`${base}api/events?from=${from}`);
  stream.onmessage = (message) => applyEvent(JSON.parse(message.data));
  stream.onerror = () => {
    stream.close();
    stream = null;
  };
};

const startMigration = async (plan) => {
  const choices = {};
  for (const input of dialogChoices.querySelectorAll("input")) {
    if (!input.disabled) choices[input.name] = input.checked;
  }

  dialogStart.disabled = true;
  try {
    await api("api/migrate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan: plan.id, choices }),
    });
  } catch (error) {
    dialogStart.disabled = false;
    dialogLog.hidden = false;
    dialogLog.textContent = `error: ${error.message}\n`;
    return;
  }

  showProgress(plan);
  follow(0);
};

dialog.addEventListener("close", () => {
  if (stream) {
    stream.close();
    stream = null;
  }
  refresh();
});

// -- start up ---------------------------------------------------------------

const refresh = async () => {
  state = await api("api/state");
  renderEnvironment();
  renderErrors();
  renderPlans();
  return state;
};

const boot = async () => {
  await refresh();
  const job = state.job;
  if (job && job.state === "running") {
    const plan = state.plans.find((item) => item.id === job.plan);
    showProgress(plan);
    for (const event of job.events) applyEvent(event);
    follow(job.events.length);
  }
};

boot().catch((error) => {
  document
    .getElementById("errors")
    .append(banner("error", "The app is not answering", error.message));
});
