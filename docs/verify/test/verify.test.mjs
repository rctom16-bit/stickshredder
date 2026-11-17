// Tests for the static verify page logic (run: node --test docs/verify/test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const SV = require('../verify.js');

const VECTOR_PAYLOAD = {
  v: 1, n: 42, t: '2026-11-17T10:00:00Z', op: 'Max Mustermann', co: 'Beispiel GmbH',
  dm: 'SanDisk 3.2Gen1', ds: '0901C3F2', dc: 61953654784, dt: 'USB', m: 'ZeroFill', p: 1,
  sk: 2, sl: 'H-2', r: 'ok', vm: 'full', ve: 0, bs: 0, hpa: 'n/a', sw: '2.0.0', h: '',
  k: 'ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ',
};
const PUBKEY = 'ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ';
const FINGERPRINT = '65B6-0673-D6ED-884B-F01C-2C22-2D82-ADA0';
const TOKEN =
  'eyJicyI6MCwiY28iOiJCZWlzcGllbCBHbWJIIiwiZGMiOjYxOTUzNjU0Nzg0LCJkbSI6IlNhbkRpc2sgMy4yR2VuMSIsImRzIjoiMDkwMUMzRjIiLCJkdCI6IlVTQiIsImgiOiIiLCJocGEiOiJuL2EiLCJrIjoiZWJWV0xvX21WUGxBZUxFUzZLbUxwNUFmaFRybWxiN1g0T09SQzYwRWxtUSIsIm0iOiJaZXJvRmlsbCIsIm4iOjQyLCJvcCI6Ik1heCBNdXN0ZXJtYW5uIiwicCI6MSwiciI6Im9rIiwic2siOjIsInNsIjoiSC0yIiwic3ciOiIyLjAuMCIsInQiOiIyMDI2LTExLTE3VDEwOjAwOjAwWiIsInYiOjEsInZlIjowLCJ2bSI6ImZ1bGwifQ' +
  '.XR2XsL_KAfkdyOTIduvuAletZ4CSwL-TmNYHY4UqFanWmkaTnpj_O7sHe1CJIY8L3zkiQEZh_jCIBtCbBc4gBA';

// Python: json.dumps(p, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
// for a payload with umlauts, quotes, backslashes, control chars, U+2028, emoji, nesting.
const PY_TRICKY_B64 =
  'eyJhIjpbMSx7IngiOiLDnyIsInkiOjJ9XSwiYiI6dHJ1ZSwiY28iOiLDhDxiPiY8L2I-IiwibiI6Nywib3AiOiJKw7xyZ2VuIFwiUVwiIMOWelxcblx0XHUwMDAx4oCo4oKs8J-YgCIsInoiOm51bGx9';

function flipChar(s, i) {
  const c = s[i];
  return s.slice(0, i) + (c === 'A' ? 'B' : 'A') + s.slice(i + 1);
}

function reverseKeys(value) {
  if (Array.isArray(value)) return value.map(reverseKeys);
  if (value && typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value).reverse()) out[k] = reverseKeys(value[k]);
    return out;
  }
  return value;
}

test('base64url round trip without padding', () => {
  for (let len = 0; len < 40; len++) {
    const bytes = new Uint8Array(len).map((_, i) => (i * 37 + len) & 0xff);
    const enc = SV.b64urlEncode(bytes);
    assert.match(enc, /^[A-Za-z0-9_-]*$/);
    assert.deepEqual(SV.b64urlDecode(enc), bytes);
  }
  assert.equal(SV.b64urlEncode(SV.b64urlDecode(PUBKEY)), PUBKEY);
});

test('base64url decode rejects invalid input', () => {
  for (const bad of ['a', 'ab=c', 'ab+c', 'ab/c', 'a b', 'abcde', 'äöü']) {
    assert.throws(() => SV.b64urlDecode(bad), undefined, bad);
  }
});

test('canonicalisation of the vector payload reproduces the token first segment', () => {
  const seg = SV.b64urlEncode(SV.canonicalBytes(VECTOR_PAYLOAD));
  assert.equal(seg, TOKEN.split('.')[0]);
  assert.equal(SV.b64urlEncode(SV.canonicalBytes(reverseKeys(VECTOR_PAYLOAD))), TOKEN.split('.')[0]);
});

test('canonicalisation matches Python for umlauts, escapes, emoji and nesting', () => {
  const pyBytes = SV.b64urlDecode(PY_TRICKY_B64);
  const parsed = JSON.parse(new TextDecoder().decode(pyBytes));
  const ours = SV.canonicalBytes(reverseKeys(parsed));
  assert.equal(SV.b64urlEncode(ours), PY_TRICKY_B64);
});

test('fingerprint of the vector key', async () => {
  assert.equal(await SV.fingerprint(SV.b64urlDecode(PUBKEY)), FINGERPRINT);
});

for (const mode of ['webcrypto', 'fallback']) {
  const opts = { forceFallback: mode === 'fallback' };

  test(`[${mode}] test vector verifies; key unknown without trust list`, async () => {
    const res = await SV.verifyToken(TOKEN, {}, opts);
    assert.equal(res.status, 'unknown_key');
    assert.equal(res.fingerprint, FINGERPRINT);
    assert.deepEqual(res.payload, VECTOR_PAYLOAD);
    assert.equal(res.engine, mode);
  });

  test(`[${mode}] test vector with trusted fingerprint is trusted`, async () => {
    const res = await SV.verifyToken(TOKEN, { [FINGERPRINT]: { name: 'Beispiel GmbH', key: PUBKEY } }, opts);
    assert.equal(res.status, 'trusted');
    assert.equal(res.trustedName, 'Beispiel GmbH');
  });

  test(`[${mode}] one-character tamper in payload fails`, async () => {
    const [p, s] = TOKEN.split('.');
    for (const i of [0, 10, 100, p.length - 3]) {
      const res = await SV.verifyToken(flipChar(p, i) + '.' + s, {}, opts);
      assert.equal(res.status, 'invalid', `index ${i}`);
    }
  });

  test(`[${mode}] one-character tamper in signature fails`, async () => {
    const [p, s] = TOKEN.split('.');
    for (const i of [0, 20, 40, 60]) {
      const res = await SV.verifyToken(p + '.' + flipChar(s, i), {}, opts);
      assert.equal(res.status, 'invalid', `index ${i}`);
    }
  });

  test(`[${mode}] payload re-signed claim with another key in k fails`, async () => {
    const other = { ...VECTOR_PAYLOAD, k: SV.b64urlEncode(new Uint8Array(32).fill(7)) };
    const forged = SV.b64urlEncode(SV.canonicalBytes(other)) + '.' + TOKEN.split('.')[1];
    const res = await SV.verifyToken(forged, {}, opts);
    assert.equal(res.status, 'invalid');
  });
}

test('malformed tokens are rejected without throwing', async () => {
  const enc = (s) => SV.b64urlEncode(new TextEncoder().encode(s));
  const sig = TOKEN.split('.')[1];
  const cases = [
    '', '   ', 'abc', 'a.b.c', '.', '!!!.###', 'abc.', '.abc',
    enc('not json') + '.' + sig,
    enc('[1,2,3]') + '.' + sig,
    enc('"str"') + '.' + sig,
    enc('null') + '.' + sig,
    enc('{"v":1}') + '.' + sig,
    enc('{"v":1,"k":"short"}') + '.' + sig,
    enc('{"v":1,"k":123}') + '.' + sig,
    TOKEN.split('.')[0] + '.' + sig.slice(0, 40),
    TOKEN.split('.')[0] + '.AAAA',
    'x'.repeat(100000) + '.' + sig,
    null, undefined, 42, {},
  ];
  for (const c of cases) {
    const res = await SV.verifyToken(c, {});
    assert.equal(res.status, 'invalid', String(c).slice(0, 40));
    assert.equal(typeof res.reason, 'string');
  }
});

test('unsupported payload version is invalid', async () => {
  const p = { ...VECTOR_PAYLOAD, v: 2 };
  const res = await SV.verifyToken(SV.b64urlEncode(SV.canonicalBytes(p)) + '.' + TOKEN.split('.')[1], {});
  assert.equal(res.status, 'invalid');
});

test('extractToken accepts raw token, hash, full verify URL and whitespace', () => {
  assert.equal(SV.extractToken(TOKEN), TOKEN);
  assert.equal(SV.extractToken('#' + TOKEN), TOKEN);
  assert.equal(SV.extractToken('  https://rctom16-bit.github.io/stickshredder/verify/#' + TOKEN + '\n'), TOKEN);
  assert.equal(SV.extractToken(TOKEN.slice(0, 50) + '\n' + TOKEN.slice(50)), TOKEN);
  assert.equal(SV.extractToken(''), null);
  assert.equal(SV.extractToken('hello world'), null);
  assert.equal(SV.extractToken('#key=abc&name=x'), null);
});

test('extractTokenFromPdf finds the marker in raw PDF bytes', () => {
  const pdf = '%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Author (x) /Keywords (stickshredder-token:' + TOKEN +
    ') /Producer (ReportLab) >>\nendobj\n%%EOF\n';
  const bytes = Uint8Array.from(pdf, (c) => c.charCodeAt(0) & 0xff);
  assert.equal(SV.extractTokenFromPdf(bytes), TOKEN);
  assert.equal(SV.extractTokenFromPdf(bytes.buffer), TOKEN);
  assert.equal(SV.extractTokenFromPdf(new TextEncoder().encode('%PDF-1.4 nothing here')), null);
});

test('parseKeyHash reads key cards and rejects junk', () => {
  const name = 'Müller & Söhne GmbH';
  const parsed = SV.parseKeyHash('#key=' + PUBKEY + '&name=' + encodeURIComponent(name));
  assert.deepEqual(parsed, { key: PUBKEY, name });
  assert.deepEqual(SV.parseKeyHash('key=' + PUBKEY), { key: PUBKEY, name: '' });
  assert.equal(SV.parseKeyHash('#' + TOKEN), null);
  assert.equal(SV.parseKeyHash('#key=abc&name=x'), null);
  // Broken percent-encoding must not throw; the raw text is kept.
  assert.deepEqual(SV.parseKeyHash('#key=' + PUBKEY + '&name=%E0%A4%A'), { key: PUBKEY, name: '%E0%A4%A' });
  assert.deepEqual(SV.parseKeyHash('#name=X&key=' + PUBKEY), { key: PUBKEY, name: 'X' });
});
