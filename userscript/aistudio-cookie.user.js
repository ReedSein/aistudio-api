// ==UserScript==
// @name         AI Studio Cookie 抓取推送 (aistudio-api)
// @namespace    https://github.com/ReedSein/aistudio-api-termux
// @version      0.1.2
// @description  在 aistudio.google.com 抓取登录 Cookie，推送到自家 aistudio-api 服务并探活导入全部已登录账号
// @author       ReedSein
// @match        https://aistudio.google.com/*
// @grant        GM_getValue
// @grant        GM_setValue
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

  function postJSON(url, password, payload) {
    return new Promise((resolve) => {
      const headers = { 'Content-Type': 'application/json' };
      if (password) headers.Authorization = 'Bearer ' + password;
      GM_xmlhttpRequest({
        method: 'POST',
        url,
        headers,
        data: JSON.stringify(payload),
        timeout: PUSH_TIMEOUT_MS,
        onload: (response) => {
          resolve({ status: response.status, text: response.responseText || '' });
        },
        onerror: () => resolve({ status: 0, text: '' }),
        ontimeout: () => resolve({ status: -1, text: '' })
      });
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

  async function pushCookies(server, password, cookieString) {
    const response = await postJSON(
      server + '/api/accounts/probe-import',
      password,
      { cookies: cookieString }
    );
    if (response.status === 200) {
      let data = null;
      try {
        data = JSON.parse(response.text);
      } catch (error) {
        /* ignore */
      }
      const accounts = (data && Array.isArray(data.accounts)) ? data.accounts : [];
      const count = data && typeof data.imported_count === 'number'
        ? data.imported_count
        : accounts.length;
      return { kind: 'ok', count, accounts };
    }
    if (response.status === 401) return { kind: 'auth' };
    if (response.status === 0) return { kind: 'net' };
    if (response.status === -1) return { kind: 'timeout' };
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
    const status = el('div', 'status', '就绪：请先确认已在 aistudio.google.com 登录。');
    const hint = el('div', 'hint');
    body.appendChild(serverLabel);
    body.appendChild(passwordLabel);
    body.appendChild(pushBtn);
    body.appendChild(status);
    body.appendChild(hint);
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

  async function onPush() {
    const server = normalizeServer(els.server.value);
    const password = els.password.value || '';
    els.server.value = server;
    setCfg(CFG_SERVER, server);
    setCfg(CFG_PASSWORD, password);

    els.push.disabled = true;
    try {
      setStatus('正在抓取 Cookie…');
      const collected = await collectCookies();
      const total = collected.cookies.size;
      if (total === 0) {
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

      const result = await pushCookies(server, password, toCookieString(collected.cookies));
      if (result.kind === 'ok') {
        const names = result.accounts
          .map((account) => account.email || account.name)
          .filter(Boolean)
          .join('、');
        setStatus(
          '导入成功：共 ' + result.count + ' 个账号' + (names ? '（' + names + '）' : ''),
          'ok'
        );
      } else if (result.kind === 'auth') {
        setStatus('服务开启了管理鉴权：请填写正确的管理密码后重试。', 'err');
      } else if (result.kind === 'net') {
        setStatus(
          '无法连接服务：请检查地址是否正确、服务是否已启动。手机本机访问用 127.0.0.1，' +
          '电脑访问手机上的服务请填手机的局域网 IP。',
          'err'
        );
      } else if (result.kind === 'timeout') {
        setStatus('推送超时：探活耗时过长，请在服务端日志确认结果后重试。', 'err');
      } else {
        setStatus(
          '推送未成功（HTTP ' + result.status + '）' +
          (result.message ? '：' + result.message : ''),
          'err'
        );
      }
    } catch (error) {
      setStatus('推送失败：' + (error && error.message ? error.message : String(error)), 'err');
    } finally {
      els.push.disabled = false;
    }
  }

  // ---------- 初始化 ----------

  try {
    host = document.createElement('div');
    host.id = HOST_ID;
    host.style.cssText = 'position: fixed; z-index: 2147483647;';
    document.body.appendChild(host);
    shadow = host.attachShadow({ mode: 'open' });
    els = buildPanel();

    els.push.addEventListener('click', onPush);
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
  } catch (error) {
    try {
      console.error('[aistudio-cookie] 初始化失败:', error);
    } catch (ignore) {
      /* ignore */
    }
    try {
      alert(
        '[AI Studio Cookie 抓取 v0.1.2] 初始化失败：' +
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
