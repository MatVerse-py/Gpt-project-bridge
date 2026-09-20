import fs from 'node:fs';
import crypto from 'node:crypto';
import puppeteer from 'puppeteer-core';

const bridgeUrl = 'https://matverse-external-bridge-verifier-8iuh4o.v2.appdeploy.ai/';
const brokerUrl = 'https://matverse-secret-broker-r0992d.v2.appdeploy.ai/';

function sortObject(value) {
  if (Array.isArray(value)) return value.map(sortObject);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, sortObject(value[key])]),
    );
  }
  return value;
}

function stableHash(value) {
  return crypto
    .createHash('sha256')
    .update(JSON.stringify(sortObject(value)))
    .digest('hex');
}

function chromePath() {
  const candidates = [
    process.env.CHROME_BIN,
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ].filter(Boolean);
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  if (!found) throw new Error('CHROME_NOT_FOUND');
  return found;
}

const browser = await puppeteer.launch({
  executablePath: chromePath(),
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
});

let report;
try {
  const bridgePage = await browser.newPage();
  await bridgePage.goto(bridgeUrl, { waitUntil: 'networkidle2', timeout: 45_000 });
  await bridgePage.waitForFunction(
    () => document.body.getAttribute('data-external-challenge') === 'PASS_ALL_CONTROLS',
    { timeout: 45_000 },
  );
  const bridge = await bridgePage.evaluate(() => ({
    marker: document.body.getAttribute('data-external-challenge'),
    contract: document.querySelector('#contract')?.textContent ?? '',
    counter: document.querySelector('#counter')?.textContent ?? '',
    stateHash: document.querySelector('#stateHash')?.textContent ?? '',
    finalResult: document.querySelector('#result')?.textContent ?? '',
    resetResult: document.querySelector('#resetResult')?.textContent ?? '',
    note: document.querySelector('.note')?.textContent ?? '',
  }));

  const brokerPage = await browser.newPage();
  await brokerPage.goto(brokerUrl, { waitUntil: 'networkidle2', timeout: 45_000 });
  await brokerPage.waitForFunction(
    () => document.querySelector('#status')?.textContent?.includes('Broker online'),
    { timeout: 45_000 },
  );
  await brokerPage.click('#checkPolicy');
  await brokerPage.waitForFunction(
    () => document.querySelector('#policy')?.textContent?.includes('Policy self-check PASS'),
    { timeout: 30_000 },
  );
  await brokerPage.click('#probeRoute');
  await brokerPage.waitForFunction(
    () => document.querySelector('#probe')?.textContent?.includes('Blocked as expected'),
    { timeout: 30_000 },
  );
  const broker = await brokerPage.evaluate(() => ({
    status: document.querySelector('#status')?.textContent ?? '',
    policy: document.querySelector('#policy')?.textContent ?? '',
    probe: document.querySelector('#probe')?.textContent ?? '',
  }));

  const hardChecks = {
    bridge_marker: bridge.marker === 'PASS_ALL_CONTROLS',
    bridge_contract: bridge.contract === 'BRIDGE-CAP-ACCUMULATE-0.1',
    bridge_counter: bridge.counter === '3',
    bridge_stale_replay_blocked: bridge.finalResult.includes('PRE_STATE_MISMATCH'),
    bridge_external_provider_boundary:
      bridge.note.includes('not an independent administrative witness'),
    bridge_state_hash_shape: /^[0-9a-f]{64}$/.test(bridge.stateHash),
    broker_online: broker.status.includes('Broker online'),
    broker_secret_not_configured: broker.status.includes('not configured'),
    broker_secret_ref_bound:
      broker.status.includes('secret_ref://openai/matverse/executor-transplant'),
    broker_policy_pass: broker.policy.includes('Policy self-check PASS'),
    broker_oidc_issuer:
      broker.policy.includes('https://token.actions.githubusercontent.com'),
    broker_audience: broker.policy.includes('matverse-secret-broker'),
    broker_repository: broker.policy.includes('MatVerse-py/Gpt-project-bridge'),
    broker_models:
      broker.policy.includes('gpt-5.6-sol') && broker.policy.includes('gpt-6-astra'),
    broker_anonymous_blocked: broker.probe.includes('Blocked as expected'),
    broker_no_unexpected_authorization:
      !broker.probe.includes('Unexpected authorization'),
  };

  const passed = Object.values(hardChecks).every(Boolean);
  report = {
    schema: 'matverse.appdeploy-live-browser-probe.v1',
    scope: 'LIVE_PUBLIC_HTTPS_BROWSER_CROSS_INFRASTRUCTURE',
    source_runtime: 'GITHUB_HOSTED_RUNNER_CHROME',
    targets: {
      bridge: 'APPDEPLOY_EXTERNAL_RUNTIME',
      secret_plane: 'APPDEPLOY_SECRET_BROKER',
    },
    hard_checks: hardChecks,
    bridge_observed: bridge,
    secret_plane_observed: broker,
    live_browser_cross_infrastructure_pass: passed,
    classification: passed
      ? 'LIVE_BROWSER_CROSS_INFRASTRUCTURE_PASS'
      : 'HOLD',
    claim_boundary: {
      independent_administrative_witness: false,
      external_pass: 'HOLD',
      world_real_pass: 'HOLD',
      live_provider_model_execution: 'HOLD_NO_PROVIDER_SECRET',
      third_party_governance: 'NOT_PRESENT',
    },
  };
  report.result_hash = stableHash(report);
  fs.writeFileSync(
    'appdeploy-live-browser-probe-v1.json',
    JSON.stringify(report, null, 2) + '\n',
    'utf8',
  );
  console.log(JSON.stringify(report, null, 2));
  if (!passed) process.exitCode = 2;
} finally {
  await browser.close();
}
