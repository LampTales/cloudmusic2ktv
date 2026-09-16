// Run with node tests/test_qr_frontend.js, or supply QR_SOURCE in a JS runtime.
(async () => {
  const source = typeof QR_SOURCE === "string" ? QR_SOURCE : require("node:fs").readFileSync("frontend/static/app.js", "utf8");
  new Function(source); // Parse the entire application, including code outside this harness.
  const helpers = source.slice(source.indexOf("function loadQrSdk("), source.indexOf("async function api("));
  const flow = source.slice(source.indexOf("function stopQrPolling("), source.indexOf("function openNeteaseReauth("));
  function assert(value, message) { if (!value) throw new Error(message); }
  function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; }
  function harness() {
    let interval;
    const elements = [];
    const window = {};
    const timers = new Map();
    let nextId = 0;
    const setTimeout = (fn) => { timers.set(++nextId, fn); return nextId; };
    const clearTimeout = id => timers.delete(id);
    const document = {
      createElement: () => ({remove() { this.removed = true; }}),
      head: {appendChild(el) { elements.push(el); }},
      body: {appendChild(el) { elements.push(el); }},
    };
    const apiCalls = [], notices = [], replies = [];
    const api = async (url, options) => {
      apiCalls.push({url, body: JSON.parse(options.body)});
      if (url.endsWith("/start")) return {qr_url: "qr-url"};
      const response = replies.shift();
      if (response instanceof Error) throw response;
      return await response;
    };
    const build = new Function("window", "document", "navigator", "setTimeout", "clearTimeout", "setInterval", "clearInterval", "api", "notify", "busy", `
      const qrSdkLoads = new Map();
      let cancelQrVerification = null, qrPollTimer = null, qrExpireTimer = null, qrFlowId = 0;
      ${helpers}\n${flow}
      return {getYdDeviceToken, loadQrSdk, verifyQrCaptcha, stopQrPolling, startQrFlow};
    `);
    const functions = build(window, document, {userAgent: "browser"}, setTimeout, clearTimeout,
      fn => { interval = fn; return 1; }, () => { interval = null; }, api,
      msg => notices.push(msg), () => {});
    return {functions, window, elements, apiCalls, notices, replies, tick: () => interval(), timers,
      get interval() { return interval; }};
  }
  const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
  const ui = () => ({image: {}, status: {}, link: {}, button: {}, onVerified() {}});

  {
    const h = harness(); let counter = 0;
    h.window.createNEFingerprint = () => ({getToken: async () => ({token: `token-${++counter}`})});
    assert(await h.functions.getYdDeviceToken() === "token-1", "first fingerprint");
    assert(await h.functions.getYdDeviceToken() === "token-2", "fingerprints must not be cached across polls");
    assert(h.timers.size === 0, "fingerprint timeout cleanup");
    delete h.window.createNEFingerprint;
    const failed = h.functions.getYdDeviceToken();
    h.elements.at(-1).onerror();
    assert(await failed === "", "script failure degrades to empty token");
    const retry = h.functions.getYdDeviceToken();
    h.window.createNEFingerprint = () => ({getToken: async () => ({token: "recovered"})});
    h.elements.at(-1).onload();
    assert(await retry === "recovered", "SDK failure must allow retry");
  }
  {
    const h = harness(); let options, destroyed = 0, verified = 0, counter = 0;
    h.window.createNEFingerprint = () => ({getToken: async () => ({token: `t${++counter}`})});
    h.window.initNECaptcha = (config, success) => {
      options = config; success({popUp() {}, destroy() { destroyed++; }});
    };
    h.replies.push({status: "verification_required"}, {status: "verified", profile: {nickname: "test"}});
    const panel = ui(); panel.onVerified = () => verified++;
    await h.functions.startQrFlow(panel);
    const polling = h.tick(); await flush();
    assert(options, "8821 must open official verification");
    assert(!("secure_captcha" in h.apiCalls[1].body), "initial poll must omit secureCaptcha");
    const calls = h.apiCalls.length; await h.tick();
    assert(h.apiCalls.length === calls, "no polling while awaiting user verification");
    options.onVerify(null, {validate: "real-proof"});
    await polling;
    await h.tick();
    assert(h.apiCalls[2].body.secure_captcha === "real-proof", "verified proof forwarded");
    assert(h.apiCalls[1].body.yd_device_token !== h.apiCalls[2].body.yd_device_token, "fresh token per poll");
    assert(destroyed === 1 && verified === 1 && !h.interval, "success cleans up timers and verification");
  }
  {
    const h = harness(); let options, destroyed = 0;
    h.window.createNEFingerprint = () => ({getToken: async () => ({token: "token"})});
    h.window.initNECaptcha = (config, success) => {
      options = config; success({popUp() {}, destroy() { destroyed++; }});
    };
    h.replies.push({status: "verification_required"});
    await h.functions.startQrFlow(ui());
    const polling = h.tick(); await flush();
    h.functions.stopQrPolling(); await polling;
    assert(destroyed === 1 && !h.interval, "cancel cleans up verification");
    assert(!h.notices.length, "cancelled flow must not show stale errors");
    options.onVerify(null, {validate: "late-proof"});
    assert(h.apiCalls.length === 2, "late proof must not resume cancelled flow");
  }
  {
    const h = harness();
    h.window.createNEFingerprint = () => ({getToken: async () => ({token: "token"})});
    const oldResponse = deferred(); h.replies.push(oldResponse.promise);
    await h.functions.startQrFlow(ui()); const oldPoll = h.tick(); await flush();
    const panel = ui(); await h.functions.startQrFlow(panel);
    oldResponse.reject(new Error("old error")); await oldPoll;
    assert(h.interval && !h.notices.length, "old errors must not stop new QR flow");
    h.functions.stopQrPolling();
  }
  {
    const submit = source.slice(source.indexOf("async function submitWebsiteRegistration("), source.indexOf('$("#websiteRegisterForm").addEventListener'));
    for (const result of [{status: "pending", message: "申请已提交，请等待管理员审批"}, {status: "pending", message: "申请仍在等待管理员审批"}, {message: "网站账号创建成功"}]) {
      const inputs = new Map(); const notices = []; const requests = [];
      const get = key => { if (!inputs.has(key)) inputs.set(key, {value: "test"}); return inputs.get(key); };
      const run = new Function("$", "api", "notify", "busy", "clearCookieInput", "refreshStatus", "closeAccountModal", `
        let registerQrVerified = true, registerUsingLegacy = true, verifiedIdentityConfirmationPurpose = null;
        ${submit}
        return submitWebsiteRegistration().then(() => registerQrVerified);
      `);
      const stillVerified = await run(get, async (url, options) => { requests.push(JSON.parse(options.body)); return result; },
        msg => notices.push(msg), () => {}, () => {}, async () => {}, () => {});
      assert(requests[0].qr === true, "registration must consume verified QR identity");
      assert(notices[0] === result.message, "pending approval must not be announced as logged in");
      assert(stillVerified === false, "submitted QR must not be reused");
    }
  }
  return "5 frontend behavior scenarios and full-script syntax passed";
})()
