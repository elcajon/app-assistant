// Run with Node and playwright installed. Supervisor responses are simulated.
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(
  __dirname,
  "../app-assistant/rootfs/usr/share/app-assistant/www",
);
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const plan = {
    id: "cloudflared",
    name: "Cloudflared",
    description: "Move Cloudflared",
    source: { slug: "9074a9fa_cloudflared" },
    target: { slug: "396f0234_cloudflared" },
    target_exists: true,
    target_version: "2026.9.1",
    changed: ["hostname"],
    removed: [],
    missing: [],
    dropped: [],
    steps: [{ id: "data_copy", label: "Verify and transfer internal data" }],
  };
  const state = {
    csrf: "test",
    environment: { ready: true },
    plans: [plan],
    errors: [],
    jobs: [],
  };
  let offline = false;
  await page.route("http://assistant.test/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/api/state")) {
      if (offline) {
        await route.abort();
        return;
      }
      await route.fulfill({ json: state });
      return;
    }
    if (url.pathname.endsWith("/api/migrate")) {
      assert.equal(route.request().headers()["x-app-csrf"], "test");
      state.jobs = [
        {
          id: "abc",
          plan: "cloudflared",
          state: "awaiting_confirmation",
          completed: ["data_copy"],
          events: [{ time: 1, message: "Copy verified" }],
        },
      ];
      await route.fulfill({ json: { job: state.jobs[0] } });
      return;
    }
    if (url.pathname.endsWith("/api/finish")) {
      assert.equal(route.request().postDataJSON().remove, false);
      state.jobs[0].state = "complete";
      await route.fulfill({ json: { job: state.jobs[0] } });
      return;
    }
    const name = url.pathname.includes("/static/")
      ? url.pathname.split("/static/")[1]
      : "index.html";
    await route.fulfill({
      body: fs.readFileSync(path.join(root, name)),
      contentType: name.endsWith(".js")
        ? "text/javascript"
        : name.endsWith(".css")
          ? "text/css"
          : "text/html",
    });
  });
  await page.goto("http://assistant.test/api/hassio_ingress/session/");
  await page.getByRole("button", { name: "Review migration" }).click();
  await page
    .getByText("Existing target data will be replaced", { exact: false })
    .waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "/tmp/app-assistant-preview.png",
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
    false,
  );
  await page.getByRole("button", { name: "Confirm and migrate" }).click();
  await page.getByRole("button", { name: "It works — keep old app" }).waitFor();
  await page.getByRole("button", { name: "It works — keep old app" }).click();
  await page.getByRole("heading", { name: "Cloudflared: complete" }).waitFor();
  await page.reload();
  await page
    .getByRole("button", { name: "View migration: complete" })
    .waitFor();
  offline = true;
  await page
    .getByText("Reconnecting automatically", { exact: false })
    .waitFor();
  offline = false;
  await page.waitForFunction(
    () => document.getElementById("errors").textContent === "",
  );
  assert.deepEqual(errors, []);
  await browser.close();
  console.log(
    "UI smoke passed: ingress prefix, preview, confirmation, reload, mobile, reconnect",
  );
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
