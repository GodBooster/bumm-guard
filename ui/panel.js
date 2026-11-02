/* BUMM Guard ABI interaction panel.
 *
 * Classic script (no ES-module syntax) so that it also works from file://, where
 * browsers refuse module scripts. In Node it is loaded with require(); the pure
 * functions below are exported through module.exports. DOM code only runs when
 * `document` exists.
 */

// ---------------------------------------------------------------- pure logic

const NETWORKS = {
  testnet: {
    rpc: 'https://rpc.testnet.fastnear.com',
    wallet: 'https://testnet.mynearwallet.com',
    explorer: 'https://testnet.nearblocks.io',
  },
  mainnet: {
    rpc: 'https://rpc.mainnet.fastnear.com',
    wallet: 'https://app.mynearwallet.com',
    explorer: 'https://nearblocks.io',
  },
};

const INT_RANGES = {
  uint8: [0n, 255n],
  uint16: [0n, 65535n],
  uint32: [0n, 4294967295n],
  uint64: [0n, 18446744073709551615n],
  uint128: [0n, 2n ** 128n - 1n],
  int8: [-128n, 127n],
  int16: [-32768n, 32767n],
  int32: [-2147483648n, 2147483647n],
  int64: [-(2n ** 63n), 2n ** 63n - 1n],
  int128: [-(2n ** 127n), 2n ** 127n - 1n],
};
const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);
const NUMERIC_STRING_DEFS = ['U64', 'U128', 'I64', 'I128'];
const SIMPLE_KINDS = ['string', 'integer', 'boolean', 'account_id'];
const ACCOUNT_ID_RE = /^(([a-z\d]+[-_])*[a-z\d]+\.)*([a-z\d]+[-_])*[a-z\d]+$/;

function isAccountId(s) {
  return typeof s === 'string' && s.length >= 2 && s.length <= 64 && ACCOUNT_ID_RE.test(s);
}

/** Parse a near-abi document into the shape the panel renders. Throws on malformed input. */
function parseAbi(abi) {
  if (!abi || typeof abi !== 'object') throw new Error('ABI is not a JSON object');
  const body = abi.body;
  if (!body || !Array.isArray(body.functions)) {
    throw new Error('not a near-abi document: body.functions[] is missing');
  }
  const root = body.root_schema || {};
  const definitions = root.definitions || root.$defs || {};
  const functions = body.functions.map((f, i) => {
    if (!f || typeof f.name !== 'string') throw new Error(`body.functions[${i}] has no name`);
    if (f.kind !== 'view' && f.kind !== 'call') {
      throw new Error(`function ${f.name}: kind must be "view" or "call", got ${JSON.stringify(f.kind)}`);
    }
    const params = f.params || { serialization_type: 'json', args: [] };
    const serialization = params.serialization_type || 'json';
    const args = serialization === 'json'
      ? (params.args || []).map((a) => ({ name: a.name, schema: a.type_schema }))
      : [];
    return {
      name: f.name,
      doc: f.doc || null,
      kind: f.kind,
      modifiers: Array.isArray(f.modifiers) ? f.modifiers.slice() : [],
      serialization,
      args,
      result: f.result ? f.result.type_schema : null,
    };
  });
  return { functions, definitions, metadata: abi.metadata || {}, schemaVersion: abi.schema_version || null };
}

function refName(ref) {
  return String(ref).split('/').pop();
}

/** Resolve one level of $ref / single-element allOf. Returns {schema, name} or {error}. */
function resolveOnce(schema, defs) {
  let s = schema;
  let name = null;
  if (s && typeof s === 'object' && Array.isArray(s.allOf) && s.allOf.length === 1 && !s.type) {
    s = s.allOf[0];
  }
  if (s && typeof s === 'object' && typeof s.$ref === 'string') {
    name = refName(s.$ref);
    const target = (defs || {})[name];
    if (target === undefined) return { error: `unresolved $ref ${s.$ref}` };
    if (target && typeof target === 'object' && (target.$ref || target.allOf)) {
      return { error: `$ref ${s.$ref} points to another $ref (only one level is followed)` };
    }
    s = target;
  }
  return { schema: s, name };
}

function unsupported(reason, extra) {
  return Object.assign({ kind: 'json', unsupportedReason: reason }, extra || {});
}

/**
 * Map a JSON schema to a form field description (FORMATS section 5).
 * kind: string | integer | boolean | account_id | array | json
 * `json` always carries unsupportedReason.
 */
function fieldForSchema(schema, definitions) {
  const defs = definitions || {};
  if (schema === true || schema === undefined || schema === null) {
    return unsupported('schema accepts any value');
  }
  if (typeof schema !== 'object') return unsupported('schema is not an object');

  let optional = false;
  let cur = schema;
  // Option<T> as anyOf/oneOf [T, null]
  const union = cur.anyOf || cur.oneOf;
  if (Array.isArray(union) && !cur.type && !cur.$ref) {
    const nonNull = union.filter((u) => !(u && u.type === 'null'));
    if (nonNull.length === 1 && nonNull.length < union.length) {
      cur = nonNull[0];
      optional = true;
    } else {
      return unsupported('anyOf/oneOf is not supported');
    }
  }

  const r = resolveOnce(cur, defs);
  if (r.error) return unsupported(r.error);
  const s = r.schema;
  const defName = r.name;
  if (s === true || !s || typeof s !== 'object') return unsupported('schema accepts any value');

  let type = s.type;
  if (Array.isArray(type)) {
    const nonNull = type.filter((t) => t !== 'null');
    if (nonNull.length === 1) {
      type = nonNull[0];
      if (nonNull.length < s.type.length) optional = true;
    } else {
      return unsupported(`type ${JSON.stringify(s.type)} is not supported`);
    }
  }
  const base = { optional, definition: defName };

  if (type === 'string') {
    if (defName === 'AccountId' || s.title === 'AccountId' || s.format === 'account_id' || s.format === 'AccountId') {
      return Object.assign({ kind: 'account_id' }, base);
    }
    if (Array.isArray(s.enum) && s.enum.length && s.enum.every((v) => typeof v === 'string')) {
      return Object.assign({ kind: 'string', enum: s.enum.slice() }, base);
    }
    if (NUMERIC_STRING_DEFS.includes(defName) || NUMERIC_STRING_DEFS.includes(s.title)) {
      return Object.assign({ kind: 'string', numeric: true }, base);
    }
    return Object.assign({ kind: 'string' }, base);
  }
  if (type === 'integer') {
    let [min, max] = INT_RANGES[s.format] || [null, null];
    if (typeof s.minimum === 'number') {
      const m = BigInt(Math.trunc(s.minimum));
      if (min === null || m > min) min = m;
    }
    if (typeof s.maximum === 'number') {
      const m = BigInt(Math.trunc(s.maximum));
      if (max === null || m < max) max = m;
    }
    return Object.assign({ kind: 'integer', format: s.format || null, min, max }, base);
  }
  if (type === 'boolean') return Object.assign({ kind: 'boolean' }, base);
  if (type === 'array') {
    if (!s.items || Array.isArray(s.items)) {
      return unsupported('tuple or untyped array is not supported', base);
    }
    const item = fieldForSchema(s.items, defs);
    if (!SIMPLE_KINDS.includes(item.kind)) {
      return unsupported(`array of ${item.unsupportedReason ? 'unsupported items (' + item.unsupportedReason + ')' : item.kind} is not supported`, base);
    }
    return Object.assign({ kind: 'array', itemKind: item.kind, item }, base);
  }
  return unsupported(`schema type ${JSON.stringify(type === undefined ? null : type)} is not supported`, base);
}

/** Field descriptions for a parsed function: [{name, schema, ...fieldForSchema}] */
function buildFields(fn, definitions) {
  return fn.args.map((a) => Object.assign({ name: a.name, schema: a.schema }, fieldForSchema(a.schema, definitions)));
}

function splitList(v) {
  if (Array.isArray(v)) return v.map((x) => String(x).trim()).filter((x) => x !== '');
  return String(v == null ? '' : v).split(/[\n,]/).map((x) => x.trim()).filter((x) => x !== '');
}

function encodeScalar(kind, field, raw) {
  // returns {value} or {error}
  if (kind === 'boolean') {
    if (raw === true || raw === 'true') return { value: true };
    if (raw === false || raw === 'false') return { value: false };
    return { error: 'must be true or false' };
  }
  const text = typeof raw === 'string' ? raw.trim() : String(raw);
  if (kind === 'account_id') {
    if (!isAccountId(text)) return { error: `"${text}" is not a valid NEAR account id (2-64 chars: a-z 0-9 - _ .)` };
    return { value: text };
  }
  if (kind === 'integer') {
    if (!/^-?\d+$/.test(text)) return { error: `"${text}" is not an integer` };
    const n = BigInt(text);
    if (field.min != null && n < field.min) return { error: `${text} is below the minimum ${field.min}` };
    if (field.max != null && n > field.max) return { error: `${text} is above the maximum ${field.max}` };
    if (n > MAX_SAFE || n < -MAX_SAFE) {
      return { error: `${text} exceeds the JavaScript safe integer range; the contract should take U64/U128 (string) for values this large` };
    }
    return { value: Number(n) };
  }
  // string
  if (field.enum && !field.enum.includes(text)) return { error: `must be one of: ${field.enum.join(', ')}` };
  if (field.numeric && !/^-?\d+$/.test(text)) return { error: `"${text}" is not a decimal integer string` };
  return { value: typeof raw === 'string' ? raw : text };
}

/**
 * Validate and encode form values into a JSON args object.
 * fields: output of buildFields; values: {name: string | boolean | string[]}.
 * Returns {args, errors: [{name, message}]}.
 */
function encodeArgs(fields, values) {
  const args = {};
  const errors = [];
  const vals = values || {};
  for (const f of fields) {
    const raw = vals[f.name];
    const empty = raw === undefined || raw === null || (typeof raw === 'string' && raw.trim() === '');
    const err = (message) => errors.push({ name: f.name, message });

    if (f.kind === 'boolean') {
      if (empty && f.optional) continue;
      const r = encodeScalar('boolean', f, empty ? false : raw);
      if (r.error) err(r.error); else args[f.name] = r.value;
      continue;
    }
    if (f.kind === 'array') {
      if (empty && f.optional) continue;
      const items = splitList(empty ? [] : raw);
      const out = [];
      let bad = false;
      items.forEach((it, i) => {
        const r = encodeScalar(f.itemKind, f.item || {}, it);
        if (r.error) { err(`item ${i + 1}: ${r.error}`); bad = true; } else out.push(r.value);
      });
      if (!bad) args[f.name] = out;
      continue;
    }
    if (f.kind === 'json') {
      if (empty) {
        if (f.optional) continue;
        err('required (enter JSON)');
        continue;
      }
      if (typeof raw !== 'string') { args[f.name] = raw; continue; }
      try { args[f.name] = JSON.parse(raw); } catch (e) { err(`invalid JSON: ${e.message}`); }
      continue;
    }
    // string, integer, account_id
    if (empty) {
      if (f.optional) continue;
      err('required');
      continue;
    }
    const r = encodeScalar(f.kind, f, raw);
    if (r.error) err(r.error); else args[f.name] = r.value;
  }
  return { args, errors };
}

/** "1.5" NEAR -> "1500000000000000000000000" yocto (string). Throws on bad input. */
function parseNearToYocto(text) {
  const t = String(text == null ? '' : text).trim() || '0';
  const m = /^(\d+)(?:\.(\d{1,24}))?$/.exec(t);
  if (!m) throw new Error(`deposit "${t}" is not a NEAR amount (decimal, up to 24 digits after the point)`);
  const frac = (m[2] || '').padEnd(24, '0');
  return (BigInt(m[1]) * 10n ** 24n + BigInt(frac)).toString();
}

/** "30" Tgas -> "30000000000000" gas units (string). Throws on bad input. */
function parseTgas(text) {
  const t = String(text == null ? '' : text).trim() || '30';
  const m = /^(\d+)(?:\.(\d{1,12}))?$/.exec(t);
  if (!m) throw new Error(`gas "${t}" is not a Tgas amount (decimal, up to 12 digits after the point)`);
  const frac = (m[2] || '').padEnd(12, '0');
  const g = BigInt(m[1]) * 10n ** 12n + BigInt(frac);
  if (g <= 0n) throw new Error('gas must be greater than 0');
  if (g > 300n * 10n ** 12n) throw new Error('gas above 300 Tgas is refused by the protocol');
  return g.toString();
}

function toBase64Utf8(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

/** JSON-RPC body for a view call. Exported separately so tests can assert it. */
function buildViewRequest(contractId, method, args) {
  return {
    jsonrpc: '2.0',
    id: 'bumm-guard-panel',
    method: 'query',
    params: {
      request_type: 'call_function',
      finality: 'final',
      account_id: contractId,
      method_name: method,
      args_base64: toBase64Utf8(JSON.stringify(args || {})),
    },
  };
}

/** Free view call via JSON-RPC `query`/`call_function`. Resolves to the parsed JSON result. */
async function viewCall(rpcUrl, contractId, method, args, fetchImpl) {
  const doFetch = fetchImpl || (typeof fetch !== 'undefined' ? fetch : null);
  if (!doFetch) throw new Error('fetch is not available');
  const resp = await doFetch(rpcUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(buildViewRequest(contractId, method, args)),
  });
  if (!resp.ok) throw new Error(`RPC HTTP ${resp.status}`);
  const json = await resp.json();
  if (json.error) {
    const e = json.error;
    const detail = (e.data && typeof e.data === 'string') ? e.data : (e.cause && e.cause.name) || '';
    throw new Error(`RPC error: ${e.message || e.name || 'unknown'}${detail ? ' (' + detail + ')' : ''}`);
  }
  const res = json.result;
  if (!res) throw new Error('RPC returned no result');
  if (res.error) throw new Error(`contract error: ${res.error}`);
  const text = new TextDecoder().decode(Uint8Array.from(res.result || []));
  if (text === '') return null;
  try { return JSON.parse(text); } catch (_) { return text; }
}

// ------------------------------------------------------------- DOM builders

function describeSchema(schema) {
  try { return JSON.stringify(schema, null, 2); } catch (_) { return String(schema); }
}

function typeHint(f) {
  if (f.kind === 'array') return `array of ${f.itemKind}`;
  if (f.kind === 'integer' && f.format) return `integer (${f.format})`;
  if (f.kind === 'json') return 'JSON';
  return f.kind;
}

/**
 * Build the card for one function. handlers:
 *   getConn() -> {rpcUrl, contractId, network}
 *   onView(name, args) -> Promise<json>
 *   onCall(name, args, {deposit, gas}) -> Promise<string | void>  (deposit yocto, gas units)
 */
function renderFunction(fn, definitions, handlers, doc) {
  const d = doc || document;
  const el = (tag, cls, text) => {
    const n = d.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const isView = fn.kind === 'view';
  const isPayable = fn.modifiers.includes('payable');
  const isPrivate = fn.modifiers.includes('private');
  const isInit = fn.modifiers.includes('init');
  const borsh = fn.serialization !== 'json';
  const disabled = isPrivate || borsh;

  const card = el('section', 'card ' + fn.kind + (isPayable ? ' payable' : '') + (isPrivate ? ' private' : ''));
  card.dataset.function = fn.name;
  const head = el('header', 'card-head');
  head.appendChild(el('h3', 'fn-name', fn.name));
  head.appendChild(el('span', 'badge ' + fn.kind, fn.kind));
  for (const m of fn.modifiers) head.appendChild(el('span', 'badge mod-' + m, m));
  card.appendChild(head);
  if (fn.doc) card.appendChild(el('p', 'doc', fn.doc));
  if (isInit) card.appendChild(el('p', 'note', 'init method: call it once, right after deploy, as the contract account. A second call fails.'));
  if (isPrivate) card.appendChild(el('p', 'note warn', 'private method: only the contract account itself can call it. Listed, not callable from this panel.'));
  if (borsh) card.appendChild(el('p', 'note warn', 'Borsh-serialized arguments are not supported by this panel.'));

  const fields = buildFields(fn, definitions);
  const inputs = {};
  const form = el('div', 'fields');
  for (const f of fields) {
    const row = el('div', 'field');
    const label = el('label', 'field-label');
    label.appendChild(el('span', 'field-name', f.name));
    label.appendChild(el('span', 'field-type', typeHint(f) + (f.optional ? ', optional' : '')));
    row.appendChild(label);
    let input;
    if (f.kind === 'boolean') {
      input = el('select');
      const opts = f.optional ? [['', '(omit)'], ['false', 'false'], ['true', 'true']] : [['false', 'false'], ['true', 'true']];
      for (const [v, t] of opts) { const o = el('option', '', t); o.value = v; input.appendChild(o); }
    } else if (f.kind === 'string' && f.enum) {
      input = el('select');
      for (const v of f.enum) { const o = el('option', '', v); o.value = v; input.appendChild(o); }
    } else if (f.kind === 'array') {
      input = el('textarea');
      input.rows = 3;
      input.placeholder = `one ${f.itemKind} per line (or comma separated)`;
    } else if (f.kind === 'json') {
      const mark = el('div', 'unsupported', 'unsupported type, enter JSON');
      row.appendChild(mark);
      row.appendChild(el('pre', 'schema', f.unsupportedReason + '\nschema:\n' + describeSchema(f.schema)));
      input = el('textarea');
      input.rows = 4;
      input.placeholder = 'JSON value';
    } else {
      input = el('input');
      input.type = 'text';
      if (f.kind === 'integer') { input.inputMode = 'numeric'; input.placeholder = '0'; }
      if (f.kind === 'account_id') input.placeholder = 'alice.testnet';
    }
    input.name = f.name;
    input.disabled = disabled;
    inputs[f.name] = input;
    row.appendChild(input);
    form.appendChild(row);
  }
  card.appendChild(form);

  let depositInput = null;
  let gasInput = null;
  if (!isView) {
    const tx = el('div', 'tx-fields');
    const dRow = el('div', 'field deposit' + (isPayable ? ' prominent' : ''));
    const dLabel = el('label', 'field-label');
    dLabel.appendChild(el('span', 'field-name', 'deposit (NEAR)'));
    dLabel.appendChild(el('span', 'field-type', isPayable ? 'payable: attach the deposit this method expects' : 'method is not payable, deposit stays 0'));
    dRow.appendChild(dLabel);
    depositInput = el('input');
    depositInput.type = 'text';
    depositInput.inputMode = 'decimal';
    depositInput.value = '0';
    depositInput.disabled = disabled || !isPayable;
    dRow.appendChild(depositInput);
    const gRow = el('div', 'field');
    const gLabel = el('label', 'field-label');
    gLabel.appendChild(el('span', 'field-name', 'gas (Tgas)'));
    gLabel.appendChild(el('span', 'field-type', 'default 30, protocol maximum 300'));
    gRow.appendChild(gLabel);
    gasInput = el('input');
    gasInput.type = 'text';
    gasInput.inputMode = 'decimal';
    gasInput.value = '30';
    gasInput.disabled = disabled;
    gRow.appendChild(gasInput);
    tx.appendChild(dRow);
    tx.appendChild(gRow);
    card.appendChild(tx);
  }

  const button = el('button', 'run', isView ? 'Call' : 'Send transaction (wallet)');
  button.type = 'button';
  button.disabled = disabled;
  const out = el('pre', 'out');
  out.setAttribute('aria-live', 'polite');
  card.appendChild(button);
  card.appendChild(out);

  function show(text, cls) {
    out.className = 'out' + (cls ? ' ' + cls : '');
    out.textContent = text;
  }

  button.addEventListener('click', async () => {
    const values = {};
    for (const f of fields) {
      const inp = inputs[f.name];
      values[f.name] = inp.value;
    }
    const { args, errors } = encodeArgs(fields, values);
    if (errors.length) {
      show(errors.map((e) => `${e.name}: ${e.message}`).join('\n'), 'error');
      return;
    }
    button.disabled = true;
    try {
      if (isView) {
        show('calling ...');
        const res = await handlers.onView(fn.name, args);
        show(JSON.stringify(res, null, 2), 'ok');
      } else {
        const deposit = parseNearToYocto(depositInput.value);
        const gas = parseTgas(gasInput.value);
        show('waiting for the wallet ...');
        const msg = await handlers.onCall(fn.name, args, { deposit, gas });
        show(msg || 'sent', 'ok');
      }
    } catch (e) {
      show(e && e.message ? e.message : String(e), 'error');
    } finally {
      button.disabled = disabled;
    }
  });

  return card;
}

// --------------------------------------------------------------- page wiring
// Everything below needs a browser. The wallet write path (near-api-js
// WalletConnection, MyNearWallet redirect) is NOT verified without a wallet.

const NEAR_API_JS_URL = 'https://cdn.jsdelivr.net/npm/near-api-js@4.0.4/dist/near-api-js.min.js';
const STORE = 'bumm-guard-panel';

function initPage() {
  const $ = (id) => document.getElementById(id);
  const state = { parsed: null, nearApi: null, wallet: null };

  function saveSettings() {
    try {
      localStorage.setItem(STORE, JSON.stringify({
        network: $('network').value,
        rpc: $('rpc').value,
        contract: $('contract').value,
        abi: state.abiText || null,
      }));
    } catch (_) { /* storage may be blocked */ }
  }
  function loadSettings() {
    try { return JSON.parse(localStorage.getItem(STORE) || 'null'); } catch (_) { return null; }
  }

  function setStatus(msg, cls) {
    const s = $('status');
    s.textContent = msg;
    s.className = 'status' + (cls ? ' ' + cls : '');
  }

  function conn() {
    const contractId = $('contract').value.trim();
    if (!isAccountId(contractId)) throw new Error('enter a valid contract account id first (for example mycontract.testnet)');
    const rpcUrl = $('rpc').value.trim();
    if (!/^https?:\/\//.test(rpcUrl)) throw new Error('RPC URL must start with http:// or https://');
    return { rpcUrl, contractId, network: $('network').value };
  }

  function loadAbiText(text, source) {
    let abi;
    try { abi = JSON.parse(text); } catch (e) { throw new Error(`${source}: not valid JSON (${e.message})`); }
    state.parsed = parseAbi(abi);
    state.abiText = text;
    renderCards();
    const m = state.parsed.metadata;
    setStatus(`ABI loaded from ${source}: ${state.parsed.functions.length} functions${m.name ? ' (' + m.name + ')' : ''}.`, 'ok');
    saveSettings();
  }

  function renderCards() {
    const root = $('cards');
    root.textContent = '';
    if (!state.parsed) return;
    const handlers = {
      onView: async (name, args) => {
        const c = conn();
        return viewCall(c.rpcUrl, c.contractId, name, args);
      },
      onCall: (name, args, opts) => sendTransaction(name, args, opts),
    };
    for (const fn of state.parsed.functions) {
      root.appendChild(renderFunction(fn, state.parsed.definitions, handlers, document));
    }
  }

  // --- wallet (unverified without a wallet) ---
  function loadNearApi() {
    if (state.nearApi) return Promise.resolve(state.nearApi);
    return new Promise((resolve, reject) => {
      if (window.nearApi) { state.nearApi = window.nearApi; resolve(window.nearApi); return; }
      const s = document.createElement('script');
      s.src = NEAR_API_JS_URL;
      s.onload = () => {
        if (!window.nearApi) { reject(new Error('near-api-js loaded but window.nearApi is missing')); return; }
        state.nearApi = window.nearApi;
        resolve(window.nearApi);
      };
      s.onerror = () => reject(new Error('could not load near-api-js from the CDN (offline?)'));
      document.head.appendChild(s);
    });
  }

  async function ensureWallet() {
    const network = $('network').value;
    if (state.wallet && state.wallet.network === network) return state.wallet;
    const api = await loadNearApi();
    const cfg = NETWORKS[network];
    const near = await api.connect({
      networkId: network,
      nodeUrl: $('rpc').value.trim(),
      walletUrl: cfg.wallet,
      keyStore: new api.keyStores.BrowserLocalStorageKeyStore(),
    });
    const connection = new api.WalletConnection(near, 'bumm-guard-panel');
    state.wallet = { network, near, connection };
    return state.wallet;
  }

  function refreshWalletUi() {
    const w = state.wallet;
    const signed = !!(w && w.connection.isSignedIn());
    $('wallet-state').textContent = signed
      ? `signed in as ${w.connection.getAccountId()}`
      : 'not signed in (view calls work without a wallet)';
    $('signin').hidden = signed;
    $('signout').hidden = !signed;
  }

  async function signIn() {
    try {
      const c = conn();
      saveSettings();
      const w = await ensureWallet();
      try { localStorage.setItem(STORE + '-wallet', w.network); } catch (_) { /* ignore */ }
      await w.connection.requestSignIn({ contractId: c.contractId });
    } catch (e) { setStatus(e.message, 'error'); }
  }

  async function signOut() {
    if (state.wallet) state.wallet.connection.signOut();
    try { localStorage.removeItem(STORE + '-wallet'); } catch (_) { /* ignore */ }
    refreshWalletUi();
  }

  async function sendTransaction(name, args, opts) {
    const c = conn();
    const w = await ensureWallet();
    if (!w.connection.isSignedIn()) throw new Error('sign in with a wallet first (button at the top)');
    const account = w.connection.account();
    const outcome = await account.functionCall({
      contractId: c.contractId,
      methodName: name,
      args,
      gas: BigInt(opts.gas),
      attachedDeposit: BigInt(opts.deposit),
    });
    const hash = outcome && outcome.transaction && outcome.transaction.hash;
    let last = null;
    try { last = state.nearApi.providers.getTransactionLastResult(outcome); } catch (_) { /* ignore */ }
    return `transaction ${hash || '(hash unavailable)'}\nreturn value: ${JSON.stringify(last)}\n` +
      (hash ? `${NETWORKS[c.network].explorer}/txns/${hash}` : '');
  }

  // --- wiring ---
  function applyNetwork() {
    const n = NETWORKS[$('network').value];
    $('rpc').value = n.rpc;
    saveSettings();
  }
  $('network').addEventListener('change', () => { state.wallet = null; applyNetwork(); refreshWalletUi(); });
  $('contract').addEventListener('change', saveSettings);
  $('rpc').addEventListener('change', saveSettings);

  $('abi-file').addEventListener('change', async (ev) => {
    const f = ev.target.files && ev.target.files[0];
    if (!f) return;
    try { loadAbiText(await f.text(), `file ${f.name}`); } catch (e) { setStatus(e.message, 'error'); }
  });
  $('abi-url-load').addEventListener('click', async () => {
    const url = $('abi-url').value.trim();
    if (!url) { setStatus('enter an ABI URL first', 'error'); return; }
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      loadAbiText(await r.text(), url);
    } catch (e) {
      setStatus(`could not load ${url}: ${e.message}. Cross-origin URLs need CORS; otherwise download the file and use the file picker.`, 'error');
    }
  });
  $('abi-paste-load').addEventListener('click', () => {
    try { loadAbiText($('abi-paste').value, 'pasted text'); } catch (e) { setStatus(e.message, 'error'); }
  });
  $('abi-fixture').addEventListener('click', async () => {
    try {
      const r = await fetch('fixtures/event_registration_abi.json');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      loadAbiText(await r.text(), 'fixtures/event_registration_abi.json');
    } catch (e) {
      setStatus(`the browser blocked reading the fixture (${e.message}). Under file:// use the file picker and choose ui/fixtures/event_registration_abi.json, or serve the folder: python3 -m http.server -d ui`, 'error');
    }
  });
  $('signin').addEventListener('click', signIn);
  $('signout').addEventListener('click', signOut);

  // restore
  const saved = loadSettings();
  if (saved) {
    if (saved.network && NETWORKS[saved.network]) $('network').value = saved.network;
    $('rpc').value = saved.rpc || NETWORKS[$('network').value].rpc;
    $('contract').value = saved.contract || '';
    if (saved.abi) { try { loadAbiText(saved.abi, 'the previous session'); } catch (_) { /* ignore */ } }
  } else {
    applyNetwork();
  }

  // returning from the wallet: show the transaction hash, restore the session
  const params = new URLSearchParams(location.search);
  const txHashes = params.get('transactionHashes');
  if (txHashes) {
    const net = NETWORKS[$('network').value];
    setStatus(`Wallet returned transaction ${txHashes}. ${net.explorer}/txns/${txHashes.split(',')[0]}`, 'ok');
  }
  let walletWanted = null;
  try { walletWanted = localStorage.getItem(STORE + '-wallet'); } catch (_) { /* ignore */ }
  if (walletWanted === $('network').value || params.get('account_id')) {
    ensureWallet().then(refreshWalletUi).catch((e) => setStatus(e.message, 'error'));
  }
  refreshWalletUi();
}

const API = {
  NETWORKS, parseAbi, fieldForSchema, buildFields, encodeArgs, renderFunction, viewCall,
  buildViewRequest, parseNearToYocto, parseTgas, isAccountId,
};

if (typeof module !== 'undefined' && module.exports) module.exports = API;
if (typeof window !== 'undefined') window.BummPanel = API;
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initPage);
  else initPage();
}
