import { expect, test } from "@playwright/test";

/** The critical workflows, driven through the real UI against the real backend. */

test.beforeEach(async ({ request }) => {
  await request.post("/api/simulation/reset");
  await request.post("/api/simulation/resume");
  await request.post("/api/simulation/speed", { data: { speed: 10 } });
});

test.afterAll(async ({ request }) => {
  await request.post("/api/simulation/speed", { data: { speed: 1 } });
  await request.post("/api/simulation/scenario", { data: { key: "NORMAL" } });
});

test("dashboard loads the seeded network and live data", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  await expect(page.getByText("Simulated data")).toBeVisible();
  const metrics = page.getByRole("region", { name: "Network metrics" });
  await expect(metrics.getByText("Intersections")).toBeVisible();
  await expect(metrics.getByText("6 of 6 on automatic")).toBeVisible();
  await expect.poll(() => page.getByText(/of 126/).count()).toBeGreaterThan(0);
});

test("every section opens without a crash", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  for (const name of [
    "Live Traffic Map", "Intersections", "IoT Sensors", "Signal Control", "Emergency Management", "Incidents",
    "Traffic Prediction", "Analytics", "Alerts", "Simulation Lab", "System Settings", "Overview",
  ]) {
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name }).click();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  }
  expect(errors).toEqual([]);
});

test("pause and resume stop and restart the city clock", async ({ page }) => {
  await page.goto("/simulation");
  await page.getByRole("button", { name: "Pause", exact: true }).first().click();
  await expect(page.getByText(/Paused at/)).toBeVisible();
  const before = await (await page.request.get("/api/simulation/status")).json();
  await page.waitForTimeout(600);
  const after = await (await page.request.get("/api/simulation/status")).json();
  expect(after.sim_seconds).toBe(before.sim_seconds);
  await page.getByRole("button", { name: "Resume" }).first().click();
  await expect(page.getByText(/Running at/)).toBeVisible();
});

test("applying a scenario changes the simulation", async ({ page }) => {
  await page.goto("/simulation");
  await page.getByRole("button", { name: /Heavy congestion/ }).click();
  await expect(page.getByText(/Heavy congestion · Running/)).toBeVisible();
});

test("an ambulance gets priority and arrives", async ({ page }) => {
  await page.goto("/emergency");
  const dispatched = page.waitForResponse((r) => r.url().endsWith("/api/emergency-vehicles") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Dispatch ambulance" }).click();
  const { vehicle_id: vehicleId } = await (await dispatched).json();
  await expect(page.getByText(/vehicle en route with signal priority/)).toBeVisible();
  const row = page.getByRole("row", { name: new RegExp(vehicleId) });
  await expect(row.getByRole("cell", { name: "Arrived" })).toBeVisible({ timeout: 45000 });
});

test("an injected accident is labelled simulated and raises an alert", async ({ page }) => {
  await page.goto("/incidents");
  await page.getByRole("button", { name: "Inject incident" }).click();
  await expect(page.getByText("Major accident").first()).toBeVisible();
  await expect(page.getByText("Simulated", { exact: true }).first()).toBeVisible();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Alerts" }).click();
  await expect(page.getByText(/Simulated Major accident injected/i)).toBeVisible();
});

test("manual mode moves the signal through its safety sequence", async ({ page }) => {
  await page.goto("/signals");
  await page.getByRole("switch", { name: /Manual control/ }).click();
  await expect(page.getByText("Operator chooses the direction")).toBeVisible();
  const view = await (await page.request.get("/api/intersections/INT-001/traffic")).json();
  const target = ["North", "East", "South", "West"].find((d) => d.toUpperCase() !== view.active_direction)!;
  await page.getByRole("button", { name: new RegExp(target) }).last().click();
  await expect
    .poll(async () => (await (await page.request.get("/api/intersections/INT-001/traffic")).json()).active_direction, {
      timeout: 15000,
    })
    .toBe(target.toUpperCase());
  await page.getByRole("switch", { name: /Manual control/ }).click();
  await expect(page.getByText("Adaptive algorithm is in control")).toBeVisible();
});

test("analytics shows stored data and exports a CSV", async ({ page }) => {
  await page.goto("/analytics");
  await expect(page.getByRole("region", { name: "Summary" }).getByText("Traffic volume")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Export CSV" }).click();
  expect((await download).suggestedFilename()).toMatch(/^traffic-report-.*\.csv$/);
});
