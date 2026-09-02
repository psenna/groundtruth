#!/usr/bin/env node
// Deterministic checks for a groundtruth development-benchmark run.
//
//   node check.mjs --target <url> --vault <name> [--phase 1|2|3] [--json out.json]
//
// Talks only to the groundtruth REST API. Emits a human report to stdout and,
// with --json, a machine-readable result file. Exit code is 0 unless a HARD
// check failed (these are the ones that mean "the run is not worth scoring").
//
// It does NOT judge note quality — that is the rubric, applied by a human/Claude
// against benchmark/development/gold/. This script establishes the facts.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BENCH = join(HERE, "..");

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith("--")) {
      const next = arr[i + 1];
      acc.push([a.slice(2), next === undefined || next.startsWith("--") ? true : next]);
    }
    return acc;
  }, []),
);
const TARGET = (args.target || process.env.GT || "").replace(/\/$/, "");
const VAULT = args.vault;
const PHASE = args.phase ? Number(args.phase) : null;
if (!TARGET || !VAULT) {
  console.error("usage: node check.mjs --target <url> --vault <name> [--phase N] [--json out.json]");
  process.exit(2);
}

async function api(path, opts = {}) {
  const r = await fetch(TARGET + path, { signal: AbortSignal.timeout(120000), ...opts });
  const t = await r.text();
  let body;
  try { body = JSON.parse(t); } catch { body = t; }
  return { status: r.status, body };
}

async function pMap(items, fn, concurrency = 6) {
  const out = new Array(items.length);
  let i = 0;
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (i < items.length) {
      const idx = i++;
      out[idx] = await fn(items[idx], idx);
    }
  }));
  return out;
}

// --- schema: the declared folders ------------------------------------------------
const SCHEMA_MD = readFileSync(join(BENCH, "schema.md"), "utf8");
const DECLARED_FOLDERS = [...SCHEMA_MD.matchAll(/^- ([a-z][a-z0-9/-]*\/)\s+—/gm)].map((m) => m[1]);

// --- wikilink helpers (mirror groundtruth ingest/links.py) ----------------------
const WIKILINK = /\[\[([^[\]|#]+)(?:#[^[\]|]*)?(?:\|[^[\]]*)?\]\]/g;
function extractLinks(body) {
  return [...(body || "").matchAll(WIKILINK)].map((m) => m[1].trim()).filter(Boolean);
}
function resolveKeys(paths) {
  const keys = new Set();
  for (const p of paths) {
    const stem = p.endsWith(".md") ? p.slice(0, -3) : p;
    keys.add(stem);
    keys.add(stem.split("/").pop());
  }
  return keys;
}

const NORMALIZED_TAG = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const PLACEHOLDER = /\b(placeholder|to-?do|tbd|tba|wip|fixme|stub|coming soon|to be (written|added|filled|completed|done))\b/i;

const result = {
  target: TARGET, vault: VAULT, phase: PHASE, when: new Date().toISOString(),
  hard_fail: [], soft_flags: [], ingests: {}, notes: {}, links: {}, queries: {}, timings: {}, tokens: {},
};
const hard = (m) => result.hard_fail.push(m);
const soft = (m) => result.soft_flags.push(m);

// --- 1. ingest jobs -----------------------------------------------------------
{
  const { body: jobs } = await api("/jobs?limit=200");
  const mine = (Array.isArray(jobs) ? jobs : []).filter((j) => j.vault === VAULT);
  const by = (s) => mine.filter((j) => j.state === s).length;
  result.ingests = {
    total: mine.length, succeeded: by("succeeded"), failed: by("failed"),
    running: by("running"), queued: by("queued"),
    dedup_hits: mine.filter((j) => j.deduplicated || j.dedup_of).length,
    failures: mine.filter((j) => j.state === "failed").map((j) => ({
      source: j.source_label, stage: j.failure_stage, error: (j.error || "").slice(0, 200),
    })),
  };
  // aggregate timings + tokens across succeeded jobs
  for (const j of mine) {
    for (const [k, v] of Object.entries(j.stage_timings || {})) result.timings[k] = (result.timings[k] || 0) + v;
    for (const [k, v] of Object.entries(j.token_usage || {})) {
      const tv = typeof v === "object" ? v.total_tokens || 0 : v;
      result.tokens[k] = (result.tokens[k] || 0) + tv;
    }
  }
  if (mine.length === 0) hard("no ingest jobs found for this vault");
  if (result.ingests.running || result.ingests.queued) hard("ingest still in progress — run again when the queue is drained");
  const okRate = mine.length ? result.ingests.succeeded / (result.ingests.succeeded + result.ingests.failed) : 0;
  result.ingests.success_rate = Number(okRate.toFixed(2));
  if (okRate < 0.6) hard(`ingest success rate ${(okRate * 100).toFixed(0)}% (< 60%)`);
  else if (okRate < 0.85) soft(`ingest success rate ${(okRate * 100).toFixed(0)}%`);
}

// --- 2. notes: structure, folders, frontmatter, tags, substance --------------
const { body: noteList } = await api(`/notes?vault=${encodeURIComponent(VAULT)}`);
const notes = Array.isArray(noteList) ? noteList : [];
const notePaths = notes.map((n) => n.path);
result.notes.count = notes.length;

const bodies = {};
await pMap(notes, async (n) => {
  const { body } = await api(`/notes/${encodeURIComponent(VAULT)}/${encodeURIComponent(n.path)}`);
  bodies[n.path] = body?.body ?? body?.content ?? "";
});

const badFolder = [], badTags = [], thinBody = [], subfolderInvented = [];
for (const n of notes) {
  const folder = n.path.includes("/") ? n.path.slice(0, n.path.lastIndexOf("/") + 1) : "";
  if (!DECLARED_FOLDERS.includes(folder)) {
    badFolder.push(n.path);
    // is it a deeper path under a declared top folder? -> invented subfolder
    if (DECLARED_FOLDERS.some((d) => folder.startsWith(d.split("/")[0] + "/") && folder !== d)) {
      subfolderInvented.push(n.path);
    }
  }
  const tags = n.tags || [];
  if (tags.length < 2 || tags.length > 6 || tags.some((t) => !NORMALIZED_TAG.test(t))) badTags.push([n.path, tags]);
  const b = (bodies[n.path] || "").trim();
  const strippedHeadings = b.replace(/^#.*$/gm, "").replace(/\[\[[^\]]*\]\]/g, "").replace(/[\s\W]/g, "");
  if (b.length < 40 || PLACEHOLDER.test(b) || strippedHeadings.length < 20) thinBody.push(n.path);
}
result.notes.undeclared_folder = badFolder;
result.notes.invented_subfolder = subfolderInvented;
result.notes.bad_tags = badTags;
result.notes.thin_or_placeholder = thinBody;
if (thinBody.length) hard(`${thinBody.length} placeholder / heading-only note(s): ${thinBody.join(", ")}`);
if (badFolder.length) hard(`${badFolder.length} note(s) in an undeclared folder: ${badFolder.join(", ")}`);
if (badTags.length) soft(`${badTags.length} note(s) with bad tag set (count or normalization)`);

// --- 3. link integrity + orphans -------------------------------------------------
const resolvable = resolveKeys(notePaths);
const dangling = [], outDeg = {}, inDeg = {};
for (const n of notes) {
  const links = extractLinks(bodies[n.path]);
  outDeg[n.path] = links.length;
  for (const l of links) {
    if (!resolvable.has(l)) dangling.push([n.path, l]);
    else {
      // credit the target
      for (const t of notePaths) {
        const stem = t.endsWith(".md") ? t.slice(0, -3) : t;
        if (stem === l || stem.split("/").pop() === l) inDeg[t] = (inDeg[t] || 0) + 1;
      }
    }
  }
}
result.links.dangling = dangling;
result.links.orphans = notes.filter((n) => !(outDeg[n.path] > 0) && !(inDeg[n.path] > 0)).map((n) => n.path);
if (dangling.length) hard(`${dangling.length} dangling wikilink(s): ${dangling.map(([a, b]) => `${a}→${b}`).join(", ")}`);
if (result.links.orphans.length > Math.max(1, notes.length * 0.25)) soft(`${result.links.orphans.length} orphan note(s) (no links in or out)`);

// --- 4. queries -------------------------------------------------------------------
let queries = [];
try {
  const qraw = readFileSync(join(BENCH, "queries.md"), "utf8").replace(/```[\s\S]*?```/g, "");
  // format:  - [G|R] <minPhase> || <question> || expect: <stem, stem>
  //   G = grounded once phase >= minPhase (refused before); R = always refused
  queries = [...qraw.matchAll(/^- \[([GR])\]\s+(\d)\s*\|\|\s*(.+?)(?:\s*\|\|\s*expect:\s*(.+))?$/gm)].map((m) => ({
    kind: m[1], minPhase: Number(m[2]), q: m[3].trim(),
    expect: (m[4] || "").split(",").map((s) => s.trim()).filter(Boolean),
  }));
} catch { /* queries.md optional */ }

if (queries.length && !args["no-queries"]) {
  const atPhase = PHASE || 3;
  const rows = await pMap(queries, async (item) => {
    const wantAnswer = item.kind === "G" && atPhase >= item.minPhase;
    const { body } = await api("/query", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ vault: VAULT, question: item.q }),
    });
    const outcome = body?.outcome || (body?.citations !== undefined ? "answer" : body?.reason ? "refused" : "?");
    const cited = (body?.citations || []).map((c) => (c.path || "").replace(/\.md$/, ""));
    const gotAnswer = outcome === "answer";
    const correct = wantAnswer === gotAnswer;
    const citeHit = !item.expect.length || item.expect.some((e) => cited.some((c) => c.endsWith(e) || c.split("/").pop() === e));
    return { q: item.q, kind: wantAnswer ? "grounded" : "refused", outcome, correct, cited,
      cite_ok: wantAnswer ? citeHit : true, answer: (body?.text || body?.message || "").slice(0, 300) };
  }, 3);
  result.queries = {
    total: rows.length,
    kind_correct: rows.filter((r) => r.correct).length,
    refusals_correct: rows.filter((r) => r.kind === "refused" && r.correct).length,
    refusals_total: rows.filter((r) => r.kind === "refused").length,
    citation_hits: rows.filter((r) => r.kind === "grounded" && r.correct && r.cite_ok).length,
    grounded_total: rows.filter((r) => r.kind === "grounded").length,
    rows,
  };
  const wrongRefusal = rows.filter((r) => r.kind === "refused" && !r.correct);
  if (wrongRefusal.length) hard(`${wrongRefusal.length} out-of-scope question(s) answered instead of refused: ${wrongRefusal.map((r) => r.q).join(" | ")}`);
  const missCite = rows.filter((r) => r.kind === "grounded" && r.correct && !r.cite_ok);
  if (missCite.length) soft(`${missCite.length} grounded answer(s) did not cite the expected note`);
}

// --- report --------------------------------------------------------------------
const line = "─".repeat(72);
console.log(line);
console.log(`groundtruth benchmark check — vault ${VAULT}${PHASE ? ` — phase ${PHASE}` : ""}`);
console.log(line);
console.log(`ingests   : ${result.ingests.succeeded}/${result.ingests.succeeded + result.ingests.failed} ok` +
  ` (${(result.ingests.success_rate * 100).toFixed(0)}%), ${result.ingests.dedup_hits} dedup`);
for (const f of result.ingests.failures) console.log(`   FAIL ${f.source}  [${f.stage}] ${f.error}`);
console.log(`notes     : ${result.notes.count}` +
  `  | undeclared-folder ${badFolder.length} (invented-subfolder ${subfolderInvented.length})` +
  `  | thin/placeholder ${thinBody.length}  | bad-tags ${badTags.length}`);
console.log(`links     : ${dangling.length} dangling  | ${result.links.orphans.length} orphans`);
if (result.queries.total) {
  const q = result.queries;
  console.log(`queries   : ${q.kind_correct}/${q.total} right kind` +
    `  | refusals ${q.refusals_correct}/${q.refusals_total}` +
    `  | grounded+cited ${q.citation_hits}/${q.grounded_total}`);
}
console.log(`timings(s): ${Object.entries(result.timings).map(([k, v]) => `${k} ${Math.round(v)}`).join("  ")}`);
console.log(`tokens    : ${Object.entries(result.tokens).map(([k, v]) => `${k} ${v}`).join("  ")}`);
console.log(line);
if (result.hard_fail.length) {
  console.log("HARD FAILURES (run is not worth rubric scoring):");
  for (const m of result.hard_fail) console.log("  ✗ " + m);
} else {
  console.log("no hard failures — proceed to rubric scoring against gold/");
}
if (result.soft_flags.length) {
  console.log("flags (note in the rubric, not blocking):");
  for (const m of result.soft_flags) console.log("  · " + m);
}
console.log(line);

if (args.json && typeof args.json === "string") {
  const { writeFileSync } = await import("node:fs");
  writeFileSync(args.json, JSON.stringify(result, null, 2));
  console.log(`wrote ${args.json}`);
}
process.exit(result.hard_fail.length ? 1 : 0);
