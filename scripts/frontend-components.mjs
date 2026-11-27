#!/usr/bin/env node
// Components file of a frontend image (P100-S1, ADR 0271), CycloneDX 1.6.
//
//   node scripts/frontend-components.mjs <app-name> <package-lock.json> <out-dir> [apk-installed] [os-release]
//
// Writes two files into <out-dir>:
// - components.cdx.json: the npm production dependencies plus the Alpine packages of
//   the runtime image (`/lib/apk/db/installed`, copied in from the nginx image) - the
//   full list, kept in the image for scanners, not served;
// - components-npm.cdx.json: the npm part only. The frontends are static sites that
//   browsers fetch directly, so a served file is public; their npm code ships to every
//   browser anyway, the OS package list doesn't need to.
// Node built-ins only: generating it must not download anything.
import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const [appName, lockPath, outDir, apkPath, osReleasePath] = process.argv.slice(2);
if (!appName || !lockPath || !outDir) {
  console.error("usage: frontend-components.mjs <app> <package-lock.json> <out-dir> [apk-installed]");
  process.exit(2);
}

function npmComponents(lock) {
  const seen = new Map();
  for (const [path, entry] of Object.entries(lock.packages ?? {})) {
    if (!path || entry.dev || entry.devOptional || entry.link || !entry.version) continue;
    const name = entry.name ?? path.slice(path.lastIndexOf("node_modules/") + "node_modules/".length);
    const key = `${name}@${entry.version}`;
    if (seen.has(key)) continue;
    const purl = `pkg:npm/${name.startsWith("@") ? "%40" + name.slice(1) : name}@${entry.version}`;
    const component = {
      type: "library",
      name,
      version: entry.version,
      purl,
      "bom-ref": purl,
      properties: [{ name: "dms:ecosystem", value: "npm" }],
    };
    if (entry.license) component.licenses = [{ expression: String(entry.license) }];
    seen.set(key, component);
  }
  return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
}

// `alpine:3.21.3` - vulnerability records are per distribution release (P100-S2).
function distroOf(text) {
  const fields = Object.fromEntries(
    text
      .split("\n")
      .filter((line) => line.includes("="))
      .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1).replace(/"/g, "")])
  );
  return fields.ID && fields.VERSION_ID ? `${fields.ID}:${fields.VERSION_ID}` : null;
}

function apkComponents(text, distro) {
  const components = [];
  for (const block of text.split("\n\n")) {
    const fields = {};
    for (const line of block.split("\n")) {
      if (line.length > 2 && line[1] === ":") fields[line[0]] = line.slice(2);
    }
    if (!fields.P || !fields.V) continue;
    const arch = fields.A ? `?arch=${fields.A}` : "";
    const purl = `pkg:apk/alpine/${fields.P}@${fields.V}${arch}`;
    // Security records name the origin (source) package, e.g. `openssl` for `libssl3`.
    const properties = [
      { name: "dms:ecosystem", value: "Alpine" },
      { name: "dms:source", value: fields.o || fields.P },
    ];
    if (distro) properties.push({ name: "dms:distro", value: distro });
    components.push({
      type: "library",
      name: fields.P,
      version: fields.V,
      purl,
      "bom-ref": purl,
      properties,
    });
  }
  return components.sort((a, b) => a.name.localeCompare(b.name));
}

function documentFor(components) {
  return {
    bomFormat: "CycloneDX",
    specVersion: "1.6",
    serialNumber: `urn:uuid:${randomUUID()}`,
    version: 1,
    metadata: {
      timestamp: new Date().toISOString(),
      component: { type: "application", name: appName, "bom-ref": appName },
      tools: { components: [{ type: "library", name: "scripts/frontend-components.mjs" }] },
    },
    components,
  };
}

const npm = npmComponents(JSON.parse(readFileSync(lockPath, "utf8")));
let os = [];
if (apkPath) {
  try {
    let distro = null;
    try {
      distro = osReleasePath ? distroOf(readFileSync(osReleasePath, "utf8")) : null;
    } catch {
      distro = null;
    }
    os = apkComponents(readFileSync(apkPath, "utf8"), distro);
  } catch {
    os = [];
  }
}
mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, "components.cdx.json"), JSON.stringify(documentFor([...npm, ...os]), null, 1));
writeFileSync(join(outDir, "components-npm.cdx.json"), JSON.stringify(documentFor(npm), null, 1));
console.error(`${appName}: ${npm.length} npm, ${os.length} Alpine components`);
