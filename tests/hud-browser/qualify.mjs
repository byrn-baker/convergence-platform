import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { setTimeout as wait } from "node:timers/promises";
import { chromium } from "playwright";

// The built server supplies runtime identity, module discovery, cookie issuance,
// revocation and /api/home. Unrelated integrations use synthetic browser data.
function fixture(path) {
  switch (path) {
    case "/api/graph":
      return {
        identity: { name: "Fixture" },
        stats: { deviceCount: 1 },
        generatedAt: new Date().toISOString(),
        devices: [{ id: "sw", name: "FixtureSwitch", protocol: "ssh" }],
        integrations: [],
        settings: [],
        config: {},
      };
    case "/api/home/health":
      return {
        healthScore: { value: 97, status: "healthy" },
        wanLatency: { value: 8 },
        wanLoss: { value: 0 },
        alertCount: { firing: 0 },
      };
    case "/api/home/wifi":
      return { clients: { wireless: 4, wired: 1, total: 5 }, txRetries: [] };
    case "/api/home/events":
      return { events: [] };
    case "/api/chat/models":
      return { models: [] };
    default:
      return {};
  }
}

if (!process.argv[2])
  throw Error("Pass the artifact path printed by setup/hud.py build.");
const tree = resolve(process.argv[2]),
  hud = join(tree, "ui/netclaw-visual");
const home = await mkdtemp(join(tmpdir(), "hud-browser-"));
const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  backendCalls.push(url.pathname);
  assert.equal(req.headers.authorization, "Bearer " + readToken);
  assert.equal(req.method, "GET");
  assert.equal(url.searchParams.get("site"), "branch");
  const pathname = url.pathname.replace("/api/", "/api/home/");
  let value = fixture(pathname);
  if (
    pathname === "/api/home/events" &&
    url.searchParams.get("status") === "escalated"
  )
    value = {
      events: [
        {
          site_id: "branch",
          id: caseId,
          alert_name: "Browser review case",
          status: "escalated",
          timestamp: new Date().toISOString(),
        },
      ],
    };
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify({ site: "branch", ...value }));
});
async function listen(s) {
  await new Promise((r) => s.listen(0, "127.0.0.1", r));
  return s.address().port;
}
const backendCalls = [],
  caseId = "01234567-89ab-4def-8123-0123456789ab",
  readToken = "synthetic-browser-scope-only-" + "a".repeat(32);
const backendPort = await listen(server);
const shell = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE,
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const processes = [];
try {
  for (const local of [false, true]) {
    const free = http.createServer();
    const port = await listen(free);
    await new Promise((r) => free.close(r));
    const proc = spawn(process.execPath, ["server.js"], {
      cwd: hud,
      env: {
        PATH: process.env.PATH,
        HOME: home,
        HUD_PORT: String(port),
        HUD_UI_PORT: String(port === 29999 ? 29998 : 29999),
        HUD_HOST: local ? "127.0.0.1" : "192.0.2.10",
        CONVERGENCE_HUD_LOCAL_OWNER: "1",
        CONVERGENCE_HUD_READ_TOKEN: readToken,
        CONVERGENCE_API_URL: "http://127.0.0.1:" + backendPort,
        CONVERGENCE_SITE: "branch",
        CONVERGENCE_REVIEWER_ORIGIN: "https://review.example.test",
        NETCLAW_RUNTIME: "openclaw",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let startup = "";
    processes.push(proc);
    proc.stdout.on("data", (v) => (startup += v));
    proc.stderr.on("data", (v) => (startup += v));
    const origin = "http://127.0.0.1:" + port;
    let ready = false;
    for (let i = 0; i < 80; i++) {
      try {
        ready = (await fetch(origin + "/api/runtime")).ok;
        if (ready) break;
      } catch {}
      await wait(100);
    }
    assert.ok(ready, startup);
    const runtime = await (await fetch(origin + "/api/runtime")).json();
    assert.equal(runtime.capabilities.siteOperations, true);
    const protectedUpdate = await fetch(origin + "/api/env", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        updates: { CONVERGENCE_API_URL: "https://invalid.test" },
      }),
    });
    assert.equal(protectedUpdate.status, 403);
    for (const kind of ["dashboard", "classic"]) {
      const start = backendCalls.length;
      const ctx = await shell.newContext({
        viewport: { width: 1500, height: 970 },
        serviceWorkers: "block",
      });
      try {
        const page = await ctx.newPage();
        page.setDefaultTimeout(12000);
        const errors = [],
          writes = [],
          homeReads = [];
        page.on("pageerror", (e) => errors.push(e.message));
        await ctx.routeWebSocket("**/*", () => {});
        await ctx.route("**/*", (route) => {
          const req = route.request(),
            u = new URL(req.url());
          if (u.origin !== origin) return route.abort();
          if (!u.pathname.startsWith("/api/")) return route.continue();
          if (u.pathname === "/api/terminal/devices")
            return route.fulfill({
              json: {
                devices: [
                  {
                    id: "fixture-sw",
                    name: "fixture-sw",
                    supported: true,
                    protocol: "ssh",
                    host: "192.0.2.1",
                    port: 22,
                  },
                ],
              },
            });

          if (u.pathname.startsWith("/api/home/")) homeReads.push(u.pathname);
          if (!["GET", "HEAD"].includes(req.method())) writes.push(u.pathname);
          if (
            [
              "/api/runtime",
              "/api/modules",
              "/api/hud/session",
              "/api/hud/session/revoke",
            ].includes(u.pathname) ||
            u.pathname.startsWith("/api/home/")
          )
            return route.continue();
          return route.fulfill({ json: fixture(u.pathname) });
        });
        await page.goto(
          origin + (kind === "classic" ? "/classic.html?fixture=empty" : "/"),
        );
        if (kind === "classic") {
          await page
            .locator("#loading.hidden")
            .waitFor({ state: "attached", timeout: 20000 });
          await page.locator('[data-app-tab="home"]').click();
        } else
          await page
            .getByRole("button", { name: "Site operations", exact: true })
            .click();
        await page
          .getByRole("link", { name: "Open reviewer" })
          .first()
          .waitFor();
        if (local) {
          await page
            .getByText("Health score", { exact: true })
            .first()
            .waitFor();
          await page.locator('[data-home-sub="triage"]:visible').click();
          await page
            .getByText("Browser review case", { exact: true })
            .first()
            .waitFor();
          const caseButton = page.locator("[data-triage-select]").first();
          if (await caseButton.count()) await caseButton.click();
          const link = page.getByRole("link", { name: "Review this case" });
          await link.waitFor();
          assert.equal(
            await link.getAttribute("href"),
            "https://review.example.test/#site=branch&case=" + caseId,
          );
          assert.equal(
            await page
              .locator(
                "[data-triage-action],[data-jev-review],[data-security-submit]",
              )
              .count(),
            0,
          );
          await page.evaluate(() =>
            fetch("/api/hud/session/revoke", { method: "POST" }),
          );
          if (kind === "dashboard")
            await page
              .getByRole("button", { name: "Refresh Convergence sources" })
              .click();
          else await page.locator("#home-refresh:visible").click();
          await page
            .getByText(/Site telemetry requires a local owner session/)
            .first()
            .waitFor();
          assert.equal(
            await page
              .getByText("Browser review case", { exact: true })
              .count(),
            0,
          );
          assert.ok(backendCalls.length > start);
        } else {
          assert.deepEqual([...new Set(homeReads)], ["/api/home/status"]);
          assert.equal(backendCalls.length, start);
          await page
            .getByText(/Site telemetry requires a local owner session/)
            .first()
            .waitFor();
        }
        assert.equal(writes.filter((p) => p.startsWith("/api/home")).length, 0);
        assert.deepEqual(errors, []);
        if (kind === "dashboard" && local) {
          await page.goto(origin + "/canvas.html#terminal=fixture-sw");
          const device = page.getByRole("combobox", { name: "Testbed device" });
          // The native selector mounts before the asynchronous testbed response.
          await device.locator('option[value="fixture-sw"]:checked').waitFor({state:"attached"});
          assert.equal(await device.inputValue(), "fixture-sw");
          assert.equal(new URL(page.url()).hash, "");
          assert.ok(!writes.some((path) => path.includes("/terminal/")));
          assert.deepEqual(errors, []);
        }
        console.log(
          [
            tree.split("/").pop(),
            kind,
            local
              ? "local owner, reviewed case link and revoked session"
              : "network HUD, reviewer link only",
          ].join(" | "),
        );
      } finally {
        await ctx.close();
      }
    }
    proc.kill();
  }
} finally {
  for (const proc of processes) proc.kill();
  await shell.close();
  server.closeAllConnections();
  server.close();
  await rm(home, { recursive: true, force: true });
}
