'use strict';

(() => {
  const CORE_API = '/core-api';
  const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);
  const state = {
    principal: '',
    secret: '',
    agents: [],
    candidates: [],
    selectedAgent: null,
    activeJob: null,
    registrySummary: null,
  };
  const $ = (selector) => document.querySelector(selector);

  function notify(message, error = false) {
    const node = $('#flash');
    if (!node) return;
    node.textContent = String(message);
    node.className = `flash${error ? ' error' : ''}`;
    node.hidden = false;
    window.clearTimeout(notify.timer);
    notify.timer = window.setTimeout(() => { node.hidden = true; }, 7000);
  }

  function setText(selector, value) {
    const node = $(selector);
    if (node) node.textContent = String(value ?? '');
  }

  function hex(bytes) {
    return [...new Uint8Array(bytes)].map((value) => value.toString(16).padStart(2, '0')).join('');
  }

  async function sha256Hex(value) {
    const bytes = new TextEncoder().encode(value);
    return hex(await crypto.subtle.digest('SHA-256', bytes));
  }

  function randomNonce() {
    return hex(crypto.getRandomValues(new Uint8Array(16)));
  }

  async function signedHeaders(method, path, bodyText) {
    if (!state.principal || !state.secret) throw new Error('Conecte um principal HMAC primeiro.');
    if (!crypto?.subtle) throw new Error('WebCrypto indisponível. Use localhost em um navegador moderno.');
    const timestamp = String(Math.floor(Date.now() / 1000));
    const nonce = randomNonce();
    const contentHash = await sha256Hex(bodyText);
    const canonical = [method.toUpperCase(), path, timestamp, nonce, contentHash].join('\n');
    const key = await crypto.subtle.importKey(
      'raw',
      new TextEncoder().encode(state.secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign'],
    );
    const signature = hex(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(canonical)));
    return {
      'X-MatVerse-Principal': state.principal,
      'X-MatVerse-Timestamp': timestamp,
      'X-MatVerse-Nonce': nonce,
      'X-MatVerse-Content-SHA256': contentHash,
      'X-MatVerse-Signature': signature,
    };
  }

  async function coreRequest(path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    const bodyText = options.body === undefined ? '' : JSON.stringify(options.body);
    const signedPath = path.split('?')[0];
    const headers = await signedHeaders(method, signedPath, bodyText);
    headers.accept = 'application/json';
    if (bodyText) headers['content-type'] = 'application/json';
    const response = await fetch(`${CORE_API}${path}`, {
      method,
      headers,
      body: bodyText || undefined,
      cache: 'no-store',
      credentials: 'same-origin',
    });
    const raw = await response.text();
    let data = null;
    try { data = raw ? JSON.parse(raw) : null; } catch { data = { raw }; }
    if (!response.ok) {
      const detail = data && typeof data === 'object' ? (data.detail || data.message || raw) : raw;
      throw new Error(`${response.status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
    }
    return data;
  }

  async function publicRequest(path) {
    const response = await fetch(`${CORE_API}${path}`, { cache: 'no-store', credentials: 'same-origin' });
    const raw = await response.text();
    let data = null;
    try { data = raw ? JSON.parse(raw) : null; } catch { data = { raw }; }
    if (!response.ok) throw new Error(`${response.status}: ${raw}`);
    return data;
  }

  function requireLocalHost() {
    if (!LOCAL_HOSTS.has(location.hostname.toLowerCase())) {
      throw new Error('A sessão HMAC no browser é local-only. Para operação remota, implemente OIDC/BFF server-side; não envie o segredo HMAC ao browser remoto.');
    }
  }

  function addCell(row, value) {
    const cell = document.createElement('td');
    cell.textContent = String(value ?? '');
    row.appendChild(cell);
  }

  function renderAgents() {
    const host = $('#core-agent-catalog');
    if (!host) return;
    host.replaceChildren();
    if (!state.agents.length) {
      const note = document.createElement('p');
      note.textContent = 'Nenhum agente configurado foi retornado. Configure os modelos e credenciais no ambiente do runtime.';
      host.appendChild(note);
      return;
    }
    const table = document.createElement('table');
    const head = document.createElement('thead');
    const header = document.createElement('tr');
    ['Agente', 'Provedor', 'Modelo / perfil', 'Configuração', 'Conexão live', 'Transporte'].forEach((name) => {
      const th = document.createElement('th'); th.textContent = name; header.appendChild(th);
    });
    head.appendChild(header); table.appendChild(head);
    const body = document.createElement('tbody');
    for (const agent of state.agents) {
      const row = document.createElement('tr');
      addCell(row, agent.agent_id);
      addCell(row, agent.provider);
      addCell(row, agent.model || 'não configurado');
      addCell(row, agent.status || (agent.configured ? 'CONFIGURED_UNVERIFIED' : 'BLOCKED_CONFIGURATION'));
      addCell(row, agent.connection_verified === true ? 'VERIFIED' : 'UNVERIFIED');
      addCell(row, agent.transport || '—');
      body.appendChild(row);
    }
    table.appendChild(body);
    host.appendChild(table);
  }

  function renderCandidates() {
    const host = $('#core-selection');
    const select = $('#core-target-agent');
    if (!host || !select) return;
    host.replaceChildren();
    select.replaceChildren();
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = state.candidates.length ? 'Selecione um agente' : 'Nenhuma capacidade admissível';
    select.appendChild(empty);
    for (const candidate of state.candidates) {
      const option = document.createElement('option');
      option.value = candidate.agent_id;
      option.textContent = `${candidate.agent_id} · ${candidate.provider} · ${candidate.model || 'sem modelo'}`;
      select.appendChild(option);
      const card = document.createElement('article');
      card.className = 'card';
      const title = document.createElement('strong');
      title.textContent = `${candidate.agent_id} — ${candidate.provider}`;
      const meta = document.createElement('p');
      meta.textContent = `Modelo: ${candidate.model || 'não configurado'} · ${candidate.status || 'estado não informado'} · conexão: ${candidate.connection_verified ? 'verificada' : 'não verificada'}`;
      card.append(title, meta);
      host.appendChild(card);
    }
    if (state.candidates.length) {
      select.value = state.candidates[0].agent_id;
      state.selectedAgent = state.candidates[0];
    } else {
      state.selectedAgent = null;
    }
  }

  async function refreshHealth() {
    try {
      const [health, ready] = await Promise.all([publicRequest('/health'), publicRequest('/ready')]);
      setText('#core-health', JSON.stringify({ health, ready }, null, 2));
    } catch (error) {
      setText('#core-health', `Runtime indisponível: ${error.message}`);
    }
  }

  async function refreshAuthenticatedState() {
    if (!state.secret) {
      setText('#core-auth-status', 'Desconectado. Informe um principal provisionado.');
      return;
    }
    const [agentsResult, summaryResult, snapshotResult] = await Promise.allSettled([
      coreRequest('/agents'),
      coreRequest('/model-registry/summary'),
      coreRequest('/model-registry/adapter-snapshot'),
    ]);
    if (agentsResult.status === 'fulfilled') {
      state.agents = agentsResult.value.agents || agentsResult.value;
      if (!Array.isArray(state.agents)) state.agents = [];
      renderAgents();
    }
    if (summaryResult.status === 'fulfilled') {
      state.registrySummary = summaryResult.value;
      setText('#core-auth-status', `Conectado como ${state.principal}. Registry: ${summaryResult.value.entries_total ?? '—'} registros.`);
    } else {
      setText('#core-auth-status', `Autenticado como ${state.principal}; resumo do registry indisponível: ${summaryResult.reason.message}`);
    }
    if (snapshotResult.status === 'fulfilled') {
      const snap = snapshotResult.value;
      setText('#core-health', JSON.stringify({
        service: 'MATVERSE_REAL_v1',
        registry_entries: state.registrySummary?.entries_total ?? null,
        adapters: snap.adapters_total ?? null,
        live_connections_verified: snap.connection_verified_total ?? null,
        registry_mismatches: snap.registry_mismatch_total ?? null,
      }, null, 2));
    }
    if (agentsResult.status === 'rejected') {
      notify(`Não foi possível ler o catálogo: ${agentsResult.reason.message}`, true);
    }
  }

  async function connect(event) {
    event.preventDefault();
    try {
      requireLocalHost();
      const principal = $('#core-principal').value.trim();
      const secret = $('#core-secret').value;
      if (!principal || !secret) throw new Error('Principal e segredo são obrigatórios.');
      state.principal = principal;
      state.secret = secret;
      $('#core-secret').value = '';
      await coreRequest('/agents');
      setText('#core-auth-status', `Autenticado como ${principal}. Segredo mantido apenas em memória.`);
      await refreshAuthenticatedState();
      notify('Bridge conectado.');
    } catch (error) {
      state.principal = '';
      state.secret = '';
      setText('#core-auth-status', error.message);
      notify(error.message, true);
    }
  }

  function disconnect() {
    state.principal = '';
    state.secret = '';
    state.agents = [];
    state.candidates = [];
    state.activeJob = null;
    renderAgents();
    renderCandidates();
    setText('#core-auth-status', 'Desconectado. Segredo removido da memória desta página.');
    notify('Sessão encerrada.');
  }

  async function seedRegistry() {
    try {
      const result = await coreRequest('/model-registry/seed-corpus', { method: 'POST', body: {} });
      notify(`Registry: ${result.decision || 'resultado'} · ${result.entries_total ?? '—'} registros.`);
      await refreshAuthenticatedState();
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function selectCapabilities(event) {
    event.preventDefault();
    try {
      const preference = $('#core-preference').value.split(',').map((item) => item.trim()).filter(Boolean);
      const result = await coreRequest('/model-registry/select', {
        method: 'POST',
        body: {
          capability: $('#core-capability').value,
          modality: $('#core-modality').value,
          provider_preference: preference,
          require_configured: $('#core-require-configured').checked,
          require_connection_verified: $('#core-require-verified').checked,
          allow_external_handoff: $('#core-allow-external').checked,
          limit: 12,
        },
      });
      state.candidates = (result.candidates || []).filter((item) => item.agent_id !== 'base44');
      renderCandidates();
      const base44 = (result.candidates || []).some((item) => item.agent_id === 'base44');
      if (base44) notify('Base44 está registrado como plataforma MCP estruturada; despacho genérico em texto foi deliberadamente excluído.');
      if (!state.candidates.length) notify('Nenhum agente elegível. Verifique modelo, credenciais, role e política de handoff.', true);
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function createFrozenContracts(task) {
    const taskHash = await sha256Hex(task);
    const artifacts = {
      ontology: {
        schema: 'matverse.observable-task.v1',
        state_boundary: 'explicit-observable-only',
        forbidden_transfer: ['hidden_reasoning', 'private_memory', 'internal_prompts', 'credentials'],
      },
      policy: {
        schema: 'matverse.provider-exposure.v1',
        provider_exposure: 'explicit-user-authorized',
        hidden_reasoning_transfer: false,
        private_memory_transfer: false,
        credential_transfer: false,
      },
      task: { schema: 'matverse.task.v1', task_sha256: taskHash, task_type: 'user-authorized-agent-task' },
      rubric: {
        schema: 'matverse.output-rubric.v1',
        required_public_fields: ['status', 'output_text', 'limitations'],
        allowed_status: ['COMPLETE', 'PARTIAL', 'RUNNING', 'WAITING', 'WAITING_EXTERNAL', 'UNKNOWN', 'FAILED'],
      },
      memory_policy: {
        schema: 'matverse.memory-policy.v1',
        persistence: 'explicit-only',
        provider_private_state: 'not-transferable',
        raw_source_context: 'not-implicitly-retained',
      },
    };
    const hashes = {};
    for (const [kind, content] of Object.entries(artifacts)) {
      const created = await coreRequest('/registry/contracts', {
        method: 'POST',
        body: { kind, version: 'cognitive-relay-v1', content },
      });
      hashes[`${kind}_hash`] = created.artifact_hash;
    }
    return hashes;
  }

  function outputCard(title, value) {
    const card = document.createElement('article');
    card.className = 'card';
    const heading = document.createElement('strong');
    heading.textContent = title;
    const pre = document.createElement('pre');
    pre.className = 'code';
    pre.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    card.append(heading, pre);
    return card;
  }

  async function dispatchTask(event) {
    event.preventDefault();
    const host = $('#core-job');
    host.replaceChildren();
    try {
      if (!state.secret) throw new Error('Conecte o Bridge antes de despachar.');
      const agentId = $('#core-target-agent').value;
      const agent = state.candidates.find((item) => item.agent_id === agentId);
      if (!agent) throw new Error('Selecione um agente retornado pela shortlist.');
      if (agent.agent_id === 'base44') throw new Error('Base44 exige um envelope de ferramenta MCP estruturado; não aceita esta tarefa genérica.');
      const task = $('#core-task').value.trim();
      if (!task) throw new Error('Descreva a tarefa.');
      const sourceText = $('#core-source-text').value;
      const sourceHash = sourceText ? await sha256Hex(sourceText) : '';

      const hashes = await createFrozenContracts(task);
      const session = await coreRequest('/model-bridge/sessions', {
        method: 'POST',
        body: {
          participants: [
            { participant_id: state.principal, provider: 'matverse', model: 'cognitive-relay-v1', endpoint_class: 'LOCAL' },
            { participant_id: agent.agent_id, provider: agent.provider, model: agent.model, endpoint_class: agent.provider === 'local' ? 'LOCAL' : 'REMOTE' },
          ],
          contract: hashes,
        },
      });
      const payload = {
        kind: 'agent_task',
        public_summary: task.slice(0, 1000),
        state: {
          task,
          source: {
            tab_id: $('#core-source-tab').value.trim() || null,
            url: $('#core-source-url').value.trim() || null,
            snapshot_sha256: sourceHash || null,
          },
          requested_public_output: ['result', 'limitations', 'status'],
        },
      };
      const handoff = await coreRequest(`/model-bridge/sessions/${encodeURIComponent(session.session_id)}/handoffs`, {
        method: 'POST',
        body: {
          from_participant: state.principal,
          to_participant: agent.agent_id,
          expected_contract_hash: session.contract_hash,
          payload,
          human: {
            consent: true,
            purpose: 'User-authorized task dispatch through MatVerse Cognitive Relay',
            sensitivity: 'normal',
            third_party: false,
            third_party_consent: false,
            serialize_human: false,
          },
        },
      });
      if (handoff.decision !== 'PASS' || handoff.stored !== true) throw new Error(`Handoff não admitido: ${handoff.decision || 'UNKNOWN'} · ${handoff.reason || ''}`);
      const handoffId = handoff.handoff_id || handoff.handoff?.handoff_id;
      if (!handoffId) throw new Error('O Bridge não retornou handoff_id.');
      const invoked = await coreRequest(`/agents/${encodeURIComponent(agent.agent_id)}/invoke`, {
        method: 'POST',
        body: {
          session_id: session.session_id,
          handoff_id: handoffId,
          expected_contract_hash: session.contract_hash,
          human: {
            consent: true,
            purpose: 'User-authorized task dispatch through MatVerse Cognitive Relay',
            sensitivity: 'normal',
            third_party: false,
            third_party_consent: false,
            serialize_human: false,
          },
        },
      });
      state.activeJob = {
        agent_id: agent.agent_id,
        job_id: invoked.job_id || invoked.job?.job_id || null,
        session_id: session.session_id,
        handoff_id: handoffId,
        result: invoked,
      };
      host.append(
        outputCard('Sessão congelada', { session_id: session.session_id, contract_hash: session.contract_hash }),
        outputCard('Handoff', handoff),
        outputCard('Despacho', invoked),
      );
      if (state.activeJob.job_id) {
        const poll = document.createElement('button');
        poll.type = 'button';
        poll.className = 'button';
        poll.textContent = agent.agent_id === 'manus' ? 'Consultar tarefa Manus' : 'Atualizar estado do job';
        poll.addEventListener('click', pollActiveJob);
        host.appendChild(poll);
      }
      notify(`Despacho enviado para ${agent.agent_id}. Estado: ${invoked.status || invoked.job?.status || 'UNKNOWN'}.`);
    } catch (error) {
      host.appendChild(outputCard('Falha no despacho', error.message));
      notify(error.message, true);
    }
  }

  async function pollActiveJob() {
    if (!state.activeJob?.job_id) return;
    try {
      const { agent_id: agentId, job_id: jobId } = state.activeJob;
      const path = agentId === 'manus'
        ? `/agents/manus/jobs/${encodeURIComponent(jobId)}/poll`
        : `/agents/${encodeURIComponent(agentId)}/jobs/${encodeURIComponent(jobId)}`;
      const result = await coreRequest(path, { method: agentId === 'manus' ? 'POST' : 'GET' });
      state.activeJob.result = result;
      const host = $('#core-job');
      host.appendChild(outputCard('Estado atualizado', result));
      notify(`Job ${jobId}: ${result.status || result.job?.status || 'estado atualizado'}.`);
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function readLedger() {
    try {
      const result = await coreRequest('/ledger');
      setText('#core-ledger-output', JSON.stringify(result, null, 2));
    } catch (error) {
      setText('#core-ledger-output', error.message);
      notify(error.message, true);
    }
  }

  async function readReplay() {
    try {
      const result = await coreRequest('/replay');
      setText('#core-ledger-output', JSON.stringify(result, null, 2));
    } catch (error) {
      setText('#core-ledger-output', error.message);
      notify(error.message, true);
    }
  }

  async function searchMemory(event) {
    event.preventDefault();
    try {
      const result = await coreRequest('/memory/search', {
        method: 'POST',
        body: {
          query: $('#core-memory-query').value.trim(),
          include_text: $('#core-memory-text').checked,
          limit: 8,
        },
      });
      setText('#core-memory-output', JSON.stringify(result, null, 2));
    } catch (error) {
      setText('#core-memory-output', error.message);
      notify(error.message, true);
    }
  }

  function init() {
    const form = $('#core-auth-form');
    if (!form) return;
    form.addEventListener('submit', connect);
    $('#core-disconnect').addEventListener('click', disconnect);
    $('#core-refresh').addEventListener('click', async () => {
      await refreshHealth();
      await refreshAuthenticatedState();
    });
    $('#core-seed-registry')?.addEventListener('click', seedRegistry);
    $('#core-select-form').addEventListener('submit', selectCapabilities);
    $('#core-target-agent').addEventListener('change', (event) => {
      state.selectedAgent = state.candidates.find((item) => item.agent_id === event.target.value) || null;
    });
    $('#core-dispatch-form').addEventListener('submit', dispatchTask);
    $('#core-read-ledger').addEventListener('click', readLedger);
    $('#core-read-replay').addEventListener('click', readReplay);
    $('#core-memory-form').addEventListener('submit', searchMemory);
    window.addEventListener('hashchange', () => {
      if (location.hash === '#capability-bridge') {
        refreshHealth();
        if (state.secret) refreshAuthenticatedState();
      }
    });
    window.addEventListener('pagehide', () => { state.secret = ''; state.principal = ''; });
    refreshHealth();
    if (location.hash === '#capability-bridge') refreshAuthenticatedState();
    if (!LOCAL_HOSTS.has(location.hostname.toLowerCase())) {
      setText('#core-auth-status', 'Modo remoto desabilitado: a autenticação HMAC no browser é local-only.');
    }
  }

  init();
})();
