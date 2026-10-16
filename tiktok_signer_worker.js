/**
 * tiktok_signer_worker.js - Node.js Worker ký X-Bogus & X-Gnarly cho TikTok.
 *
 * Chạy độc lập qua binary Node.js (tận dụng node.exe nội bộ của patchright hoặc hệ thống).
 * Đọc cấu hình vân tay data.huynhthang để đồng bộ 100% môi trường trình duyệt.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

function createSandbox(fingerprint, userAgent) {
  const ua = userAgent || (fingerprint && fingerprint.navigator && fingerprint.navigator.userAgent) ||
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

  const screenCfg = (fingerprint && fingerprint.screen) || {
    width: 1920,
    height: 1080,
    availWidth: 1920,
    availHeight: 1040,
    colorDepth: 24,
    pixelDepth: 24
  };

  const navCfg = (fingerprint && fingerprint.navigator) || {};

  const noop = () => {};

  const sandbox = {
    window: {},
    document: {
      addEventListener: noop,
      removeEventListener: noop,
      createElement: (tag) => ({
        getContext: () => null,
        tagName: tag ? String(tag).toUpperCase() : 'DIV',
        setAttribute: noop,
        style: {}
      }),
      documentElement: { clientWidth: screenCfg.width, clientHeight: screenCfg.height },
      body: { clientWidth: screenCfg.width, clientHeight: screenCfg.height },
      cookie: '',
      readyState: 'complete'
    },
    navigator: {
      userAgent: ua,
      platform: navCfg.platform || 'Win32',
      languages: navCfg.languages || ['en-US', 'en'],
      appVersion: navCfg.appVersion || '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      hardwareConcurrency: navCfg.hardwareConcurrency || 8,
      deviceMemory: navCfg.deviceMemory || 8,
      maxTouchPoints: 0,
      webdriver: false
    },
    screen: screenCfg,
    location: {
      href: 'https://www.tiktok.com/tiktokstudio/upload?from=webapp',
      host: 'www.tiktok.com',
      hostname: 'www.tiktok.com',
      origin: 'https://www.tiktok.com',
      protocol: 'https:',
      search: '',
      pathname: '/tiktokstudio/upload'
    },
    addEventListener: noop,
    removeEventListener: noop,
    Request: globalThis.Request || class Request {},
    Response: globalThis.Response || class Response {},
    Headers: globalThis.Headers || class Headers {},
    TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder,
    crypto: globalThis.crypto,
    fetch: noop,
    atob: (str) => Buffer.from(str, 'base64').toString('binary'),
    btoa: (str) => Buffer.from(str, 'binary').toString('base64'),
    console: { log: noop, warn: noop, error: noop, info: noop },
    setTimeout: (fn, ms) => setTimeout(fn, 0),
    clearTimeout: clearTimeout,
    setInterval: (fn, ms) => setInterval(fn, 0),
    clearInterval: clearInterval,
    Date: Date,
    Math: Math,
    JSON: JSON,
    String: String,
    Array: Array,
    Object: Object,
    RegExp: RegExp,
    Function: Function
  };

  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;

  return sandbox;
}

function signUrl(targetUrl, fingerprint, userAgent, body) {
  const mssdkPath = path.join(__dirname, 'tiktok_mssdk.js');
  if (!fs.existsSync(mssdkPath)) {
    throw new Error('Không tìm thấy file tiktok_mssdk.js tại: ' + mssdkPath);
  }

  const scriptContent = fs.readFileSync(mssdkPath, 'utf-8');
  const sandbox = createSandbox(fingerprint, userAgent);
  const ctx = vm.createContext(sandbox);

  // Chạy MSSDK script trong VM
  vm.runInContext(scriptContent, ctx);

  let xBogus = '';
  let xGnarly = '';

  // 1. Lấy X-Bogus qua hook _dbg.get_yn() và X-Gnarly qua hook _dbg.get_bn()
  if (sandbox.window && sandbox.window._dbg) {
    if (typeof sandbox.window._dbg.get_yn === 'function') {
      const fnYn = sandbox.window._dbg.get_yn();
      if (typeof fnYn === 'function') {
        try {
          xBogus = (body ? fnYn(targetUrl, body) : fnYn(targetUrl)) || '';
        } catch (e) {}
      }
    }
    if (typeof sandbox.window._dbg.get_bn === 'function') {
      const fnBn = sandbox.window._dbg.get_bn();
      if (typeof fnBn === 'function') {
        try {
          xGnarly = (body ? fnBn(targetUrl, body) : fnBn(targetUrl)) || '';
        } catch (e) {}
      }
    }
  }

  // 2. Fallback cho X-Bogus nếu yn chưa lấy được
  if (!xBogus && sandbox.byted_acrawler && typeof sandbox.byted_acrawler.frontierSign === 'function') {
    const signOpts = { url: targetUrl };
    if (body) {
      signOpts.body = body;
    }
    const res = sandbox.byted_acrawler.frontierSign(signOpts);
    if (res && res['X-Bogus']) {
      xBogus = res['X-Bogus'];
    }
  }

  return {
    success: true,
    x_bogus: xBogus,
    x_gnarly: xGnarly
  };
}

function main() {
  let inputData = '';
  if (process.argv[2]) {
    inputData = process.argv[2];
    processInput(inputData);
  } else {
    // Đọc từ stdin
    let raw = '';
    process.stdin.setEncoding('utf-8');
    process.stdin.on('data', chunk => { raw += chunk; });
    process.stdin.on('end', () => {
      processInput(raw);
    });
  }
}

function processInput(raw) {
  try {
    const payload = JSON.parse(raw);
    const targetUrl = payload.url;
    const fingerprint = payload.fingerprint || null;
    const userAgent = payload.user_agent || '';
    const body = payload.body || null;

    if (!targetUrl) {
      console.log(JSON.stringify({ success: false, error: 'Missing target url' }));
      return;
    }

    const result = signUrl(targetUrl, fingerprint, userAgent, body);
    console.log(JSON.stringify(result));
  } catch (err) {
    console.log(JSON.stringify({ success: false, error: err.message }));
  }
}

if (require.main === module) {
  main();
}
