#!/usr/bin/env node
// Benchmark run helper — used by the `benchmark-groundtruth` skill.
//
//   node bench.mjs schema  <target> <vault>            set the vault schema (MCP update_schema)
//   node bench.mjs create  <target> <vault>            create the vault (POST /vaults, init)
//   node bench.mjs ingest  <target> <vault> <phase>    submit a phase's corpus (async)
//   node bench.mjs dedup   <target> <vault>            re-ingest one phase-1 doc (dedup probe)
//   node bench.mjs wait    <target> <vault>            poll /jobs until the queue is drained
//   node bench.mjs dump    <target> <vault>            print every note (path, tags, body)
//   node bench.mjs jobs    <target> <vault>            one-line-per-job status
//
// <target> is a base URL. No npm deps.

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BENCH = join(HERE, "..");
const [cmd, rawTarget, vault, phase] = process.argv.slice(2);
const T = (rawTarget || "").replace(/\/$/, "");
if (!cmd || !T || !vault) {
  console.error("usage: node bench.mjs <schema|create|ingest|dedup|wait|dump|jobs> <target> <vault> [phase]");
  process.exit(2);
}

async function api(path, opts) {
  const r = await fetch(T + path, opts);
  const t = await r.text();
  let body; try { body = JSON.parse(t); } catch { body = t; }
  return { status: r.status, body };
}
const jpost = (path, obj) => api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(obj) });

// --- MCP (streamable HTTP, stateless) for update_schema ------------------------
async function mcp(method, params, sid) {
  const h = { "content-type": "application/json", accept: "application/json, text/event-stream" };
  if (sid) h["mcp-session-id"] = sid;
  const r = await fetch(T + "/mcp/", { method: "POST", headers: h, body: JSON.stringify({ jsonrpc: "2.0", id: Date.now(), method, params }) });
  const newSid = r.headers.get("mcp-session-id");
  const txt = await r.text();
  let body;
  if ((r.headers.get("content-type") || "").includes("event-stream")) {
    const dl = txt.split("\n").find((l) => l.startsWith("data:"));
    body = dl ? JSON.parse(dl.slice(5).trim()) : null;
  } else body = txt ? JSON.parse(txt) : null;
  return { sid: newSid, body };
}
async function mcpSession() {
  const init = await mcp("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "bench", version: "1" } });
  await mcp("notifications/initialized", {}, init.sid).catch(() => {});
  return init.sid;
}

const PHASE_DIR = { 1: "phase-1-small", 2: "phase-2-medium", 3: "phase-3-large" };

if (cmd === "create") {
  const r = await jpost("/vaults", { name: vault, repo_root: `/data/${vault}`, init: true });
  console.log(r.status, JSON.stringify(r.body));
} else if (cmd === "schema") {
  const md = readFileSync(join(BENCH, "schema.md"), "utf8");
  const sid = await mcpSession();
  const r = await mcp("tools/call", { name: "update_schema", arguments: { vault, markdown: md, rationale: "benchmark: 5 flat folders + prescriptive tag vocab" } }, sid);
  console.log(JSON.stringify(r.body?.result ?? r.body));
} else if (cmd === "ingest") {
  const dir = join(BENCH, "corpus", PHASE_DIR[phase]);
  for (const f of readdirSync(dir).filter((f) => f.endsWith(".md")).sort()) {
    const text = readFileSync(join(dir, f), "utf8");
    const r = await jpost("/ingest", { vault, text, source_label: `phase${phase}/${f}` });
    console.log(`phase${phase}/${f} -> ${r.status} ${JSON.stringify(r.body)}`);
  }
} else if (cmd === "dedup") {
  // re-ingest a phase-1 doc verbatim; expect deduplicated:true
  const f = "commit-conventions.md";
  const text = readFileSync(join(BENCH, "corpus/phase-1-small", f), "utf8");
  const r = await jpost("/ingest", { vault, text, source_label: `phase2/dedup-${f}` });
  console.log(`dedup re-ingest ${f} -> ${r.status} ${JSON.stringify(r.body)}`);
} else if (cmd === "wait") {
  let last = "";
  for (;;) {
    const { body } = await api("/jobs?limit=200");
    const mine = (Array.isArray(body) ? body : []).filter((j) => j.vault === vault);
    const s = mine.reduce((m, j) => ((m[j.state] = (m[j.state] || 0) + 1), m), {});
    const cur = JSON.stringify(s);
    if (cur !== last) { console.log(new Date().toLocaleTimeString(), cur); last = cur; }
    if (!mine.some((j) => j.state === "queued" || j.state === "running")) break;
    await new Promise((z) => setTimeout(z, 20000));
  }
  console.log("queue drained");
} else if (cmd === "jobs") {
  const { body } = await api("/jobs?limit=200");
  for (const j of (Array.isArray(body) ? body : []).filter((j) => j.vault === vault)) {
    const tu = Object.entries(j.token_usage || {}).map(([k, v]) => `${k}:${typeof v === "object" ? v.total_tokens : v}`).join(" ");
    console.log(j.state.padEnd(9), (j.source_label || "?").padEnd(34),
      `c:${j.notes_created?.length ?? 0} u:${j.notes_updated?.length ?? 0}`,
      j.deduplicated ? "DEDUP" : "", tu ? `| ${tu}` : "",
      j.error ? `| ${j.error.slice(0, 120)}` : "");
  }
} else if (cmd === "dump") {
  const { body: list } = await api(`/notes?vault=${encodeURIComponent(vault)}`);
  const notes = Array.isArray(list) ? list : [];
  console.log(`# ${vault} — ${notes.length} notes\n`);
  for (const n of notes) {
    const { body } = await api(`/notes/${encodeURIComponent(vault)}/${encodeURIComponent(n.path)}`);
    console.log("=".repeat(78));
    console.log(`PATH: ${n.path}`);
    console.log(`TAGS: ${JSON.stringify(n.tags)}`);
    console.log("-".repeat(78));
    console.log((body?.body ?? body?.content ?? JSON.stringify(body)).trim());
    console.log();
  }
} else {
  console.error("unknown command:", cmd);
  process.exit(2);
}
