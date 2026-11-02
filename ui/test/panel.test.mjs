import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const P = require(path.join(here, '..', 'panel.js'));
const abi = JSON.parse(readFileSync(path.join(here, '..', 'fixtures', 'event_registration_abi.json'), 'utf8'));

const parsed = P.parseAbi(abi);
const byName = Object.fromEntries(parsed.functions.map((f) => [f.name, f]));
const defs = parsed.definitions;

test('module is importable in Node without a DOM', () => {
  assert.equal(typeof document, 'undefined');
  assert.equal(typeof P.parseAbi, 'function');
});

test('parseAbi: fixture functions, kinds and modifiers', () => {
  // N06 source has 8 public methods (new, register, cancel + 5 views).
  assert.equal(parsed.functions.length, 8);
  const kinds = Object.fromEntries(parsed.functions.map((f) => [f.name, f.kind]));
  assert.deepEqual(kinds, {
    new: 'call', register: 'call', cancel: 'call',
    get_capacity: 'view', get_registered_count: 'view', get_registered: 'view',
    is_registered: 'view', get_seats_left: 'view',
  });
  assert.deepEqual(byName.new.modifiers, ['init']);
  assert.deepEqual(byName.register.modifiers, []);
  assert.deepEqual(byName.new.args.map((a) => a.name), ['capacity']);
  assert.deepEqual(byName.is_registered.args.map((a) => a.name), ['account_id']);
  assert.ok(byName.get_registered.result);
  assert.ok(defs.AccountId);
});

test('parseAbi: rejects non-ABI input and bad kind', () => {
  assert.throws(() => P.parseAbi({}), /body\.functions/);
  assert.throws(() => P.parseAbi({ body: { functions: [{ name: 'x', kind: 'weird' }] } }), /kind/);
});

test('parseAbi: private and payable modifiers pass through, borsh args are dropped', () => {
  const p = P.parseAbi({ body: { root_schema: {}, functions: [
    { name: 'cb', kind: 'call', modifiers: ['private', 'payable'], params: { serialization_type: 'json', args: [] } },
    { name: 'b', kind: 'call', params: { serialization_type: 'borsh', args: [{ name: 'x', type_schema: {} }] } },
  ] } });
  assert.deepEqual(p.functions[0].modifiers, ['private', 'payable']);
  assert.equal(p.functions[1].serialization, 'borsh');
  assert.deepEqual(p.functions[1].args, []);
});

test('fieldForSchema: u32 -> integer with range', () => {
  const f = P.fieldForSchema(byName.new.args[0].schema, defs);
  assert.equal(f.kind, 'integer');
  assert.equal(f.min, 0n);
  assert.equal(f.max, 4294967295n);
});

test('fieldForSchema: bool -> boolean', () => {
  assert.equal(P.fieldForSchema({ type: 'boolean' }, defs).kind, 'boolean');
});

test('fieldForSchema: AccountId $ref -> account_id', () => {
  const f = P.fieldForSchema(byName.is_registered.args[0].schema, defs);
  assert.equal(f.kind, 'account_id');
  assert.equal(f.definition, 'AccountId');
  assert.equal(P.fieldForSchema({ type: 'string', format: 'account_id' }, {}).kind, 'account_id');
});

test('fieldForSchema: Vec<AccountId> -> array of account_id', () => {
  const f = P.fieldForSchema(byName.get_registered.result, defs);
  assert.equal(f.kind, 'array');
  assert.equal(f.itemKind, 'account_id');
});

test('fieldForSchema: object, nested $ref, unresolved $ref, array of objects -> json + reason', () => {
  const obj = P.fieldForSchema({ type: 'object', properties: { a: { type: 'string' } } }, defs);
  assert.equal(obj.kind, 'json');
  assert.match(obj.unsupportedReason, /object/);
  const nested = P.fieldForSchema({ $ref: '#/definitions/Alias' }, { Alias: { $ref: '#/definitions/AccountId' }, AccountId: { type: 'string' } });
  assert.equal(nested.kind, 'json');
  assert.match(nested.unsupportedReason, /one level/);
  assert.equal(P.fieldForSchema({ $ref: '#/definitions/Nope' }, defs).kind, 'json');
  const arr = P.fieldForSchema({ type: 'array', items: { type: 'object' } }, defs);
  assert.equal(arr.kind, 'json');
  assert.ok(arr.unsupportedReason);
});

test('fieldForSchema: Option<T> forms are optional', () => {
  const a = P.fieldForSchema({ type: ['integer', 'null'], format: 'uint32' }, defs);
  assert.equal(a.kind, 'integer');
  assert.equal(a.optional, true);
  const b = P.fieldForSchema({ anyOf: [{ $ref: '#/definitions/AccountId' }, { type: 'null' }] }, defs);
  assert.equal(b.kind, 'account_id');
  assert.equal(b.optional, true);
});

test('encodeArgs: integer range and shape', () => {
  const fields = P.buildFields(byName.new, defs);
  assert.deepEqual(P.encodeArgs(fields, { capacity: '2' }), { args: { capacity: 2 }, errors: [] });
  for (const bad of ['-1', '4294967296', '1.5', 'abc', '']) {
    const r = P.encodeArgs(fields, { capacity: bad });
    assert.equal(r.errors.length, 1, `value ${JSON.stringify(bad)} must be rejected`);
    assert.equal(r.errors[0].name, 'capacity');
  }
});

test('encodeArgs: account id pattern', () => {
  const fields = P.buildFields(byName.is_registered, defs);
  assert.deepEqual(P.encodeArgs(fields, { account_id: 'alice.testnet' }).args, { account_id: 'alice.testnet' });
  assert.ok(P.encodeArgs(fields, { account_id: 'x' }).errors.length);            // too short
  assert.ok(P.encodeArgs(fields, { account_id: 'Alice.testnet' }).errors.length); // uppercase
  assert.ok(P.encodeArgs(fields, { account_id: 'a..b' }).errors.length);          // empty label
  assert.ok(P.encodeArgs(fields, { account_id: 'alice testnet' }).errors.length); // space
  assert.ok(P.encodeArgs(fields, { account_id: 'a'.repeat(65) }).errors.length);  // too long
});

test('encodeArgs: arrays, booleans and json', () => {
  const arrField = [{ name: 'who', ...P.fieldForSchema({ type: 'array', items: { $ref: '#/definitions/AccountId' } }, defs) }];
  assert.deepEqual(P.encodeArgs(arrField, { who: 'a1.near\nb2.near, c3.near' }).args, { who: ['a1.near', 'b2.near', 'c3.near'] });
  assert.deepEqual(P.encodeArgs(arrField, { who: '' }).args, { who: [] });
  assert.equal(P.encodeArgs(arrField, { who: 'a1.near\nBAD' }).errors.length, 1);
  const boolField = [{ name: 'flag', ...P.fieldForSchema({ type: 'boolean' }, defs) }];
  assert.deepEqual(P.encodeArgs(boolField, { flag: 'true' }).args, { flag: true });
  const jsonField = [{ name: 'o', ...P.fieldForSchema({ type: 'object' }, defs) }];
  assert.deepEqual(P.encodeArgs(jsonField, { o: '{"a":1}' }).args, { o: { a: 1 } });
  assert.equal(P.encodeArgs(jsonField, { o: '{bad' }).errors.length, 1);
});

test('encodeArgs: missing required string/optional omitted', () => {
  const req = [{ name: 's', ...P.fieldForSchema({ type: 'string' }, defs) }];
  assert.equal(P.encodeArgs(req, {}).errors.length, 1);
  const opt = [{ name: 's', ...P.fieldForSchema({ type: ['string', 'null'] }, defs) }];
  assert.deepEqual(P.encodeArgs(opt, {}), { args: {}, errors: [] });
});

test('deposit and gas parsing', () => {
  assert.equal(P.parseNearToYocto('0'), '0');
  assert.equal(P.parseNearToYocto('1'), '1000000000000000000000000');
  assert.equal(P.parseNearToYocto('0.5'), '500000000000000000000000');
  assert.throws(() => P.parseNearToYocto('-1'));
  assert.throws(() => P.parseNearToYocto('1e3'));
  assert.equal(P.parseTgas('30'), '30000000000000');
  assert.throws(() => P.parseTgas('301'));
  assert.throws(() => P.parseTgas('0'));
});

function stubFetch(responseBody, { ok = true, status = 200 } = {}) {
  const calls = [];
  const f = async (url, opts) => {
    calls.push({ url, opts });
    return { ok, status, json: async () => responseBody };
  };
  return { f, calls };
}

test('viewCall: builds the JSON-RPC call_function body and decodes the result', async () => {
  const payload = Array.from(new TextEncoder().encode('["alice.testnet","bob.testnet"]'));
  const { f, calls } = stubFetch({ jsonrpc: '2.0', id: 'x', result: { result: payload, logs: [], block_height: 1 } });
  const out = await P.viewCall('https://rpc.example/', 'c.testnet', 'get_registered', { limit: 2 }, f);
  assert.deepEqual(out, ['alice.testnet', 'bob.testnet']);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://rpc.example/');
  assert.equal(calls[0].opts.method, 'POST');
  const body = JSON.parse(calls[0].opts.body);
  assert.equal(body.jsonrpc, '2.0');
  assert.equal(body.method, 'query');
  assert.equal(body.params.request_type, 'call_function');
  assert.equal(body.params.finality, 'final');
  assert.equal(body.params.account_id, 'c.testnet');
  assert.equal(body.params.method_name, 'get_registered');
  assert.equal(Buffer.from(body.params.args_base64, 'base64').toString('utf8'), '{"limit":2}');
});

test('viewCall: empty args encode as {} and empty result is null', async () => {
  const { f, calls } = stubFetch({ result: { result: [] } });
  assert.equal(await P.viewCall('u', 'c.near', 'm', undefined, f), null);
  assert.equal(Buffer.from(JSON.parse(calls[0].opts.body).params.args_base64, 'base64').toString(), '{}');
});

test('viewCall: surfaces RPC, contract and HTTP errors', async () => {
  await assert.rejects(
    P.viewCall('u', 'c.near', 'm', {}, stubFetch({ error: { name: 'HANDLER_ERROR', message: 'Server error', cause: { name: 'UNKNOWN_ACCOUNT' } } }).f),
    /UNKNOWN_ACCOUNT/);
  await assert.rejects(
    P.viewCall('u', 'c.near', 'm', {}, stubFetch({ result: { error: 'wasm execution failed: MethodNotFound' } }).f),
    /MethodNotFound/);
  await assert.rejects(P.viewCall('u', 'c.near', 'm', {}, stubFetch({}, { ok: false, status: 503 }).f), /503/);
});
