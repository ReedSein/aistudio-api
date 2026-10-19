import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const source = readFileSync(new URL('../../userscript/aistudio-cookie.user.js', import.meta.url), 'utf8')
const prefix = 'aistudio_diagnostic_event_v1_'
const secretCookie = 'fixture-cookie-do-not-log'
const secretPassword = 'fixture-password-do-not-log'
const success = { imported_count: 1, accounts: [{ id: 'fixture-account', name: 'Fixture', email: null }] }
const clone = value => JSON.parse(JSON.stringify(value))
const uuid = () => webcrypto.randomUUID().replaceAll('-', '')

function environment({ mode = 'success', store = new Map(), exportMode = 'offline', unavailableStorage = false } = {}) {
  const nodes = [], requests = [], downloads = [], blobs = new Map()
  let committed = false
  class Element {
    constructor(tag) { this.tag = tag; this.style = {}; this.events = {}; this.children = []; this.textContent = ''; nodes.push(this) }
    appendChild(child) { this.children.push(child); return child }
    setAttribute() {}
    addEventListener(name, fn) { this.events[name] = fn }
    attachShadow() { return new Element('shadow') }
    click() { if (this.tag === 'a') downloads.push({ filename: this.download, blob: blobs.get(this.href) }) }
    remove() {}
  }
  class ObjectURL extends URL {
    static createObjectURL(blob) { const value = 'blob:fixture-' + uuid(); blobs.set(value, blob); return value }
    static revokeObjectURL(value) { blobs.delete(value) }
  }
  const body = new Element('body')
  body.innerText = ''
  const context = {
    document: { cookie: mode === 'no-cookies' ? '' : 'SID=' + secretCookie, body, createElement: tag => new Element(tag), getElementById: () => null, querySelectorAll: () => [] },
    window: { innerWidth: 1280, innerHeight: 900, addEventListener() {} },
    location: { href: 'https://aistudio.google.com/fixture' },
    navigator: { userAgent: 'Fixture browser' },
    crypto: webcrypto, TextEncoder, Blob, URL: ObjectURL, performance,
    GM_info: { scriptHandler: 'Fixture manager', version: '1.0' },
    GM_getValue(key, fallback) { return store.has(key) ? clone(store.get(key)) : fallback },
    GM_setValue(key, value) { if (unavailableStorage) throw Error('storage unavailable'); store.set(key, clone(value)) },
    GM_listValues() { if (unavailableStorage) throw Error('storage unavailable'); return [...store.keys()] },
    GM_deleteValue(key) { if (unavailableStorage) throw Error('storage unavailable'); store.delete(key) },
    GM_xmlhttpRequest(options) {
      requests.push(options)
      if (mode === 'throw' && options.url.endsWith('probe-import')) throw new TypeError('fixture exception ' + secretCookie)
      queueMicrotask(() => {
        const response = { status: 200, readyState: 4, responseText: JSON.stringify(success), responseHeaders: 'X-Request-ID: ' + 'b'.repeat(32) + '\nSet-Cookie: ' + secretCookie }
        options.onreadystatechange?.({ readyState: 1, status: 0 })
        options.onprogress?.({ readyState: 3, loaded: 10, status: 200 })
        if (options.url.endsWith('/export')) {
          if (exportMode === 'combined') { response.response = new Blob([new Uint8Array([0x1f, 0x8b, 8, 0])]); options.onload(response) }
          else if (exportMode === 'invalid') { response.response = new Blob(['not an archive']); options.onload(response) }
          else options.onerror({ status: 0, responseText: secretCookie, error: secretPassword })
          return
        }
        committed = mode !== 'before-send' && mode !== 'http401'
        if (mode === 'after-commit' || mode === 'before-send') options.onerror({ status: 0, responseText: secretCookie, error: secretPassword })
        else if (mode === 'timeout') options.ontimeout({ status: 0 })
        else if (mode === 'abort') options.onabort({ status: 0 })
        else {
          if (mode === 'invalid-json') response.responseText = 'not JSON ' + secretCookie
          if (mode === 'invalid-schema') response.responseText = '{"imported_count":1,"accounts":[]}'
          if (mode === 'http401' || mode === 'http500') { response.status = mode === 'http401' ? 401 : 500; response.responseText = '{"detail":"fixture server error"}' }
          options.onload(response)
          if (mode === 'duplicate') options.onerror({ status: 0 })
        }
      })
    },
    console, alert(message) { throw Error(message) }
  }
  vm.runInNewContext(source, context)
  const push = nodes.find(node => node.className === 'big push')
  const exportButton = nodes.find(node => node.className === 'headBtn export')
  const password = nodes.find(node => node.className === 'password')
  password.value = secretPassword
  return {
    store, requests, downloads, nodes,
    push: () => push.events.click(), export: () => exportButton.events.click(),
    get committed() { return committed },
    get status() { return nodes.find(node => String(node.className).startsWith('status') && !node.className.includes('export-info')).textContent },
    get events() { return [...store].filter(([key]) => key.startsWith(prefix)).map(([, value]) => value) }
  }
}

const modes = ['success', 'after-commit', 'before-send', 'timeout', 'abort', 'invalid-json', 'invalid-schema', 'http401', 'http500', 'throw', 'duplicate']
for (const mode of modes) {
  const page = environment({ mode })
  await page.push()
  assert.equal(page.requests.length, 1, 'diagnostics must not upload automatically or retry imports')
  if (mode === 'success' || mode === 'duplicate') assert.match(page.status, /导入成功/)
  else if (mode === 'http401') assert.match(page.status, /管理鉴权/)
  else if (mode === 'http500') assert.match(page.status, /服务返回错误/)
  else assert.match(page.status, /未能确认导入结果/)
  assert.doesNotMatch(page.status, /无法连接服务/)
  if (mode === 'after-commit') assert.equal(page.committed, true)
  const saved = JSON.stringify(page.events)
  assert.ok(!saved.includes(secretCookie) && !saved.includes(secretPassword))
  assert.ok(page.events.some(event => event.stage === 'result'))
  if (mode === 'success') assert.ok(page.events.some(event => event.stage === 'request_settled' && event.response_bytes > 0))
  const operation = page.requests[0].headers['X-Aistudio-Operation-ID']
  assert.match(operation, /^[a-f0-9]{32}$/)
  assert.ok(page.events.filter(event => event.stage !== 'script_start').every(event => event.operation_id === operation))
  if (mode === 'duplicate') assert.ok(page.events.some(event => event.duplicate_callback === true))
}

const noCookies = environment({ mode: 'no-cookies' })
await noCookies.push()
assert.equal(noCookies.requests.length, 0)
assert.match(noCookies.status, /没有读到任何 Cookie/)
assert.ok(noCookies.events.some(event => event.stage === 'result' && event.outcome === 'no_cookies'))

// Separate browser sessions append separate keys; one cannot overwrite the other's event buffer.
const shared = new Map()
const left = environment({ mode: 'after-commit', store: shared })
const right = environment({ mode: 'success', store: shared })
await Promise.all([left.push(), right.push()])
const operations = [left.requests[0], right.requests[0]].map(request => request.headers['X-Aistudio-Operation-ID'])
assert.notEqual(...operations)
const reloaded = environment({ store: shared, exportMode: 'combined' })
await reloaded.export()
const sampleReport = JSON.parse(reloaded.requests[0].data).client_report
assert.ok(operations.every(operation => sampleReport.events.some(event => event.operation_id === operation && event.stage === 'result')))
assert.match(reloaded.downloads[0].filename, /\.tar\.gz$/)
assert.ok(!JSON.stringify(sampleReport).includes(secretCookie) && !JSON.stringify(sampleReport).includes(secretPassword))

for (const exportMode of ['offline', 'invalid']) {
  const page = environment({ mode: 'after-commit', exportMode })
  await page.push()
  await page.export()
  assert.match(page.downloads[0].filename, /browser-only\.json$/)
  const local = JSON.parse(await page.downloads[0].blob.text())
  assert.equal(local.kind, 'browser_only')
  assert.deepEqual(local.missing, ['server_diagnostics'])
  assert.ok(local.client_report.events.some(event => event.stage === 'result' && event.outcome === 'unconfirmed'))
}

const template = clone(sampleReport.events[0])
const large = new Map([['unrelated-user-data', { cookies: secretCookie }]])
for (let index = 0; index < 150; index++) {
  const event = { ...template, id: uuid(), sequence: index, error_stack: 'at [script]:123:45\n'.repeat(400).trimEnd(), timestamp: new Date(Date.now() - (index === 0 ? 8 : 1) * 86400000).toISOString() }
  large.set(prefix + event.id, event)
}
const invalidId = uuid()
large.set(prefix + invalidId, { ...template, id: invalidId, cookies: secretCookie })
const invalidType = uuid()
large.set(prefix + invalidType, { ...template, id: invalidType, cookie_count: { value: secretCookie } })
const bounded = environment({ store: large })
await bounded.export()
const local = JSON.parse(await bounded.downloads[0].blob.text())
assert.ok(local.client_report.pruned_events > 0)
assert.equal(local.client_report.storage_error, true)
assert.ok(!large.has(prefix + invalidId))
assert.ok(!large.has(prefix + invalidType))
assert.ok(!JSON.stringify(local).includes(secretCookie))
assert.ok(large.has('unrelated-user-data'))
assert.ok([...large].filter(([key]) => key.startsWith(prefix)).reduce((sum, [key, value]) => sum + key.length + 4 + new TextEncoder().encode(JSON.stringify(value)).length, 0) < 1024 * 1024)

const unavailable = environment({ unavailableStorage: true, mode: 'after-commit' })
await unavailable.push()
await unavailable.export()
const emergency = JSON.parse(await unavailable.downloads[0].blob.text())
assert.equal(emergency.client_report.storage_error, true)
assert.ok(emergency.client_report.events.some(event => event.stage === 'result'))

console.log(JSON.stringify({ scenarios: modes.length + 6, sample_report: sampleReport }))
