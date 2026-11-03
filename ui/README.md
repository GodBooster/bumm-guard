# BUMM Guard ABI panel

A static page that turns a near-abi file into one form per contract function: view
methods are called through public RPC, change methods are sent through a wallet. It is
an **interaction panel generated from the ABI, not a product UI**. There is no backend
and no build step: `index.html` + `panel.js` + `panel.css`.

Input format: `docs/FORMATS.md` section 5 (`artifact.abi`, i.e. `cargo near abi` output).

## Open it

```sh
open ui/index.html                 # works from file://
python3 -m http.server -d ui       # or serve it: http://localhost:8000
```
Use the http server for the wallet flow (a wallet redirects back to the page's URL,
which is unreliable for `file://`) and for the "Load bundled N06 fixture" button
(browsers block `fetch` of local files under `file://`; use the file picker there).

## Try it with the fixture (do X, see Y)

Replace `<your-contract>.testnet` with a testnet account where the N06 contract is
deployed and initialised (`new` called with a capacity). View calls need nothing else.

1. Open the page. See three boxes (network, ABI, wallet) and "Load an ABI to see the functions."
2. Network `testnet` is preselected, RPC URL `https://rpc.testnet.fastnear.com` (editable).
   Type `<your-contract>.testnet` into **Contract account id**.
3. Click **Load bundled N06 fixture** (or choose `ui/fixtures/event_registration_abi.json`
   with the file picker). See "ABI loaded ... 8 functions (event_registration)" and 8 cards:
   `new` (badges call + init), `register`, `cancel`, and view cards `get_capacity`,
   `get_registered_count`, `get_registered`, `is_registered`, `get_seats_left`.
4. On `get_capacity` click **Call**. See the capacity as a JSON number, for example `2`.
   On `get_registered` click **Call**. See a JSON array of account ids (`[]` when empty).
5. On `is_registered` type an account id, click **Call**. See `true` or `false`.
   Type `Alice` instead. See `account_id: "Alice" is not a valid NEAR account id ...`
   and no request is sent.
6. On `new` type `-1` as capacity, click **Send transaction (wallet)**. See
   `capacity: -1 is below the minimum 0`. (`new` is `init`: it works once, right after deploy.)
7. Click **Sign in with wallet**. The browser goes to MyNearWallet (testnet); approve.
   Back on the page, the wallet box shows `signed in as <you>.testnet`.
8. On `register` leave gas at 30 Tgas, click **Send transaction (wallet)**. See the
   transaction hash and an explorer link; then `get_registered` shows your account.
   Repeat `register` with the same account: the card shows the contract's panic text
   (`... is already registered`) from the wallet/RPC error.

Steps 1-6 need no wallet. Steps 7-8 are the unverified part (see below).

To use your own contract, run `cargo near abi` and load `target/near/<name>_abi.json`
instead of the fixture (there is no `bumm-guard build` command in this release). The fixture
is hand-written, see `fixtures/README.md`.

## What a card shows

| ABI | Panel |
|---|---|
| `kind: view` | argument fields, **Call** button, JSON result below; free, no wallet |
| `kind: call` | argument fields, **deposit (NEAR)** (default 0), **gas (Tgas)** (default 30, max 300), **Send transaction (wallet)** |
| `payable` | card has a coloured border, deposit field is highlighted; for non-payable methods the deposit field is disabled at 0 |
| `private` | listed, all inputs and the button disabled, with a note |
| `init` | marked, with a note that it is called once after deploy |
| borsh arguments | listed, disabled, with a note |

## Supported argument types

From `params.args[].type_schema`; `$ref` into `body.root_schema.definitions` is followed
**one level**.

| Schema | Field |
|---|---|
| `string` | text (a string `enum` becomes a select) |
| `integer` (`uint8`..`uint128`, `int8`..`int128`, `minimum`/`maximum`) | text, validated for range; values beyond 2^53 are refused (use `U64`/`U128` strings in the contract) |
| `boolean` | select true / false |
| `$ref` to `AccountId` (or `format: account_id`, title `AccountId`) | text, validated against the NEAR account id pattern |
| `U64`/`U128`/`I64`/`I128` definitions | text, validated as a decimal integer string |
| `array` of the above | textarea, one item per line (or comma separated) |
| `Option<T>` (`type: [T, "null"]` or `anyOf [T, null]`) | same field, optional, omitted when empty |

Anything else (objects, floats, tuples, enums with data, `$ref` chains deeper than one
level, `anyOf`/`oneOf` that is not `Option`, arrays of those) gets a raw JSON textarea,
the schema printed above it and a visible **unsupported type, enter JSON** marker.
Return values are shown as pretty-printed JSON whatever their type.

## Tests

```sh
node --test ui/test        # 18 tests, node:test, no dependencies, no network
```
Covers `parseAbi` on the fixture, `fieldForSchema` mappings, `encodeArgs` validation,
deposit/gas parsing and the JSON-RPC request body of `viewCall` (injected `fetch` stub).
`ui/test/index.js` only exists so that Node 22 accepts the directory argument.

## API of `panel.js` (also usable from Node via `require`)

`parseAbi(abi) -> {functions:[{name, kind, modifiers, args:[{name, schema}], result, doc, serialization}], definitions, metadata}`,
`fieldForSchema(schema, definitions) -> {kind, itemKind?, unsupportedReason?, ...}`,
`encodeArgs(fields, values) -> {args, errors:[{name, message}]}` (`fields` from `buildFields(fn, definitions)`),
`renderFunction(fn, definitions, handlers, document) -> HTMLElement`,
`viewCall(rpcUrl, contractId, method, args[, fetch]) -> Promise<json>`.
`panel.js` is a classic script (not an ES module) because browsers refuse module
scripts from `file://`.

## Verified and not verified

Verified:
- Unit tests above (18 pass on Node 22).
- `viewCall` against live testnet RPC (`rpc.testnet.fastnear.com`): a view on `wrap.testnet`
  returned parsed JSON; a missing method and a missing account produce readable errors.
- Page in a browser over `http://localhost`: ABI load from the fixture and from pasted
  JSON, 8 cards, validation messages, payable/private/unsupported rendering, a view call
  error surfaced on a contract that lacks the method.

**Not verified: the wallet write path** (steps 7-8 above; needs a wallet, a testnet
account and a deployed contract, none available when this was written). The code is in
`panel.js` (`signIn`, `sendTransaction`): it loads `near-api-js@4.0.4`
(`dist/near-api-js.min.js`, CDN, only on first use), builds a `WalletConnection` for
MyNearWallet (`testnet.mynearwallet.com` / `app.mynearwallet.com`), requests sign-in for
the contract and calls `account.functionCall` with the typed deposit and gas. Assumptions
that may need a fix on first run: the `window.nearApi` global name of that bundle, `BigInt`
accepted for `gas`/`attachedDeposit`, and the redirect back (the page restores network,
contract and ABI from `localStorage`, and shows `transactionHashes` from the URL). A
non-zero deposit makes the wallet ask for approval by redirect; the result then comes
back as `transactionHashes` and the card does not show the return value.
Also not verified: the bundled fixture against real `cargo near abi` output (it is
hand-written), and mainnet (RPC/wallet URLs are defaults, untested).
