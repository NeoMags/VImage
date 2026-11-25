// Plain JS on purpose: the spike has no build step. The real UI (React + Vite) replaces this in B11.
const { invoke } = window.__TAURI__.core;
const status = document.getElementById("status");
const out = document.getElementById("out");

async function run() {
  const t0 = performance.now();
  for (let i = 0; i < 100; i++) {
    try {
      const ep = await invoke("get_sidecar_endpoint");
      const res = await fetch(`http://127.0.0.1:${ep.port}/v1/health`, {
        headers: { Authorization: `Bearer ${ep.token}` },
      });
      const body = await res.json();
      status.textContent = `Engine ready in ${Math.round(performance.now() - t0)} ms (HTTP ${res.status})`;
      status.className = res.ok ? "ok" : "bad";
      out.textContent = JSON.stringify(body, null, 2);
      return;
    } catch (e) {
      status.textContent = `Waiting for local engine… (${e})`;
      await new Promise((r) => setTimeout(r, 200));
    }
  }
  status.className = "bad";
  status.textContent = "Local engine did not start within 20 s. Check the log in %LOCALAPPDATA%\\ImageVault\\logs.";
}
run();
