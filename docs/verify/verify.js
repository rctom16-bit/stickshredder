/*
 * StickShredder certificate verification page.
 *
 * Everything runs locally in the browser: the certificate data arrives in the
 * URL fragment (#...), which browsers never send to a server. No network
 * requests are made by this script.
 *
 * The first half of this file is the verification core (also loaded by the
 * Node tests); the second half wires up the page and only runs in a browser.
 */
(function (root) {
  'use strict';

  // ---------------------------------------------------------------------------
  // Verification core
  // ---------------------------------------------------------------------------

  const MAX_TOKEN_LENGTH = 16384;
  const TOKEN_RE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;
  const PDF_MARKER_RE = /stickshredder-token:([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)/;
  const B64URL_RE = /^[A-Za-z0-9_-]*$/;

  function b64urlEncode(bytes) {
    const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    let bin = '';
    for (let i = 0; i < u8.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    }
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function b64urlDecode(text) {
    if (typeof text !== 'string' || !B64URL_RE.test(text) || text.length % 4 === 1) {
      throw new Error('invalid base64url');
    }
    let b64 = text.replace(/-/g, '+').replace(/_/g, '/');
    while (b64.length % 4) b64 += '=';
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  // Mirrors Python: json.dumps(obj, separators=(",", ":"), sort_keys=True,
  // ensure_ascii=False). JSON.stringify escapes strings exactly like Python
  // does with ensure_ascii=False (quote, backslash, control characters).
  function canonicalJson(value) {
    if (value === null) return 'null';
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) throw new Error('non-finite number');
      return JSON.stringify(value);
    }
    if (typeof value === 'string') return JSON.stringify(value);
    if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
    if (typeof value === 'object') {
      const keys = Object.keys(value).sort();
      return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalJson(value[k])).join(',') + '}';
    }
    throw new Error('unsupported type');
  }

  function canonicalBytes(payload) {
    return new TextEncoder().encode(canonicalJson(payload));
  }

  function subtle() {
    const c = root.crypto;
    if (!c || !c.subtle) throw new Error('crypto_unavailable');
    return c.subtle;
  }

  function hex(bytes) {
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('').toUpperCase();
  }

  async function fingerprint(publicKey) {
    const digest = new Uint8Array(await subtle().digest('SHA-256', publicKey));
    const h = hex(digest.subarray(0, 16));
    return h.match(/.{4}/g).join('-');
  }

  function noble() {
    if (root.nobleEd25519) return root.nobleEd25519;
    if (typeof require === 'function') return require('./vendor/noble-ed25519.js');
    throw new Error('crypto_unavailable');
  }

  // Returns { ok, engine }. WebCrypto first; if the browser lacks Ed25519 (or
  // rejects the input in any way) the vendored implementation decides.
  async function verifySignature(publicKey, signature, message, forceFallback) {
    if (!forceFallback) {
      try {
        const s = subtle();
        const key = await s.importKey('raw', publicKey, { name: 'Ed25519' }, false, ['verify']);
        const ok = await s.verify({ name: 'Ed25519' }, key, signature, message);
        return { ok: ok === true, engine: 'webcrypto' };
      } catch (e) {
        // fall through to the vendored implementation
      }
    }
    let ok = false;
    try {
      ok = (await noble().verifyAsync(signature, message, publicKey, { zip215: false })) === true;
    } catch (e) {
      if (e && e.message === 'crypto_unavailable') throw e;
      ok = false;
    }
    return { ok, engine: 'fallback' };
  }

  function invalid(reason, extra) {
    return Object.assign({ status: 'invalid', payload: null, fingerprint: null, trustedName: null, reason }, extra || {});
  }

  // trusted: { [fingerprint]: { name, key } }
  // Resolves to { status: 'trusted' | 'unknown_key' | 'invalid', payload,
  //   fingerprint, trustedName, reason, engine }. Never rejects.
  async function verifyToken(token, trusted, opts) {
    const options = opts || {};
    try {
      if (typeof token !== 'string' || token.trim() === '') return invalid('empty');
      token = token.trim();
      if (token.length > MAX_TOKEN_LENGTH) return invalid('format');
      if (!TOKEN_RE.test(token)) return invalid('format');
      const [p64, s64] = token.split('.');
      let payloadBytes, signature;
      try {
        payloadBytes = b64urlDecode(p64);
        signature = b64urlDecode(s64);
      } catch (e) {
        return invalid('format');
      }
      if (signature.length !== 64) return invalid('format');
      let payload;
      try {
        payload = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(payloadBytes));
      } catch (e) {
        return invalid('format');
      }
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return invalid('format');
      if (payload.v !== 1) return invalid('version');
      if (typeof payload.k !== 'string') return invalid('format');
      let publicKey;
      try {
        publicKey = b64urlDecode(payload.k);
      } catch (e) {
        return invalid('format');
      }
      if (publicKey.length !== 32) return invalid('format');

      const { ok, engine } = await verifySignature(publicKey, signature, payloadBytes, options.forceFallback);
      if (!ok) return invalid('signature', { engine });

      const fp = await fingerprint(publicKey);
      const entry = trusted && Object.prototype.hasOwnProperty.call(trusted, fp) ? trusted[fp] : null;
      if (entry && entry.key === payload.k) {
        return { status: 'trusted', payload, fingerprint: fp, trustedName: String(entry.name || ''), reason: '', engine };
      }
      return { status: 'unknown_key', payload, fingerprint: fp, trustedName: null, reason: '', engine };
    } catch (e) {
      return invalid(e && e.message === 'crypto_unavailable' ? 'crypto_unavailable' : 'error');
    }
  }

  // Accepts a raw token, "#token", or a whole verify URL (line breaks allowed,
  // e.g. when copied from a PDF). Returns the token or null.
  function extractToken(text) {
    if (typeof text !== 'string') return null;
    let s = text.replace(/\s+/g, '');
    const hashAt = s.indexOf('#');
    if (hashAt >= 0) s = s.slice(hashAt + 1);
    const m = s.match(PDF_MARKER_RE);
    if (m) return m[1];
    return TOKEN_RE.test(s) ? s : null;
  }

  function extractTokenFromPdf(data) {
    const u8 = data instanceof Uint8Array ? data : new Uint8Array(data);
    // latin1-style decode: one char per byte, so the ASCII marker survives.
    let text = '';
    for (let i = 0; i < u8.length; i += 0x8000) {
      text += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    }
    const m = text.match(PDF_MARKER_RE);
    return m ? m[1] : null;
  }

  function safeDecode(s) {
    try {
      return decodeURIComponent(s.replace(/\+/g, ' '));
    } catch (e) {
      return s;
    }
  }

  // "#key=<b64url>&name=<pct>" -> { key, name } or null.
  function parseKeyHash(hash) {
    if (typeof hash !== 'string') return null;
    const h = hash.replace(/^#/, '');
    let key = null;
    let name = '';
    for (const part of h.split('&')) {
      const eq = part.indexOf('=');
      if (eq < 0) continue;
      const k = part.slice(0, eq);
      const v = part.slice(eq + 1);
      if (k === 'key') key = v;
      else if (k === 'name') name = safeDecode(v);
    }
    if (!key) return null;
    try {
      if (b64urlDecode(key).length !== 32) return null;
    } catch (e) {
      return null;
    }
    return { key, name: name.slice(0, 200) };
  }

  const api = {
    b64urlEncode, b64urlDecode, canonicalJson, canonicalBytes, fingerprint,
    verifyToken, extractToken, extractTokenFromPdf, parseKeyHash,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.StickVerify = api;

  if (typeof document === 'undefined') return;

  // ---------------------------------------------------------------------------
  // Page
  // ---------------------------------------------------------------------------

  const STRINGS = {
    de: {
      'page.title': 'Zertifikat prüfen – StickShredder',
      'app.subtitle': 'Löschzertifikat prüfen',
      'lang.toggle': 'EN',
      'lang.toggle.label': 'Switch to English',
      'privacy': 'Die Prüfung läuft vollständig in diesem Browser. Es werden keine Daten gesendet.',
      'check.title': 'Zertifikat prüfen',
      'check.hint': 'QR-Code des Zertifikats scannen, Prüflink oder Prüfcode einfügen oder das Zertifikat-PDF hierher ziehen.',
      'check.placeholder': 'Prüflink oder Prüfcode einfügen …',
      'check.button': 'Prüfen',
      'check.pdf': 'PDF auswählen',
      'check.drop': 'oder Zertifikat-PDF hier ablegen',
      'check.nopdf': 'In dieser Datei wurde kein StickShredder-Prüfcode gefunden.',
      'check.notoken': 'Das ist kein gültiger Prüflink oder Prüfcode.',
      'check.readerror': 'Die Datei konnte nicht gelesen werden.',
      'result.trusted': 'Echt — Schlüssel bekannt ({name})',
      'result.trusted.detail': 'Die Signatur ist gültig und stammt von einem Schlüssel, den Sie als vertraut gespeichert haben.',
      'result.unknown': 'Signatur gültig, Schlüssel unbekannt — Fingerabdruck mit der Schlüsselkarte vergleichen',
      'result.unknown.detail': 'Das Zertifikat ist seit der Erstellung unverändert. Ob es von der angegebenen Firma stammt, zeigt erst der Vergleich des Fingerabdrucks mit deren unterschriebener Schlüsselkarte.',
      'result.invalid': 'Ungültig oder verändert',
      'result.fingerprint': 'Fingerabdruck des Schlüssels',
      'reason.empty': 'Es wurde kein Prüfcode angegeben.',
      'reason.format': 'Der Prüfcode ist beschädigt oder unvollständig.',
      'reason.version': 'Dieses Zertifikatsformat wird von dieser Seite nicht unterstützt.',
      'reason.signature': 'Die Signatur passt nicht zu den Daten. Das Zertifikat wurde verändert oder nicht von StickShredder erstellt.',
      'reason.crypto_unavailable': 'Dieser Browser unterstützt die nötigen Kryptografie-Funktionen nicht. Bitte einen aktuellen Browser verwenden.',
      'reason.error': 'Bei der Prüfung ist ein unerwarteter Fehler aufgetreten.',
      'engine.webcrypto': 'Geprüft mit der Kryptografie des Browsers (Ed25519).',
      'engine.fallback': 'Geprüft mit der eingebauten Ed25519-Bibliothek.',
      'data.title': 'Zertifikatsdaten',
      'keycard.title': 'Schlüsselkarte',
      'keycard.hint': 'Vergleichen Sie diesen Fingerabdruck mit der unterschriebenen Schlüsselkarte, bevor Sie den Schlüssel speichern.',
      'keycard.company': 'Firma',
      'keycard.fingerprint': 'Fingerabdruck',
      'keycard.save': 'Als vertrauten Schlüssel speichern',
      'keycard.saved': 'Dieser Schlüssel ist als vertraut gespeichert.',
      'keycard.nostorage': 'Der Browser erlaubt kein Speichern. Der Schlüssel gilt nur, bis diese Seite geschlossen wird.',
      'keycard.unnamed': '(ohne Namen)',
      'trusted.title': 'Vertraute Schlüssel',
      'trusted.empty': 'Noch keine vertrauten Schlüssel. Scannen Sie den QR-Code einer Schlüsselkarte, um einen hinzuzufügen.',
      'trusted.remove': 'Entfernen',
      'trusted.remove.label': 'Schlüssel „{name}“ entfernen',
      'trusted.local': 'Gespeichert nur in diesem Browser.',
      'about.title': 'Was die Prüfung beweist — und was nicht',
      'about.1': 'Eine gültige Signatur beweist, dass das Zertifikat seit seiner Erstellung nicht verändert wurde und von der StickShredder-Installation mit diesem Schlüssel stammt.',
      'about.2': 'Der Fingerabdruck auf dem Zertifikat allein beweist nichts — ein Fälscher kann seinen eigenen aufdrucken. Erst der Abgleich mit der unterschriebenen Schlüsselkarte der Firma macht ein Zertifikat vertrauenswürdig.',
      'about.3': 'Die Signatur beweist nicht, dass der Datenträger tatsächlich physisch gelöscht wurde.',
      'noscript': 'Diese Seite benötigt JavaScript, um Zertifikate zu prüfen.',
      'empty': '—',
      'bytes': 'Bytes',
      'f.n': 'Zertifikatsnummer', 'f.t': 'Datum', 'f.op': 'Bediener', 'f.co': 'Firma',
      'f.dm': 'Gerätemodell', 'f.ds': 'Seriennummer', 'f.dc': 'Kapazität', 'f.dt': 'Anschluss / Typ',
      'f.m': 'Verfahren', 'f.p': 'Durchgänge', 'f.sk': 'Schutzklasse', 'f.sl': 'Sicherheitsstufe',
      'f.r': 'Ergebnis', 'f.vm': 'Prüfung', 'f.ve': 'Prüffehler', 'f.bs': 'Nicht beschreibbare Sektoren',
      'f.hpa': 'HPA/DCO', 'f.sw': 'Programmversion', 'f.h': 'Verlaufs-Hash', 'f.k': 'Öffentlicher Schlüssel',
      'f.v': 'Formatversion',
      'r.ok': 'Erfolgreich', 'r.limited': 'Eingeschränkt', 'r.failed': 'Fehlgeschlagen',
      'vm.none': 'Keine', 'vm.sample': 'Stichprobe', 'vm.full': 'Vollständig',
    },
    en: {
      'page.title': 'Verify certificate – StickShredder',
      'app.subtitle': 'Verify a deletion certificate',
      'lang.toggle': 'DE',
      'lang.toggle.label': 'Auf Deutsch umschalten',
      'privacy': 'Verification runs entirely in this browser. No data is sent anywhere.',
      'check.title': 'Verify a certificate',
      'check.hint': 'Scan the certificate’s QR code, paste the verify link or code, or drop the certificate PDF here.',
      'check.placeholder': 'Paste verify link or code …',
      'check.button': 'Verify',
      'check.pdf': 'Choose PDF',
      'check.drop': 'or drop the certificate PDF here',
      'check.nopdf': 'No StickShredder verification code was found in this file.',
      'check.notoken': 'This is not a valid verify link or code.',
      'check.readerror': 'The file could not be read.',
      'result.trusted': 'Genuine — key known ({name})',
      'result.trusted.detail': 'The signature is valid and was made with a key you saved as trusted.',
      'result.unknown': 'Signature valid, key unknown — compare the fingerprint with the key card',
      'result.unknown.detail': 'The certificate is unchanged since it was created. Whether it comes from the stated company only becomes clear by comparing the fingerprint with that company’s signed key card.',
      'result.invalid': 'Invalid or modified',
      'result.fingerprint': 'Key fingerprint',
      'reason.empty': 'No verification code was given.',
      'reason.format': 'The verification code is damaged or incomplete.',
      'reason.version': 'This certificate format is not supported by this page.',
      'reason.signature': 'The signature does not match the data. The certificate was modified or not created by StickShredder.',
      'reason.crypto_unavailable': 'This browser lacks the required cryptography features. Please use a current browser.',
      'reason.error': 'An unexpected error occurred during verification.',
      'engine.webcrypto': 'Verified with the browser’s built-in cryptography (Ed25519).',
      'engine.fallback': 'Verified with the bundled Ed25519 library.',
      'data.title': 'Certificate data',
      'keycard.title': 'Key card',
      'keycard.hint': 'Compare this fingerprint with the signed key card before saving the key.',
      'keycard.company': 'Company',
      'keycard.fingerprint': 'Fingerprint',
      'keycard.save': 'Save as trusted key',
      'keycard.saved': 'This key is saved as trusted.',
      'keycard.nostorage': 'This browser does not allow saving. The key only stays trusted until this page is closed.',
      'keycard.unnamed': '(no name)',
      'trusted.title': 'Trusted keys',
      'trusted.empty': 'No trusted keys yet. Scan a key card’s QR code to add one.',
      'trusted.remove': 'Remove',
      'trusted.remove.label': 'Remove key “{name}”',
      'trusted.local': 'Stored only in this browser.',
      'about.title': 'What verification proves — and what it does not',
      'about.1': 'A valid signature proves that the certificate has not been changed since it was created and that it comes from the StickShredder installation holding this key.',
      'about.2': 'The fingerprint printed on the certificate proves nothing by itself — a forger can print their own. Only comparing it with the company’s signed key card makes a certificate trustworthy.',
      'about.3': 'The signature does not prove that the drive was actually physically erased.',
      'noscript': 'This page needs JavaScript to verify certificates.',
      'empty': '—',
      'bytes': 'bytes',
      'f.n': 'Certificate number', 'f.t': 'Date', 'f.op': 'Operator', 'f.co': 'Company',
      'f.dm': 'Device model', 'f.ds': 'Serial number', 'f.dc': 'Capacity', 'f.dt': 'Connection / type',
      'f.m': 'Method', 'f.p': 'Passes', 'f.sk': 'Protection class', 'f.sl': 'Security level',
      'f.r': 'Result', 'f.vm': 'Verification', 'f.ve': 'Verification errors', 'f.bs': 'Unwritable sectors',
      'f.hpa': 'HPA/DCO', 'f.sw': 'Software version', 'f.h': 'History hash', 'f.k': 'Public key',
      'f.v': 'Format version',
      'r.ok': 'Successful', 'r.limited': 'Limited', 'r.failed': 'Failed',
      'vm.none': 'None', 'vm.sample': 'Sample', 'vm.full': 'Full',
    },
  };

  const FIELD_ORDER = ['n', 't', 'co', 'op', 'dm', 'ds', 'dc', 'dt', 'm', 'p', 'sk', 'sl', 'r', 'vm', 've', 'bs', 'hpa', 'sw', 'h', 'k', 'v'];
  const STORE_KEY = 'stickshredder.verify.trustedKeys';
  const LANG_KEY = 'stickshredder.verify.lang';

  const state = {
    lang: 'de',
    trusted: {}, // fingerprint -> { name, key }
    storageOk: true,
    lastResult: null,
    keycard: null, // { key, name, fingerprint }
    message: null, // i18n key for the input message line
  };

  function t(key, vars) {
    const table = STRINGS[state.lang] || STRINGS.de;
    let s = Object.prototype.hasOwnProperty.call(table, key) ? table[key] : (STRINGS.de[key] || key);
    if (vars) {
      for (const k of Object.keys(vars)) s = s.split('{' + k + '}').join(vars[k]);
    }
    return s;
  }

  // Builds elements with textContent only; payload data never touches innerHTML.
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const k of Object.keys(attrs)) {
        if (k === 'text') node.textContent = attrs[k];
        else if (k === 'class') node.className = attrs[k];
        else if (k === 'onclick') node.addEventListener('click', attrs[k]);
        else node.setAttribute(k, attrs[k]);
      }
    }
    for (const c of children || []) if (c) node.appendChild(c);
    return node;
  }

  function $(id) {
    return document.getElementById(id);
  }

  // --- storage (optional) ---------------------------------------------------

  function storageGet(key) {
    try {
      return root.localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      root.localStorage.setItem(key, value);
      return true;
    } catch (e) {
      return false;
    }
  }

  function loadTrusted() {
    const raw = storageGet(STORE_KEY);
    if (!raw) return {};
    try {
      const data = JSON.parse(raw);
      const out = {};
      if (data && typeof data === 'object') {
        for (const fp of Object.keys(data)) {
          const e = data[fp];
          if (e && typeof e.key === 'string' && /^[0-9A-F]{4}(-[0-9A-F]{4}){7}$/.test(fp)) {
            out[fp] = { name: String(e.name || '').slice(0, 200), key: e.key };
          }
        }
      }
      return out;
    } catch (e) {
      return {};
    }
  }

  function saveTrusted() {
    state.storageOk = storageSet(STORE_KEY, JSON.stringify(state.trusted));
  }

  // --- formatting -------------------------------------------------------------

  function locale() {
    return state.lang === 'de' ? 'de-DE' : 'en-GB';
  }

  function formatValue(key, value) {
    if (value === '' || value === null || value === undefined) return t('empty');
    if (key === 'r' && typeof value === 'string' && STRINGS.de['r.' + value]) return t('r.' + value);
    if (key === 'vm' && typeof value === 'string' && STRINGS.de['vm.' + value]) return t('vm.' + value);
    if (key === 'dc' && typeof value === 'number' && Number.isFinite(value)) {
      const nf = new Intl.NumberFormat(locale(), { maximumFractionDigits: 2 });
      const gb = value / 1e9;
      const exact = new Intl.NumberFormat(locale()).format(value);
      return nf.format(gb) + ' GB (' + exact + ' ' + t('bytes') + ')';
    }
    if (key === 't' && typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) {
      const d = new Date(value);
      if (!isNaN(d.getTime())) {
        return new Intl.DateTimeFormat(locale(), {
          dateStyle: 'medium', timeStyle: 'medium', timeZone: 'UTC',
        }).format(d) + ' UTC';
      }
    }
    if (typeof value === 'number' && Number.isInteger(value)) {
      return key === 'n' ? String(value) : new Intl.NumberFormat(locale()).format(value);
    }
    if (typeof value === 'string') return value;
    try {
      return JSON.stringify(value);
    } catch (e) {
      return String(value);
    }
  }

  // --- rendering --------------------------------------------------------------

  function renderStatic() {
    document.documentElement.lang = state.lang;
    document.title = t('page.title');
    for (const node of document.querySelectorAll('[data-i18n]')) node.textContent = t(node.getAttribute('data-i18n'));
    for (const node of document.querySelectorAll('[data-i18n-placeholder]')) {
      node.setAttribute('placeholder', t(node.getAttribute('data-i18n-placeholder')));
    }
    const toggle = $('lang-toggle');
    toggle.textContent = t('lang.toggle');
    toggle.setAttribute('aria-label', t('lang.toggle.label'));
    toggle.setAttribute('lang', state.lang === 'de' ? 'en' : 'de');
  }

  function renderMessage() {
    const box = $('check-message');
    box.textContent = state.message ? t(state.message) : '';
    box.hidden = !state.message;
  }

  function renderResult() {
    const section = $('result');
    const banner = $('banner');
    const tableWrap = $('data');
    const res = state.lastResult;
    banner.replaceChildren();
    tableWrap.replaceChildren();
    if (!res) {
      section.hidden = true;
      return;
    }
    section.hidden = false;

    const kind = res.status === 'trusted' ? 'ok' : res.status === 'unknown_key' ? 'warn' : 'bad';
    banner.className = 'banner banner-' + kind;
    const icon = el('span', { class: 'banner-icon', 'aria-hidden': 'true', text: kind === 'ok' ? '✓' : kind === 'warn' ? '!' : '✕' });
    let headline;
    let detail;
    if (res.status === 'trusted') {
      headline = t('result.trusted', { name: res.trustedName || t('keycard.unnamed') });
      detail = t('result.trusted.detail');
    } else if (res.status === 'unknown_key') {
      headline = t('result.unknown');
      detail = t('result.unknown.detail');
    } else {
      headline = t('result.invalid');
      detail = t('reason.' + res.reason);
    }
    const body = el('div', { class: 'banner-body' }, [
      el('p', { class: 'banner-title', text: headline }),
      el('p', { class: 'banner-detail', text: detail }),
    ]);
    if (res.fingerprint) {
      body.appendChild(el('p', { class: 'banner-fp' }, [
        el('span', { class: 'banner-fp-label', text: t('result.fingerprint') + ': ' }),
        el('code', { class: 'fp', text: res.fingerprint }),
      ]));
    }
    banner.appendChild(icon);
    banner.appendChild(body);

    if (res.status === 'invalid' || !res.payload) return;

    const rows = [];
    const seen = new Set();
    const p = res.payload;
    const addRow = (key) => {
      seen.add(key);
      rows.push(el('tr', null, [
        el('th', { scope: 'row', text: STRINGS.de['f.' + key] ? t('f.' + key) : key }),
        el('td', { class: key === 'k' || key === 'h' || key === 'ds' ? 'mono' : '', text: formatValue(key, p[key]) }),
      ]));
    };
    for (const key of FIELD_ORDER) if (Object.prototype.hasOwnProperty.call(p, key)) addRow(key);
    for (const key of Object.keys(p).sort()) if (!seen.has(key)) addRow(key);

    tableWrap.appendChild(el('h2', { text: t('data.title') }));
    tableWrap.appendChild(el('table', { class: 'data-table' }, [el('tbody', null, rows)]));
    if (res.engine) tableWrap.appendChild(el('p', { class: 'muted small', text: t('engine.' + res.engine) }));
  }

  function renderKeycard() {
    const section = $('keycard');
    section.replaceChildren();
    const kc = state.keycard;
    if (!kc) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    const already = Object.prototype.hasOwnProperty.call(state.trusted, kc.fingerprint) && state.trusted[kc.fingerprint].key === kc.key;
    section.appendChild(el('h2', { text: t('keycard.title') }));
    section.appendChild(el('dl', { class: 'kc' }, [
      el('dt', { text: t('keycard.company') }),
      el('dd', { class: 'kc-name', text: kc.name || t('keycard.unnamed') }),
      el('dt', { text: t('keycard.fingerprint') }),
      el('dd', null, [el('code', { class: 'fp fp-large', text: kc.fingerprint })]),
    ]));
    if (already) {
      section.appendChild(el('p', { class: 'note note-ok', text: t('keycard.saved') }));
    } else {
      section.appendChild(el('p', { class: 'muted', text: t('keycard.hint') }));
      section.appendChild(el('button', { type: 'button', class: 'btn btn-primary', text: t('keycard.save'), onclick: onSaveKey }));
    }
    if (!state.storageOk) section.appendChild(el('p', { class: 'note note-warn', text: t('keycard.nostorage') }));
  }

  function renderTrusted() {
    const list = $('trusted-list');
    list.replaceChildren();
    const fps = Object.keys(state.trusted).sort((a, b) => state.trusted[a].name.localeCompare(state.trusted[b].name));
    $('trusted-empty').hidden = fps.length > 0;
    for (const fp of fps) {
      const entry = state.trusted[fp];
      const name = entry.name || t('keycard.unnamed');
      list.appendChild(el('li', { class: 'trusted-item' }, [
        el('div', { class: 'trusted-text' }, [
          el('span', { class: 'trusted-name', text: name }),
          el('code', { class: 'fp', text: fp }),
        ]),
        el('button', {
          type: 'button', class: 'btn btn-small', text: t('trusted.remove'),
          'aria-label': t('trusted.remove.label', { name }),
          onclick: () => onRemoveKey(fp),
        }),
      ]));
    }
  }

  function renderAll() {
    renderStatic();
    renderMessage();
    renderResult();
    renderKeycard();
    renderTrusted();
  }

  // --- actions ----------------------------------------------------------------

  let runId = 0;

  async function checkToken(token, scroll) {
    const id = ++runId;
    state.message = null;
    state.keycard = null;
    renderKeycard();
    const res = await verifyToken(token, state.trusted);
    if (id !== runId) return;
    state.lastResult = res;
    renderMessage();
    renderResult();
    if (scroll) $('result').scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  async function showKeycard(card) {
    const id = ++runId;
    let fp;
    try {
      fp = await fingerprint(b64urlDecode(card.key));
    } catch (e) {
      state.lastResult = invalid('crypto_unavailable');
      renderResult();
      return;
    }
    if (id !== runId) return;
    state.keycard = { key: card.key, name: card.name, fingerprint: fp };
    state.lastResult = null;
    state.lastToken = null;
    renderKeycard();
    renderResult();
  }

  async function reverify() {
    if (state.lastResult && state.lastResult.status !== 'invalid' && state.lastToken) {
      state.lastResult = await verifyToken(state.lastToken, state.trusted);
      renderResult();
    }
  }

  function onSaveKey() {
    const kc = state.keycard;
    if (!kc) return;
    state.trusted[kc.fingerprint] = { name: kc.name, key: kc.key };
    saveTrusted();
    renderKeycard();
    renderTrusted();
    reverify();
  }

  function onRemoveKey(fp) {
    delete state.trusted[fp];
    saveTrusted();
    renderKeycard();
    renderTrusted();
    reverify();
  }

  function handleInput(text) {
    const card = parseKeyHash((text || '').trim().replace(/^[^#]*#/, ''));
    if (card) {
      state.message = null;
      renderMessage();
      showKeycard(card);
      return;
    }
    const token = extractToken(text);
    if (!token) {
      state.message = 'check.notoken';
      renderMessage();
      return;
    }
    state.lastToken = token;
    checkToken(token, true);
  }

  async function handleFile(file) {
    if (!file) return;
    let buf;
    try {
      buf = await file.arrayBuffer();
    } catch (e) {
      state.message = 'check.readerror';
      renderMessage();
      return;
    }
    const token = extractTokenFromPdf(buf);
    if (!token) {
      state.lastResult = null;
      state.message = 'check.nopdf';
      renderMessage();
      renderResult();
      return;
    }
    state.lastToken = token;
    checkToken(token, true);
  }

  function route() {
    const hash = root.location.hash || '';
    if (hash.length <= 1) return;
    const card = parseKeyHash(hash);
    if (card) {
      showKeycard(card);
      return;
    }
    const token = extractToken(hash);
    if (token) {
      state.lastToken = token;
      checkToken(token);
    } else {
      state.lastResult = invalid('format');
      renderResult();
    }
  }

  function init() {
    const savedLang = storageGet(LANG_KEY);
    if (savedLang === 'en' || savedLang === 'de') state.lang = savedLang;
    state.trusted = loadTrusted();

    $('lang-toggle').addEventListener('click', () => {
      state.lang = state.lang === 'de' ? 'en' : 'de';
      storageSet(LANG_KEY, state.lang);
      renderAll();
    });

    $('check-form').addEventListener('submit', (ev) => {
      ev.preventDefault();
      handleInput($('token-input').value);
    });

    $('pdf-input').addEventListener('change', (ev) => {
      const f = ev.target.files && ev.target.files[0];
      handleFile(f);
      ev.target.value = '';
    });

    const drop = $('drop-zone');
    const stop = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
    };
    ['dragenter', 'dragover'].forEach((type) => drop.addEventListener(type, (ev) => {
      stop(ev);
      drop.classList.add('drag');
    }));
    ['dragleave', 'drop'].forEach((type) => drop.addEventListener(type, (ev) => {
      stop(ev);
      drop.classList.remove('drag');
    }));
    drop.addEventListener('drop', (ev) => {
      const f = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
      handleFile(f);
    });
    // Keep a stray drop elsewhere from navigating away to the PDF.
    root.addEventListener('dragover', (ev) => ev.preventDefault());
    root.addEventListener('drop', (ev) => ev.preventDefault());

    root.addEventListener('hashchange', route);

    renderAll();
    route();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(typeof globalThis !== 'undefined' ? globalThis : this);
