#!/usr/bin/env node
/**
 * Fail on high/critical production advisories, with narrow expiring exceptions.
 *
 * npm currently exposes no first-party advisory allowlist. Keeping the exception
 * in reviewed JSON makes the package, exact version, rationale, and expiry visible
 * instead of weakening the whole gate to "critical only".
 */

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const webRoot = join(root, "web");
const policyPath = join(root, "security", "npm-audit-allowlist.json");
const lockPath = join(webRoot, "package-lock.json");
const policy = JSON.parse(readFileSync(policyPath, "utf8"));
const lock = JSON.parse(readFileSync(lockPath, "utf8"));
const today = new Date().toISOString().slice(0, 10);

if (policy.schema_version !== 1 || typeof policy.advisories !== "object") {
  throw new Error("invalid npm audit allowlist schema");
}

for (const [id, exception] of Object.entries(policy.advisories)) {
  if (!/^GHSA-[a-z0-9-]+$/.test(id)) {
    throw new Error(`invalid advisory id in allowlist: ${id}`);
  }
  if (!exception.reason || exception.expires < today) {
    throw new Error(`expired or undocumented npm audit exception: ${id}`);
  }
  const installed = lock.packages?.[`node_modules/${exception.package}`]?.version;
  if (!installed || !exception.versions.includes(installed)) {
    throw new Error(
      `npm audit exception ${id} does not match installed ${exception.package}@${installed ?? "missing"}`,
    );
  }
}

const audit = spawnSync("npm", ["audit", "--omit=dev", "--json"], {
  cwd: webRoot,
  encoding: "utf8",
});
if (audit.error) {
  throw audit.error;
}
if (audit.signal || ![0, 1].includes(audit.status)) {
  process.stderr.write(audit.stderr || audit.stdout);
  throw new Error(`npm audit failed without a vulnerability report (status ${audit.status})`);
}

let report;
try {
  report = JSON.parse(audit.stdout);
} catch {
  process.stderr.write(audit.stderr || audit.stdout);
  throw new Error("npm audit did not return JSON");
}
if (report.error || typeof report.vulnerabilities !== "object") {
  throw new Error(`npm audit returned an error report: ${JSON.stringify(report.error ?? report)}`);
}

const severityRank = { info: 0, low: 1, moderate: 2, high: 3, critical: 4 };
const blocked = [];
const allowed = [];
const usedExceptions = new Set();

for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
  for (const advisory of vulnerability.via ?? []) {
    if (typeof advisory !== "object" || severityRank[advisory.severity] < severityRank.high) {
      continue;
    }
    const id = advisory.url?.match(/GHSA-[a-z0-9-]+/)?.[0];
    const exception = id ? policy.advisories[id] : undefined;
    if (exception?.package === advisory.name) {
      usedExceptions.add(id);
      allowed.push(`${id} (${advisory.name}) until ${exception.expires}`);
    } else {
      blocked.push(`${id ?? advisory.source} ${advisory.name}: ${advisory.title}`);
    }
  }
}

for (const id of Object.keys(policy.advisories)) {
  if (!usedExceptions.has(id)) {
    blocked.push(`${id}: allowlist entry is no longer present in npm audit; remove it`);
  }
}

if (allowed.length) {
  process.stderr.write(`Allowed scoped npm advisories:\n- ${allowed.join("\n- ")}\n`);
}
if (blocked.length) {
  process.stderr.write(`Unapproved high/critical npm advisories:\n- ${blocked.join("\n- ")}\n`);
  process.exit(1);
}

process.stdout.write("No unapproved high/critical production npm advisories found.\n");
