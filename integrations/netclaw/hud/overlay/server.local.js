import express from 'express';
import { readModelConfig, runtimeEnvironment } from './src/fork-local/model-config.js';
import fs from 'fs';
import path from 'path';
import { execFile } from 'child_process';
import { fileURLToPath } from 'url';
import { hasOwner } from './modules/convergence/owner-access.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Borrowed-from-upstream identifiers that must be present in ctx.
const REQUIRED_CTX = [
  'ROOT',
  'broadcastWS',
  'callRagTool',
  'ragStartProgressPolling',
  'readText',
  'ownerContext',
];

function assertCtx(ctx) {
  const missing = REQUIRED_CTX.filter((k) => ctx?.[k] === undefined);
  if (missing.length) {
    throw new Error(
      `server.local.js: server.js no longer provides [${missing.join(', ')}]. `
      + 'An upstream pull likely renamed or removed them — update the ctx object '
      + 'at the registerLocalExtensions() call in server.js.',
    );
  }
}

export function register(app, ctx) {
  assertCtx(ctx);

  const {
    ROOT,
    broadcastWS,
    callRagTool,
    ragStartProgressPolling,
    readText,
  } = ctx;

  const paths = ['/api/tokens/summary', '/api/models', '/api/rag/ingest-url'];
  app.use(paths, (req, res, next) => {
    res.set('Cache-Control', 'no-store');
    if (!hasOwner(req, ctx.ownerContext)) return res.status(403).json({error: 'Local HUD owner required'});
    if (req.method === 'POST' && (req.get('Origin') !== 'http://' + req.get('Host') || !req.is('application/json'))) {
      return res.status(403).json({error: 'Same-origin JSON request required'});
    }
    next();
  });
  registerTokens(app, { readText, getLastChatUsage: ctx.getLastChatUsage });
  registerModels(app, ctx.ownerContext.installation.configPath);
  registerRag(app, {
    callRagTool, ragStartProgressPolling, broadcastWS,
  });

  registerFrontend(app);

  return {
    routes: [
      '/api/tokens/summary', 'GET /api/models',
      '/api/rag/ingest-url',
    ],
    servesFrontend: fs.existsSync(path.join(__dirname, 'dist', 'index.html')),
  };
}

function registerFrontend(app) {
  const distDir = path.join(__dirname, 'dist');
  const indexHtml = path.join(distDir, 'index.html');
  if (!fs.existsSync(indexHtml)) {
    console.warn('[local] dist/ not built — frontend not served. Run: npm run build');
    return;
  }
  app.use(express.static(distDir));
  app.get('*', (req, res, next) => {
    // Never let the SPA fallback mask a genuine 404 from the API surface.
    if (req.path.startsWith('/api/') || req.path === '/ws') return next();
    res.sendFile(indexHtml);
  });
}



// ───────────────────────────────────────────────────────────────────────────
// Token / cost summary strip.
// ───────────────────────────────────────────────────────────────────────────
function registerTokens(app, { readText, getLastChatUsage }) {
// NOTE: this stays null — see DELIBERATE OMISSION in the file header. Kept so
// the endpoint body below remains byte-identical to the pre-merge original.
// Backs the HUD footer strip. Source: openclaw-token-exporter scrape
// of ~/.openclaw/agents/*/sessions/*.jsonl (same counters as Grafana).
const TOKEN_EXPORTER_URL =
  process.env.TOKEN_EXPORTER_URL || 'http://127.0.0.1:9110/metrics';

let _lastChatUsage = null; // { input, output, total, model, at }

function parsePromCounters(text) {
  const byModel = new Map(); // model -> { input, output, cost, calls, provider }
  let totalIn = 0;
  let totalOut = 0;
  let totalCost = 0;
  let totalCalls = 0;
  for (const line of String(text || '').split('\n')) {
    if (!line || line.startsWith('#')) continue;
    const m = line.match(
      /^(netclaw_model_(?:input_tokens|output_tokens|cost_usd|calls)_total)\{([^}]*)\}\s+([0-9.eE+-]+)/,
    );
    if (!m) continue;
    const [, metric, labels, valStr] = m;
    const val = parseFloat(valStr);
    if (!Number.isFinite(val)) continue;
    const lab = {};
    for (const part of labels.split(',')) {
      const eq = part.indexOf('=');
      if (eq < 1) continue;
      lab[part.slice(0, eq)] = part.slice(eq + 1).replace(/^"|"$/g, '');
    }
    const model = lab.model || 'unknown';
    const provider = lab.provider || 'unknown';
    const row = byModel.get(model) || {
      model,
      provider,
      input: 0,
      output: 0,
      cost: 0,
      calls: 0,
    };
    if (metric.includes('input_tokens')) {
      row.input = val;
      totalIn += val;
    } else if (metric.includes('output_tokens')) {
      row.output = val;
      totalOut += val;
    } else if (metric.includes('cost_usd')) {
      row.cost = val;
      totalCost += val;
    } else if (metric.includes('calls_total')) {
      row.calls = val;
      totalCalls += val;
    }
    byModel.set(model, row);
  }
  // Prefer summing unique models once (input/output counted above may double-count
  // if we add every metric line — recompute from map)
  totalIn = 0;
  totalOut = 0;
  totalCost = 0;
  totalCalls = 0;
  const models = [...byModel.values()].sort((a, b) => b.input - a.input);
  for (const r of models) {
    totalIn += r.input || 0;
    totalOut += r.output || 0;
    totalCost += r.cost || 0;
    totalCalls += r.calls || 0;
  }
  return { models, totalIn, totalOut, totalCost, totalCalls };
}
app.get('/api/tokens/summary', async (req, res) => {
  let exporter = null;
  let exporterError = null;
  try {
    const r = await fetch(TOKEN_EXPORTER_URL, { signal: AbortSignal.timeout(2500) });
    if (!r.ok) throw new Error(`exporter HTTP ${r.status}`);
    const text = await r.text();
    exporter = parsePromCounters(text);
  } catch (err) {
    exporterError = err.message;
  }

  // NetClaw-owned token optimization flags (NOT openclaw.json — OpenClaw schema rejects unknown keys)
  let tokenOptimization = null;
  try {
    const optPath = path.join(
      process.env.HOME || '/root',
      '.openclaw',
      'netclaw-token-optimization.json',
    );
    tokenOptimization = JSON.parse(readText(optPath) || 'null');
  } catch {
    /* ignore */
  }

  const top = (exporter?.models || []).slice(0, 5).map((m) => ({
    model: m.model,
    provider: m.provider,
    input: Math.round(m.input),
    output: Math.round(m.output),
    calls: Math.round(m.calls),
  }));

  res.json({
    ok: !exporterError,
    exporterError,
    exporterUrl: TOKEN_EXPORTER_URL,
    lifetime: exporter
      ? {
          input: Math.round(exporter.totalIn),
          output: Math.round(exporter.totalOut),
          costUsd: Math.round(exporter.totalCost * 10000) / 10000,
          calls: Math.round(exporter.totalCalls),
        }
      : null,
    topModels: top,
    // FORK: read live from server.js rather than the always-null local below.
    lastTurn: getLastChatUsage ? getLastChatUsage() : _lastChatUsage,
    tokenOptimization: tokenOptimization
      ? {
          enabled: Boolean(tokenOptimization.enabled),
          footerDisplay: tokenOptimization.footerDisplay || null,
          gcfSerializationDefault: Boolean(tokenOptimization.gcfSerializationDefault),
        }
      : { enabled: false },
    timestamp: new Date().toISOString(),
  });
});
}

// ───────────────────────────────────────────────────────────────────────────
// Read-only model status for the bound OpenClaw installation.
// ───────────────────────────────────────────────────────────────────────────
function registerModels(app, configPath) {
  // Report only explicit model identifiers from the bound installation.
  const identifier = /^(?=.{1,256}$)[A-Za-z0-9][A-Za-z0-9_.:-]*(?:\/[A-Za-z0-9][A-Za-z0-9_.:-]*)+$/;
  const modelId = value => typeof value === 'string' && identifier.test(value) ? value : null;
  const primary = value => modelId(typeof value === 'string' ? value : value?.primary);

  app.get('/api/models', (_req, res) => {
    try {
      const config = readModelConfig(configPath);
      const env = runtimeEnvironment(configPath);
      const alert = config.agents?.entries?.alert ?? config.agents?.list?.find(agent => agent.id === 'alert');
      const mapping = config.hooks?.mappings?.find(item => item.match?.path === 'alert');
      res.json({
        selectionSupported: false,
        sot: {
          brain: modelId(env.NETCLAW_BRAIN_MODEL),
          alert: modelId(env.NETCLAW_ALERT_TRIAGE_MODEL),
          fallback: modelId(env.NETCLAW_ALERT_FALLBACK_MODEL),
        },
        live: {
          defaultsPrimary: primary(config.agents?.defaults?.model),
          alertAgent: primary(alert?.model),
          hookAlert: {model: modelId(mapping?.model)},
        },
      });
    } catch {
      res.status(503).json({error: 'Selected installation model settings are unavailable.'});
    }
  });
  app.all('/api/models', (_req, res) => {
    res.set('Allow', 'GET, HEAD').status(405).json({error: 'Global model settings are read-only.'});
  });
}

// ───────────────────────────────────────────────────────────────────────────
// RAG: URL ingestion + depth-1 same-site crawl (fork additions).
// ───────────────────────────────────────────────────────────────────────────
function registerRag(app, {
  callRagTool, ragStartProgressPolling, broadcastWS,
}) {
app.post('/api/rag/ingest-url', async (req, res) => {
  const url = String(req.body?.url || '').trim();
  const mode = typeof req.body?.mode === 'string' ? req.body.mode.toLowerCase() : req.body?.mode == null ? 'preview' : '';
  if (!url || !/^https?:\/\//i.test(url)) {
    return res.status(400).json({ error: 'url is required (http or https)' });
  }
  if (mode !== 'preview' && mode !== 'ingest') {
    return res.status(400).json({ error: "mode must be 'preview' or 'ingest'" });
  }
  const args = {
    url,
    mode,
    include_linked: Boolean(req.body?.include_linked),
    doc_type: req.body?.doc_type || 'other',
  };
  if (req.body?.scope_token) args.scope_token = req.body.scope_token;
  if (req.body?.title) args.title = req.body.title;
  // Self-signed local gear (UniFi :11443, pfSense, etc.)
  if (req.body?.verify_ssl === false || req.body?.insecure === true) {
    args.verify_ssl = false;
  } else if (req.body?.verify_ssl === true) {
    args.verify_ssl = true;
  }

  try {
    if (mode === 'ingest') {
      res.status(202).json({ status: 'pending', url, mode: 'ingest' });
      ragStartProgressPolling();
      try {
        const result = await callRagTool('rag_ingest_url', args, 900);
        if (result.success) {
          const pages = result.data?.pages || [];
          const firstOk = pages.find((p) => p?.success || p?.data?.document_id);
          const docId = firstOk?.data?.document_id || firstOk?.document_id || null;
          const title = firstOk?.data?.title || result.data?.title || url;
          broadcastWS('rag_progress', {
            document_id: docId,
            title,
            status: 'ready',
            error: null,
            pages_ingested: result.data?.ingested,
          });
        } else {
          broadcastWS('rag_progress', {
            document_id: null,
            title: url,
            status: 'error',
            error: result.error?.message || 'URL ingest failed',
          });
        }
      } catch (ingestErr) {
        broadcastWS('rag_progress', {
          document_id: null,
          title: url,
          status: 'error',
          error: ingestErr.message,
        });
      }
      broadcastWS('rag_update', { documents_changed: true });
      return;
    }

    // preview — synchronous so the UI can show linked pages + scope_token
    const result = await callRagTool('rag_ingest_url', args, 120);
    if (!result.success) {
      return res.status(500).json(result.error || { error: 'preview failed' });
    }
    res.json(result.data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

}
