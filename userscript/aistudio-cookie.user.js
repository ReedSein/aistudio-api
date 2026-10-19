// ==UserScript==
// @name         AI Studio Cookie 抓取推送 (aistudio-api)
// @namespace    https://github.com/ReedSein/aistudio-api-termux
// @version      0.1.3
// @description  在 aistudio.google.com 抓取登录 Cookie，推送到自家 aistudio-api 服务并探活导入全部已登录账号
// @author       ReedSein
// @match        https://aistudio.google.com/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_listValues
// @grant        GM_deleteValue
// @grant        GM_info
// @grant        GM_xmlhttpRequest
// @grant        GM_cookie
// @grant        GM_registerMenuCommand
// @connect      localhost
// @connect      127.0.0.1
// @connect      *
// @run-at       document-idle
// @noframes
// @updateURL    __AISTUDIO_SELF_URL__
// @downloadURL  __AISTUDIO_SELF_URL__
// ==/UserScript==

/*
 * 本脚本由 aistudio-api 服务托管，@updateURL / @downloadURL 会按实际访问地址自动改写。
 *
 * 工作原理：
 * 1. 读取当前页面可读的 Cookie（document.cookie，其中 SID / SAPISID 等核心认证项本就不在
 *    HttpOnly 之列）；若脚本管理器支持 GM_cookie，则再读取浏览器对当前地址实际发送的完整
 *    集合（含 HSID / SSID 等 HttpOnly 项），合并后作为推送数据。
 * 2. 推送到服务端 POST /api/accounts/probe-import，服务端会用这份 Cookie 探活
 *    u/0, u/1, ... 全部已登录 Google 账号并一键导入；缺的凭据由服务端浏览器自行协商补齐。
 *
 * 设置（服务地址、管理密码、面板位置）保存在脚本管理器本地存储中，密码仅发送到所填地址。
 *
 * 注意：aistudio.google.com 启用了 Trusted Types 安全策略，会拦截 innerHTML 赋值。
 * 面板全部用 createElement 构建；初始化失败会弹出提示，不再静默消失。
 */

(function () {
  'use strict';

  const CFG_SERVER = 'server';
  const CFG_PASSWORD = 'password';
  const CFG_POS = 'position';
  const CFG_OPEN = 'open';
  const DEFAULT_SERVER = 'http://127.0.0.1:11451';
  const PUSH_TIMEOUT_MS = 300000;
  const HOST_ID = 'aistudio-cookie-helper';
  const SCRIPT_VERSION = '0.1.3';
  const DIAG_PREFIX = 'aistudio_diagnostic_event_v1_';
  const DIAG_META = 'aistudio_diagnostic_meta_v1';
  const DIAG_MAX_BYTES = 1024 * 1024;
  const DIAG_MAX_AGE = 7 * 86400000;
  const EVENT_STAGES = ['script_start', 'cookie_start', 'cookie_end', 'request_start', 'request_callback', 'request_settled', 'result', 'export_start', 'export_result', 'unexpected_error'];
  const EVENT_FIELDS = ['id', 'session_id', 'operation_id', 'timestamp', 'script_version', 'sequence', 'stage', 'callback', 'outcome', 'reason', 'elapsed_ms', 'status', 'ready_state', 'loaded_bytes', 'response_bytes', 'request_id', 'cookie_count', 'imported_count', 'missing_cookie_names', 'gm_supported', 'gm_failed', 'enhanced', 'error_type', 'error_stack', 'server_origin', 'duplicate_callback'];
  let storageError = false;
  let memoryEvents = [];
  let sequence = 0;
  let downloadUrl = null;

  function identifier() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  }

  let sessionId;

  function jsonBytes(value) {
    return new TextEncoder().encode(JSON.stringify(value)).length;
  }

  function storedBytes(event) {
    return jsonBytes(event) + DIAG_PREFIX.length + event.id.length + 4;
  }

  function errorDetails(error) {
    const name = error && typeof error.name === 'string' && /^[A-Za-z]{1,64}$/.test(error.name) ? error.name : 'Error';
    // Stack frames help locate code; exception messages and URL credentials/query do not belong in diagnostics.
    const frames = error && typeof error.stack === 'string' ? error.stack.split('\n').slice(1) : [];
    const locations = frames.flatMap(frame => {
      const match = frame.match(/:(\d+):(\d+)\)?$/);
      return match ? ['at [script]:' + match[1] + ':' + match[2]] : [];
    });
    let stack = '';
    for (const location of locations) {
      const next = stack ? stack + '\n' + location : location;
      if (next.length > 8192) break;
      stack = next;
    }
    return { error_type: name, error_stack: stack };
  }

  function validEvent(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    if (Object.keys(value).some(key => !EVENT_FIELDS.includes(key))) return false;
    if (![value.id, value.session_id, value.operation_id].every(id => typeof id === 'string' && /^[a-f0-9]{32}$/.test(id))) return false;
    if (!EVENT_STAGES.includes(value.stage) || typeof value.timestamp !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value.timestamp) || !Number.isFinite(Date.parse(value.timestamp))) return false;
    if (!Number.isSafeInteger(value.sequence) || value.sequence < 0 || typeof value.script_version !== 'string' || value.script_version.length > 32 || !/^\d+\.\d+\.\d+$/.test(value.script_version)) return false;
    const enums = {
      callback: ['load', 'error', 'timeout', 'abort', 'ready_state', 'progress', 'throw'],
      outcome: ['ok', 'unconfirmed', 'auth', 'http', 'no_cookies', 'local_only', 'combined', 'error'],
      reason: ['network_error', 'timeout', 'aborted', 'invalid_json', 'invalid_response', 'unexpected_error', 'local_export', 'archive_rejected', 'download_failed']
    };
    for (const [key, choices] of Object.entries(enums)) if (key in value && !choices.includes(value[key])) return false;
    const integers = { status: 599, ready_state: 4, loaded_bytes: Number.MAX_SAFE_INTEGER, response_bytes: Number.MAX_SAFE_INTEGER, cookie_count: 10000, imported_count: 10000 };
    for (const [key, maximum] of Object.entries(integers)) if (key in value && (!Number.isSafeInteger(value[key]) || value[key] < 0 || value[key] > maximum)) return false;
    if ('elapsed_ms' in value && (!Number.isFinite(value.elapsed_ms) || value.elapsed_ms < 0 || value.elapsed_ms > DIAG_MAX_AGE)) return false;
    for (const key of ['gm_supported', 'gm_failed', 'enhanced', 'duplicate_callback']) if (key in value && typeof value[key] !== 'boolean') return false;
    if ('request_id' in value && (typeof value.request_id !== 'string' || !/^[a-f0-9]{32}$/.test(value.request_id))) return false;
    if ('error_type' in value && (typeof value.error_type !== 'string' || !/^[A-Za-z]{1,64}$/.test(value.error_type))) return false;
    if ('error_stack' in value && (typeof value.error_stack !== 'string' || value.error_stack.length > 8192 || !/^(?:at \[script\]:\d+:\d+(?:\n|$))*$/.test(value.error_stack))) return false;
    if ('missing_cookie_names' in value && (!Array.isArray(value.missing_cookie_names) || value.missing_cookie_names.length > 8 || value.missing_cookie_names.some(name => !CORE_COOKIE_NAMES.includes(name)))) return false;
    if ('server_origin' in value) {
      try {
        const origin = new URL(value.server_origin);
        if (typeof value.server_origin !== 'string' || value.server_origin.length > 512 || !['http:', 'https:'].includes(origin.protocol) || origin.origin !== value.server_origin) return false;
      } catch (error) { return false; }
    }
    return jsonBytes(value) <= 16384;
  }

  function diagnosticRecords() {
    const events = new Map(memoryEvents.map(event => [event.id, event]));
    const storedIds = new Set();
    try {
      for (const key of GM_listValues()) {
        if (!new RegExp('^' + DIAG_PREFIX + '[a-f0-9]{32}$').test(key)) continue;
        const event = GM_getValue(key, null);
        if (validEvent(event) && key === DIAG_PREFIX + event.id) { events.set(event.id, event); storedIds.add(event.id); }
        else {
          storageError = true;
          GM_deleteValue(key);
        }
      }
      memoryEvents = memoryEvents.filter(event => !storedIds.has(event.id));
    } catch (error) {
      storageError = true;
    }
    return Array.from(events.values()).sort((a, b) => a.timestamp.localeCompare(b.timestamp) || a.session_id.localeCompare(b.session_id) || a.sequence - b.sequence);
  }

  function diagnosticMeta() {
    try {
      const stored = GM_getValue(DIAG_META, {});
      return {
        pruned_events: Number.isSafeInteger(stored.pruned_events) && stored.pruned_events >= 0 ? stored.pruned_events : 0,
        pruned_bytes: Number.isSafeInteger(stored.pruned_bytes) && stored.pruned_bytes >= 0 ? stored.pruned_bytes : 0,
        storage_error: storageError || stored.storage_error === true
      };
    } catch (error) {
      storageError = true;
      return { pruned_events: 0, pruned_bytes: 0, storage_error: true };
    }
  }

  function retainedEvents() {
    const events = diagnosticRecords();
    let bytes = events.reduce((sum, event) => sum + storedBytes(event), 0);
    const meta = diagnosticMeta();
    let removed = 0;
    while (events.length && (Date.parse(events[0].timestamp) < Date.now() - DIAG_MAX_AGE || bytes > DIAG_MAX_BYTES - 8192 || events.length > 5000)) {
      const event = events.shift();
      const size = storedBytes(event);
      bytes -= size;
      removed += 1;
      meta.pruned_events += 1;
      meta.pruned_bytes += size;
      memoryEvents = memoryEvents.filter(row => row.id !== event.id);
      try { GM_deleteValue(DIAG_PREFIX + event.id); } catch (error) { storageError = true; }
    }
    if (removed || storageError) {
      meta.storage_error = meta.storage_error || storageError;
      try { GM_setValue(DIAG_META, meta); } catch (error) { storageError = true; }
    }
    return events;
  }

  function record(stage, operation, fields = {}) {
    const event = { id: identifier(), session_id: sessionId, operation_id: operation, timestamp: new Date().toISOString(), script_version: SCRIPT_VERSION, sequence: sequence++, stage, ...fields };
    memoryEvents.push(event);
    try {
      GM_setValue(DIAG_PREFIX + event.id, event);
    } catch (error) { storageError = true; }
    retainedEvents();
    if (els && els.diagnosticsHint && storageError) els.diagnosticsHint.textContent = '本地诊断保存存在错误，请在关闭页面前导出；历史记录可能不完整。';
  }

  function browserReport() {
    const events = retainedEvents();
    const info = typeof GM_info === 'object' && GM_info ? GM_info : {};
    return {
      schema_version: 1, script_version: SCRIPT_VERSION,
      browser: String(navigator.userAgent || 'unknown').slice(0, 256),
      manager: String(info.scriptHandler || 'unknown').slice(0, 64),
      manager_version: String(info.version || 'unknown').slice(0, 64),
      ...diagnosticMeta(), events
    };
  }

  const CORE_COOKIE_NAMES = [
    'SID',
    'HSID',
    'SSID',
    'SAPISID',
    '__Secure-1PAPISID',
    '__Secure-3PAPISID',
    '__Secure-1PSID',
    '__Secure-3PSID'
  ];

  if (document.getElementById(HOST_ID)) return;

  let host = null;
  let shadow = null;
  let els = null;

  function getCfg(key, fallback) {
    try {
      const value = GM_getValue(key, fallback);
      return value === undefined ? fallback : value;
    } catch (error) {
      return fallback;
    }
  }

  function setCfg(key, value) {
    try {
      GM_setValue(key, value);
    } catch (error) {
      /* 本地存储不可用时仅影响设置记忆，不影响推送 */
    }
  }

  // ---------- Cookie 抓取 ----------

  function readDocumentCookies() {
    const map = new Map();
    (document.cookie || '').split(';').forEach((pair) => {
      const idx = pair.indexOf('=');
      if (idx < 1) return;
      const name = pair.slice(0, idx).trim();
      const value = pair.slice(idx + 1).trim();
      if (name && value !== '') map.set(name, value);
    });
    return map;
  }

  function isGmCookieAvailable() {
    return (
      typeof GM_cookie !== 'undefined' &&
      GM_cookie !== null &&
      typeof GM_cookie.list === 'function'
    );
  }

  function gmCookieList(query) {
    return new Promise((resolve) => {
      try {
        GM_cookie.list(query, (cookies, error) => {
          if (error || !Array.isArray(cookies)) {
            resolve({ cookies: [], failed: true });
            return;
          }
          resolve({ cookies, failed: false });
        });
      } catch (error) {
        resolve({ cookies: [], failed: true });
      }
    });
  }

  function collectCookies() {
    const map = readDocumentCookies();
    const gmSupported = isGmCookieAvailable();
    if (!gmSupported) {
      return Promise.resolve({ cookies: map, enhanced: false, gmSupported, gmFailed: false });
    }
    return gmCookieList({ url: location.href }).then((result) => {
      let enhanced = false;
      result.cookies.forEach((cookie) => {
        if (!cookie || !cookie.name) return;
        const name = String(cookie.name);
        const value = cookie.value == null ? '' : String(cookie.value);
        if (value === '') return;
        if (!map.has(name)) enhanced = true;
        map.set(name, value);
      });
      return { cookies: map, enhanced, gmSupported, gmFailed: result.failed };
    });
  }

  function toCookieString(map) {
    const parts = [];
    map.forEach((value, name) => {
      parts.push(name + '=' + value);
    });
    return parts.join('; ');
  }

  // ---------- 邮箱识别（尽力而为，仅用于面板显示） ----------

  function detectEmail() {
    const emailRe = /[\w.+-]+@[\w.-]+\.\w+/;
    try {
      const links = document.querySelectorAll(
        'a[href*="accounts.google.com"], a[href*="myaccount.google.com"]'
      );
      for (const link of links) {
        const match = (link.getAttribute('aria-label') || '').match(emailRe);
        if (match) return match[0].toLowerCase();
      }
    } catch (error) {
      /* ignore */
    }
    try {
      const images = document.querySelectorAll('img[alt]');
      for (const img of images) {
        const match = (img.getAttribute('alt') || '').match(emailRe);
        if (match) return match[0].toLowerCase();
      }
    } catch (error) {
      /* ignore */
    }
    try {
      const match = (document.body.innerText || '').match(emailRe);
      if (match) return match[0].toLowerCase();
    } catch (error) {
      /* ignore */
    }
    return null;
  }

  // ---------- 推送 ----------

  function normalizeServer(raw) {
    let server = String(raw || '').trim() || DEFAULT_SERVER;
    if (!/^https?:\/\//i.test(server)) server = 'http://' + server;
    return server.replace(/\/+$/, '');
  }

  function postJSON(url, password, payload, operation, responseType = 'text') {
    return new Promise((resolve) => {
      const started = performance.now();
      let settled = false;
      let previousState = -1;
      const headers = { 'Content-Type': 'application/json' };
      if (password) headers.Authorization = 'Bearer ' + password;
      headers['X-Aistudio-Operation-ID'] = operation;
      let origin;
      try { origin = new URL(url).origin; } catch (error) { origin = undefined; }
      const metadata = response => {
        const data = { elapsed_ms: Math.max(0, performance.now() - started) };
        if (response && Number.isInteger(response.status) && response.status >= 0 && response.status <= 599) data.status = response.status;
        if (response && Number.isInteger(response.readyState) && response.readyState >= 0 && response.readyState <= 4) data.ready_state = response.readyState;
        if (response && Number.isSafeInteger(response.loaded) && response.loaded >= 0) data.loaded_bytes = response.loaded;
        const requestId = response && typeof response.responseHeaders === 'string' ? response.responseHeaders.match(/^x-request-id:\s*([a-f0-9]{32})\s*$/im) : null;
        if (requestId) data.request_id = requestId[1];
        return data;
      };
      const finish = (callback, response, error) => {
        const meta = metadata(response);
        if (responseType === 'text' && response && typeof response.responseText === 'string') meta.response_bytes = new TextEncoder().encode(response.responseText).length;
        else if (responseType === 'blob' && response && response.response && Number.isSafeInteger(response.response.size) && response.response.size >= 0) meta.response_bytes = response.response.size;
        record('request_callback', operation, { callback, ...meta, duplicate_callback: settled, ...(error ? errorDetails(error) : {}) });
        if (settled) return;
        settled = true;
        record('request_settled', operation, { callback, ...meta });
        resolve({ callback, status: meta.status || 0, text: responseType === 'text' && response && typeof response.responseText === 'string' ? response.responseText : '', blob: responseType === 'blob' && response ? response.response : null });
      };
      record('request_start', operation, origin ? { server_origin: origin } : {});
      try { GM_xmlhttpRequest({
        method: 'POST',
        url,
        headers,
        data: JSON.stringify(payload),
        timeout: PUSH_TIMEOUT_MS,
        responseType,
        onreadystatechange: response => {
          if (response.readyState === previousState) return;
          previousState = response.readyState;
          record('request_callback', operation, { callback: 'ready_state', ...metadata(response) });
        },
        onprogress: response => record('request_callback', operation, { callback: 'progress', ...metadata(response) }),
        onload: response => finish('load', response),
        onerror: response => finish('error', response),
        ontimeout: response => finish('timeout', response),
        onabort: response => finish('abort', response)
      }); } catch (error) { finish('throw', null, error); }
    });
  }

  function extractServerMessage(text) {
    try {
      const data = JSON.parse(text);
      if (data && data.error && data.error.message) return String(data.error.message);
      if (data && typeof data.detail === 'string') return data.detail;
    } catch (error) {
      /* ignore */
    }
    return '';
  }

  async function pushCookies(server, password, cookieString, operation) {
    const response = await postJSON(
      server + '/api/accounts/probe-import',
      password,
      { cookies: cookieString },
      operation
    );
    if (response.callback !== 'load' || response.status === 0) {
      return { kind: 'unconfirmed', reason: response.callback === 'timeout' ? 'timeout' : response.callback === 'abort' ? 'aborted' : 'network_error' };
    }
    if (response.status === 200) {
      let data = null;
      try {
        data = JSON.parse(response.text);
      } catch (error) {
        return { kind: 'unconfirmed', reason: 'invalid_json' };
      }
      if (!data || !Number.isSafeInteger(data.imported_count) || data.imported_count < 0 || data.imported_count > 10000 || !Array.isArray(data.accounts) || data.accounts.length !== data.imported_count || data.accounts.some(account => !account || typeof account.id !== 'string' || typeof account.name !== 'string' || (account.email != null && typeof account.email !== 'string'))) {
        return { kind: 'unconfirmed', reason: 'invalid_response' };
      }
      return { kind: 'ok', count: data.imported_count, accounts: data.accounts };
    }
    if (response.status === 401) return { kind: 'auth' };
    return {
      kind: 'http',
      status: response.status,
      message: extractServerMessage(response.text)
    };
  }

  // ---------- 面板（Shadow DOM 隔离） ----------

  const PANEL_CSS = [
    ':host { all: initial; }',
    '.wrap { display: flex; flex-direction: column; align-items: flex-end; gap: 8px;',
    '  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }',
    '.panel { width: min(340px, calc(100vw - 16px)); background: #ffffff; color: #111827;',
    '  border-radius: 16px; box-shadow: 0 10px 32px rgba(0, 0, 0, 0.28); overflow: hidden; }',
    '.head { display: flex; align-items: center; justify-content: space-between;',
    '  padding: 10px 12px; background: #111827; color: #f9fafb; font-size: 13px; font-weight: 600;',
    '  cursor: grab; touch-action: none; user-select: none; -webkit-user-select: none; }',
    '.headBtn { border: 0; background: rgba(255, 255, 255, 0.14); color: #f9fafb;',
    '  font-size: 12px; padding: 4px 10px; border-radius: 999px; cursor: pointer; }',
    '.body { display: flex; flex-direction: column; gap: 10px; padding: 12px; }',
    '.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #374151; }',
    '.field input { width: 100%; box-sizing: border-box; font-size: 16px; padding: 8px 10px;',
    '  border: 1px solid #d1d5db; border-radius: 10px; outline: none; background: #f9fafb; color: #111827; }',
    '.field input:focus { border-color: #2563eb; background: #ffffff; }',
    '.big { min-height: 48px; border: 0; border-radius: 12px; background: #2563eb; color: #ffffff;',
    '  font-size: 15px; font-weight: 600; cursor: pointer; }',
    '.big:disabled { opacity: 0.6; cursor: default; }',
    '.hint { font-size: 11px; color: #9ca3af; line-height: 1.5; word-break: break-all; }',
    '.status { font-size: 12px; color: #4b5563; line-height: 1.5; word-break: break-all; }',
    '.status.ok { color: #059669; }',
    '.status.err { color: #dc2626; }',
    '.bubble { width: 48px; height: 48px; border-radius: 50%; border: 0; cursor: pointer;',
    '  background: #111827; color: #ffffff; font-size: 22px; line-height: 1;',
    '  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.3); display: flex; align-items: center;',
    '  justify-content: center; touch-action: manipulation; }'
  ].join('\n');

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function buildPanel() {
    const style = document.createElement('style');
    style.textContent = PANEL_CSS;

    const wrap = el('div', 'wrap');

    const panel = el('div', 'panel');
    const head = el('div', 'head');
    head.appendChild(el('span', null, 'AI Studio Cookie 抓取'));
    const headBtn = el('button', 'headBtn', '收起');
    headBtn.type = 'button';
    head.appendChild(headBtn);
    panel.appendChild(head);

    const body = el('div', 'body');
    const serverLabel = el('label', 'field', '服务地址');
    const serverInput = el('input', 'server');
    serverInput.type = 'text';
    serverInput.spellcheck = false;
    serverLabel.appendChild(serverInput);
    const passwordLabel = el('label', 'field', '管理密码（可选，服务设置了才需要）');
    const passwordInput = el('input', 'password');
    passwordInput.type = 'password';
    passwordInput.spellcheck = false;
    passwordLabel.appendChild(passwordInput);
    const pushBtn = el('button', 'big push', '抓取并推送');
    pushBtn.type = 'button';
    const exportBtn = el('button', 'headBtn export', '导出诊断包');
    exportBtn.type = 'button';
    exportBtn.style.background = '#374151';
    const diagnosticsHint = el('div', 'hint', '遇到推送异常时，从这里导出可合并浏览器与服务端记录；不包含 Cookie 值或管理密码。');
    const exportInfo = el('div', 'status export-info');
    const status = el('div', 'status', '就绪：请先确认已在 aistudio.google.com 登录。');
    const hint = el('div', 'hint');
    body.appendChild(serverLabel);
    body.appendChild(passwordLabel);
    body.appendChild(pushBtn);
    body.appendChild(status);
    body.appendChild(hint);
    body.appendChild(exportBtn);
    body.appendChild(diagnosticsHint);
    body.appendChild(exportInfo);
    panel.appendChild(body);
    wrap.appendChild(panel);

    const bubble = el('button', 'bubble', '🍪');
    bubble.type = 'button';
    bubble.setAttribute('aria-label', '展开 AI Studio Cookie 抓取面板');
    wrap.appendChild(bubble);

    shadow.appendChild(style);
    shadow.appendChild(wrap);

    return {
      panel,
      head,
      headBtn,
      bubble,
      server: serverInput,
      password: passwordInput,
      push: pushBtn,
      export: exportBtn,
      diagnosticsHint,
      exportInfo,
      status,
      hint
    };
  }

  function setStatus(text, kind) {
    els.status.textContent = text;
    els.status.className = 'status' + (kind ? ' ' + kind : '');
  }

  function updateHint(collected) {
    const parts = [];
    if (!collected.gmSupported) {
      parts.push('扩展增强不可用，只推送了页面可读 Cookie。');
    } else if (collected.gmFailed) {
      parts.push('扩展增强读取失败（可能未授权），只推送了页面可读 Cookie。');
    } else if (collected.enhanced) {
      parts.push('已合并扩展读取的完整 Cookie 集合。');
    } else {
      parts.push('扩展读取未带来额外项（HttpOnly 项可能未放行）。');
    }
    const missing = CORE_COOKIE_NAMES.filter((name) => !collected.cookies.has(name));
    if (missing.length) {
      parts.push(
        '缺少关键项 ' + missing.join('、') +
        '：服务端探活导入可能失败；开启完整 Cookie 读取的方法见 docs/USERSCRIPT.md。'
      );
    }
    els.hint.textContent = parts.join(' ');
  }

  // ---------- 位置与展开状态 ----------

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function applyPosition(pos) {
    const right = clamp(Number(pos && pos.right) || 12, 4, Math.max(4, window.innerWidth - 60));
    const bottom = clamp(Number(pos && pos.bottom) || 12, 4, Math.max(4, window.innerHeight - 60));
    host.style.right = right + 'px';
    host.style.bottom = bottom + 'px';
  }

  function currentPosition() {
    const rect = host.getBoundingClientRect();
    return {
      right: Math.round(window.innerWidth - rect.right),
      bottom: Math.round(window.innerHeight - rect.bottom)
    };
  }

  function applyOpen(open) {
    if (!els) return;
    els.panel.style.display = open ? 'block' : 'none';
    els.bubble.style.display = open ? 'none' : 'flex';
    setCfg(CFG_OPEN, open);
  }

  function makeDraggable(handle) {
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let startRight = 0;
    let startBottom = 0;
    handle.addEventListener('pointerdown', (event) => {
      if (event.target.closest('.headBtn')) return;
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      dragging = true;
      const pos = currentPosition();
      startRight = pos.right;
      startBottom = pos.bottom;
      startX = event.clientX;
      startY = event.clientY;
      try {
        handle.setPointerCapture(event.pointerId);
      } catch (error) {
        /* ignore */
      }
      event.preventDefault();
    });
    handle.addEventListener('pointermove', (event) => {
      if (!dragging) return;
      applyPosition({
        right: startRight - (event.clientX - startX),
        bottom: startBottom - (event.clientY - startY)
      });
    });
    const end = () => {
      if (!dragging) return;
      dragging = false;
      setCfg(CFG_POS, currentPosition());
    };
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
  }

  // ---------- 交互 ----------

  function download(blob, filename) {
    if (downloadUrl) URL.revokeObjectURL(downloadUrl);
    downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  async function onExport() {
    if (els.export.disabled) return;
    els.export.disabled = true;
    const operation = identifier();
    const filename = 'aistudio-diagnostics-' + new Date().toISOString().replace(/:/g, '-') + '-' + operation;
    let reason = 'local_export';
    record('export_start', operation);
    els.exportInfo.textContent = '正在生成诊断包…';
    try {
      const response = await postJSON(normalizeServer(els.server.value) + '/api/diagnostics/export', els.password.value || '', { client_report: browserReport() }, operation, 'blob');
      reason = response.callback === 'timeout' ? 'timeout' : response.callback === 'abort' ? 'aborted' : response.callback !== 'load' ? 'network_error' : 'archive_rejected';
      if (response.callback === 'load' && response.status === 200 && response.blob && typeof response.blob.slice === 'function') {
        const magic = new Uint8Array(await response.blob.slice(0, 2).arrayBuffer());
        if (magic[0] === 0x1f && magic[1] === 0x8b) {
          download(response.blob, filename + '.tar.gz');
          record('export_result', operation, { outcome: 'combined' });
          els.exportInfo.textContent = '完整诊断包已生成，包含浏览器摘要与服务端记录；服务端部分可能包含对话原文，请只发给信任的人。';
          return;
        }
      }
    } catch (error) {
      reason = 'download_failed';
      record('unexpected_error', operation, { ...errorDetails(error), reason: 'unexpected_error' });
    } finally {
      els.export.disabled = false;
    }
    record('export_result', operation, { outcome: 'local_only', reason });
    try {
      const report = { schema_version: 1, kind: 'browser_only', missing: ['server_diagnostics'], client_report: browserReport() };
      download(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }), filename + '-browser-only.json');
      els.exportInfo.textContent = '仅本地诊断记录已导出，缺少服务端记录；请检查连接与管理密码，服务恢复后可再次导出完整包。';
    } catch (error) {
      record('unexpected_error', operation, { ...errorDetails(error), reason: 'download_failed' });
      els.exportInfo.textContent = '浏览器下载失败，尚未取得诊断文件；请保留页面并检查脚本管理器。';
    }
  }

  async function onPush() {
    if (els.push.disabled) return;
    const operation = identifier();
    const server = normalizeServer(els.server.value);
    const password = els.password.value || '';
    els.server.value = server;
    setCfg(CFG_SERVER, server);
    setCfg(CFG_PASSWORD, password);

    els.push.disabled = true;
    try {
      record('cookie_start', operation);
      setStatus('正在抓取 Cookie…');
      const collected = await collectCookies();
      const total = collected.cookies.size;
      record('cookie_end', operation, { cookie_count: total, gm_supported: collected.gmSupported, gm_failed: collected.gmFailed, enhanced: collected.enhanced, missing_cookie_names: CORE_COOKIE_NAMES.filter(name => !collected.cookies.has(name)) });
      if (total === 0) {
        record('result', operation, { outcome: 'no_cookies' });
        setStatus('本页没有读到任何 Cookie：请先确认已在 aistudio.google.com 登录。', 'err');
        return;
      }
      updateHint(collected);
      const email = detectEmail();
      setStatus(
        '已抓取 ' + total + ' 项' +
        (collected.enhanced ? '（含扩展增强项）' : '') +
        (email ? ' · ' + email : '') +
        '，正在推送并探活导入…'
      );

      const result = await pushCookies(server, password, toCookieString(collected.cookies), operation);
      record('result', operation, { outcome: result.kind, ...(result.reason ? { reason: result.reason } : {}), ...(result.status ? { status: result.status } : {}), ...(typeof result.count === 'number' ? { imported_count: result.count } : {}) });
      if (result.kind === 'ok') {
        const names = result.accounts
          .map((account) => account.email || account.name)
          .filter(Boolean)
          .join('、');
        setStatus(
          (result.count ? '导入成功：共 ' : '导入完成：共 ') + result.count + ' 个账号' + (names ? '（' + names + '）' : ''),
          'ok'
        );
      } else if (result.kind === 'auth') {
        setStatus('服务开启了管理鉴权：请填写正确的管理密码后重试。', 'err');
      } else if (result.kind === 'unconfirmed') {
        setStatus(
          '未能确认导入结果，可能已导入：' + (result.reason === 'timeout' ? '等待回执超时。' : result.reason === 'aborted' ? '请求被取消。' : result.reason === 'invalid_json' || result.reason === 'invalid_response' ? '收到的回执格式不正确。' : '连接或接收回执时出错。') + '请先查看账号列表或导出诊断包，避免重复导入。',
          'err'
        );
      } else {
        setStatus(
          '服务返回错误（HTTP ' + result.status + '）' +
          (result.message ? '：' + result.message : '') + '。请核对账号列表或导出诊断包确认是否部分导入。',
          'err'
        );
      }
    } catch (error) {
      record('unexpected_error', operation, { ...errorDetails(error), reason: 'unexpected_error' });
      setStatus('操作出现异常，尚未确认导入结果；请查看账号列表或导出诊断包。', 'err');
    } finally {
      els.diagnosticsHint.textContent = '本次操作编号：' + operation + (storageError ? '；本地保存存在错误，请在关闭页面前导出。' : '。诊断记录保留最近 7 天，最多 1 MiB。');
      els.push.disabled = false;
    }
  }

  // ---------- 初始化 ----------

  try {
    sessionId = identifier();
    host = document.createElement('div');
    host.id = HOST_ID;
    host.style.cssText = 'position: fixed; z-index: 2147483647;';
    document.body.appendChild(host);
    shadow = host.attachShadow({ mode: 'open' });
    els = buildPanel();

    els.push.addEventListener('click', onPush);
    els.export.addEventListener('click', onExport);
    window.addEventListener('pagehide', () => { if (downloadUrl) URL.revokeObjectURL(downloadUrl); });
    els.headBtn.addEventListener('click', () => applyOpen(false));
    els.bubble.addEventListener('click', () => applyOpen(true));
    els.server.addEventListener('change', () => {
      els.server.value = normalizeServer(els.server.value);
      setCfg(CFG_SERVER, els.server.value);
    });
    els.password.addEventListener('change', () => setCfg(CFG_PASSWORD, els.password.value));
    window.addEventListener('resize', () => applyPosition(currentPosition()));

    els.server.value = String(getCfg(CFG_SERVER, DEFAULT_SERVER));
    let savedPos = null;
    try {
      savedPos = JSON.parse(getCfg(CFG_POS, '') || 'null');
    } catch (error) {
      savedPos = null;
    }
    applyPosition(savedPos);
    makeDraggable(els.head);
    applyOpen(getCfg(CFG_OPEN, true) !== false);
    record('script_start', sessionId);
  } catch (error) {
    try {
      console.error('[aistudio-cookie] 初始化失败:', error);
    } catch (ignore) {
      /* ignore */
    }
    try {
      alert(
        '[AI Studio Cookie 抓取 v' + SCRIPT_VERSION + '] 初始化失败：' +
        (error && error.message ? error.message : String(error))
      );
    } catch (ignore) {
      /* ignore */
    }
  }

  try {
    if (typeof GM_registerMenuCommand === 'function') {
      GM_registerMenuCommand('打开 Cookie 抓取面板', () => applyOpen(true));
    }
  } catch (error) {
    /* 菜单命令不可用时忽略 */
  }
})();
