// Mixed realistic-usage load test for the DMS gateway.
//
// Simulates concurrent users logging in, browsing folders, searching,
// uploading, and downloading documents - the same actions a real user-ui
// session performs (see apps/user-ui/src/lib/api.ts for the reference
// request shapes this script mirrors).
//
// Ramp profile: 10min ramp-up to 200 VUs, 5min hold at 200, 5min ramp-down.
// "Goodput" is defined as successful (2xx) responses that also complete
// under an SLA latency threshold (default 1000ms, override with SLA_MS).
//
// Usage:
//   loadtest/run.sh
// or directly:
//   loadtest/.tools/k6-v2.2.0-linux-amd64/k6 run loadtest/k6/scenario.js

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";
import { randomIntBetween, randomString } from "https://jslib.k6.io/k6-utils/1.6.0/index.js";

const BASE_URL = __ENV.GATEWAY_BASE_URL || "http://localhost:8009";
const SLA_MS = Number(__ENV.SLA_MS || 1000);
const USER_POOL_SIZE = Number(__ENV.USER_POOL_SIZE || 50);
const ADMIN_USERNAME = __ENV.ADMIN_USERNAME || "users-admin";
const ADMIN_PASSWORD = __ENV.ADMIN_PASSWORD || "users-admin";

const goodput = new Rate("goodput");
const actionDuration = new Trend("action_duration", true);

// Stages default to the agreed profile (10min ramp-up to 200 VUs, 5min
// hold, 5min ramp-down) but are overridable for a quick rehearsal, e.g.:
//   RAMP_UP=1m RAMP_UP_VUS=20 HOLD=30s RAMP_DOWN=30s loadtest/run.sh
const RAMP_UP = __ENV.RAMP_UP || "10m";
const RAMP_UP_VUS = Number(__ENV.RAMP_UP_VUS || 200);
const HOLD = __ENV.HOLD || "5m";
const RAMP_DOWN = __ENV.RAMP_DOWN || "5m";

export const options = {
  scenarios: {
    mixed_usage: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: RAMP_UP, target: RAMP_UP_VUS },
        { duration: HOLD, target: RAMP_UP_VUS },
        { duration: RAMP_DOWN, target: 0 },
      ],
      gracefulRampDown: "30s",
    },
  },
  thresholds: {
    goodput: ["rate>0.95"],
    http_req_failed: ["rate<0.05"],
  },
};

function apiUrl(serviceType, path) {
  return `${BASE_URL}/api/${serviceType}/${path}`;
}

function authHeaders(token, extra) {
  return Object.assign({ Authorization: `Bearer ${token}` }, extra || {});
}

// k6's default Prometheus `name` tag is the full request URL, which
// explodes cardinality for parameterized paths (document/user IDs). Every
// request below passes an explicit low-cardinality `name` tag instead.
function withName(name, headers) {
  return { headers, tags: { name } };
}

// Records both the built-in goodput/duration metrics and a per-action check,
// so the JSON output carries an SLA-aware pass/fail per sample, not just the
// raw http_req_duration k6 already tags.
function recordAction(name, res) {
  const ok = res.status >= 200 && res.status < 300;
  const withinSla = res.timings.duration < SLA_MS;
  goodput.add(ok && withinSla, { action: name });
  actionDuration.add(res.timings.duration, { action: name });
  check(res, { [`${name}: 2xx`]: () => ok }, { action: name });
}

function login(username, password) {
  const res = http.post(
    apiUrl("auth-service", "login"),
    JSON.stringify({ username, password }),
    withName("login", { "Content-Type": "application/json" })
  );
  recordAction("login", res);
  if (res.status !== 200) {
    return null;
  }
  const body = res.json();
  return {
    token: body.access_token,
    // expires_in is in seconds; refresh a bit early to avoid a request
    // failing mid-flight right at expiry.
    expiresAt: Date.now() + (body.expires_in - 15) * 1000,
  };
}

// Per-VU token cache. Each k6 VU runs its own JS runtime/module scope, so a
// module-level variable is naturally isolated per VU - this avoids logging
// in on every iteration, which matters because the login endpoint is keyed
// by client IP (pre-auth), not by user, in the gateway rate limiter.
let session = null;

function ensureSession(user) {
  if (session && Date.now() < session.expiresAt) {
    return session.token;
  }
  session = login(user.username, user.password);
  return session ? session.token : null;
}

export function setup() {
  const adminLogin = login(ADMIN_USERNAME, ADMIN_PASSWORD);
  if (!adminLogin) {
    throw new Error("setup: could not log in as admin technical account");
  }
  const adminToken = adminLogin.token;
  const runId = randomString(8);

  const users = [];
  for (let i = 0; i < USER_POOL_SIZE; i++) {
    const username = `loadtest-${runId}-${i}`;
    const password = `LoadTest-${runId}-${i}!`;
    const res = http.post(
      apiUrl("auth-service", "users"),
      JSON.stringify({
        username,
        password,
        email: `${username}@example.invalid`,
        first_name: "Load",
        last_name: `Test${i}`,
      }),
      withName("setup_create_user", authHeaders(adminToken, { "Content-Type": "application/json" }))
    );
    if (res.status !== 201) {
      throw new Error(`setup: failed to create test user ${username}: ${res.status} ${res.body}`);
    }
    users.push({ username, password, id: res.json().id });
  }

  const folderRes = http.post(
    apiUrl("folder-service", "folders"),
    JSON.stringify({
      name: `Load Test ${runId}`,
      parent_id: "root",
      created_by: ADMIN_USERNAME,
    }),
    withName("setup_create_folder", authHeaders(adminToken, { "Content-Type": "application/json" }))
  );
  if (folderRes.status !== 201) {
    throw new Error(`setup: failed to create load-test folder: ${folderRes.status} ${folderRes.body}`);
  }
  const folderId = folderRes.json().id;

  // Seed document, repeatedly downloaded by VUs during the run instead of
  // every download depending on a document some other VU just uploaded.
  const seedUpload = http.post(
    apiUrl("document-service", "documents"),
    {
      file: http.file("seed content for load test downloads\n".repeat(50), "seed.txt", "text/plain"),
      title: "seed.txt",
      created_by: ADMIN_USERNAME,
      folder_id: folderId,
    },
    withName("setup_seed_upload", authHeaders(adminToken))
  );
  if (seedUpload.status !== 201) {
    throw new Error(`setup: failed to upload seed document: ${seedUpload.status} ${seedUpload.body}`);
  }

  return {
    runId,
    users,
    folderId,
    seedDocumentId: seedUpload.json().id,
    adminToken,
  };
}

function pickUser(data) {
  return data.users[__VU % data.users.length];
}

function listFolder(token, data) {
  // Occasionally list root too (realistic navigation) but mostly stay
  // inside the dedicated load-test folder so measurements aren't confounded
  // by the large pre-existing root folder content.
  const folderId = Math.random() < 0.1 ? "root" : data.folderId;
  const res = http.get(
    apiUrl("folder-service", `folders/${folderId}/children`),
    withName("list_folder", authHeaders(token))
  );
  recordAction("list_folder", res);
}

function searchDocuments(token) {
  const res = http.get(
    apiUrl("search-service", "search?q=seed&limit=10"),
    withName("search", authHeaders(token))
  );
  recordAction("search", res);
}

function downloadSeedDocument(token, data) {
  const res = http.get(
    apiUrl("document-service", `documents/${data.seedDocumentId}/content`),
    withName("download", authHeaders(token))
  );
  recordAction("download", res);
}

function uploadDocument(token, data) {
  const title = `upload-${__VU}-${__ITER}.txt`;
  const res = http.post(
    apiUrl("document-service", "documents"),
    {
      file: http.file(`load test upload ${title}\n`, title, "text/plain"),
      title,
      created_by: pickUser(data).username,
      folder_id: data.folderId,
    },
    withName("upload", authHeaders(token))
  );
  recordAction("upload", res);
}

// Weighted mixed workload: browsing/searching dominates, uploads are the
// rarest action - matches typical DMS usage (many reads per write).
const ACTIONS = [
  { weight: 0.35, run: listFolder },
  { weight: 0.25, run: downloadSeedDocument },
  { weight: 0.2, run: searchDocuments },
  { weight: 0.2, run: uploadDocument },
];

function pickAction() {
  const r = Math.random();
  let acc = 0;
  for (const action of ACTIONS) {
    acc += action.weight;
    if (r < acc) return action.run;
  }
  return ACTIONS[ACTIONS.length - 1].run;
}

export default function (data) {
  const user = pickUser(data);
  const token = ensureSession(user);
  if (!token) {
    return;
  }
  const action = pickAction();
  action(token, data);
  sleep(randomIntBetween(1, 3));
}

// teardown() runs long after setup() - a full ramp profile takes ~20
// minutes, well past the auth-service access token lifetime (300s by
// default), and cleanup itself can involve thousands of deletes (one per
// upload) that exceed the gateway's per-principal rate limit
// (600 req/60s, admin-keyed since it's an authenticated request). A fixed
// token or an unretried 429 would silently drop most of the cleanup - the
// original version of this function did exactly that. TeardownSession
// re-logs in as needed and retries on 429 instead.
function newTeardownSession() {
  let token = null;
  let tokenIssuedAt = 0;

  function currentToken() {
    if (!token || Date.now() - tokenIssuedAt > 250 * 1000) {
      const result = login(ADMIN_USERNAME, ADMIN_PASSWORD);
      if (!result) {
        throw new Error("teardown: could not re-authenticate as admin");
      }
      token = result.token;
      tokenIssuedAt = Date.now();
    }
    return token;
  }

  function request(method, url, name, body, maxAttempts) {
    maxAttempts = maxAttempts || 5;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      const params = withName(name, authHeaders(currentToken(), { "Content-Type": "application/json" }));
      const res = http.request(method, url, body, params);
      if (res.status === 429) {
        sleep(2);
        continue;
      }
      if (res.status === 401) {
        // Token expired between currentToken() and the request itself -
        // force a re-login on the next attempt.
        token = null;
        continue;
      }
      return res;
    }
    return null;
  }

  return { request };
}

export function teardown(data) {
  const session = newTeardownSession();

  // A single POST /folders/{id}/trash cascades over the folder's entire
  // active subtree (folder-service repository.soft_delete_folder calls
  // document-service internally) - one request soft-deletes the folder AND
  // every document in it. The original version of this function instead
  // listed then individually DELETEd every document one at a time, which
  // for a full-scale run (thousands of uploads) blew past both k6's
  // default 60s teardownTimeout and the gateway's per-admin rate limit.
  const trashRes = session.request(
    "POST",
    apiUrl("folder-service", `folders/${data.folderId}/trash`),
    "teardown_trash_folder",
    JSON.stringify({ deleted_by: ADMIN_USERNAME })
  );
  if (!trashRes || trashRes.status < 200 || trashRes.status >= 300) {
    console.error(
      `teardown: failed to trash folder ${data.folderId}: ${trashRes && trashRes.status} ${trashRes && trashRes.body}`
    );
  }

  let deletedUsers = 0;
  let failedUsers = 0;
  for (const user of data.users) {
    const res = session.request(
      "DELETE",
      apiUrl("auth-service", `users/${user.id}`),
      "teardown_delete_user",
      null
    );
    if (res && res.status >= 200 && res.status < 300) {
      deletedUsers++;
    } else {
      failedUsers++;
    }
  }

  console.log(`teardown: folder trashed=${trashRes && trashRes.status === 200}, users deleted=${deletedUsers} failed=${failedUsers}`);
}
