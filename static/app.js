const $ = (selector) => document.querySelector(selector);
let selectedSong = null;
let toastTimer = null;
let previewTimer = null;
let previewRequest = 0;
let accountLoggedIn = false;
let accountRole = null;
let neteaseBound = false;
let selectedSongLocal = {status: "missing", ready: false, message: "尚未选择歌曲"};
let selectedSongVideos = [];
let videoStatusRequest = 0;
let lastQueueArtifactUrl = null;
let queueTimer = null;
let latestQueue = {current: null, queued: [], queued_count: 0, recent: null};
let registerQrVerified = false;
// The normal registration path is the SMS flow; Cookie import is an explicit
// alternate method kept behind the underlined link in the form.
let registerUsingLegacy = true;
let cookieCsrfToken = "";
let qrPollTimer = null;
let qrExpireTimer = null;
let qrFlowId = 0;
let ydDeviceTokenPromise = null;

function getYdDeviceToken() {
  if (ydDeviceTokenPromise) return ydDeviceTokenPromise;
  ydDeviceTokenPromise = new Promise(resolve => {
    const finish = async () => {
      try {
        if (typeof window.createNEFingerprint !== "function") return resolve("");
        const result = await window.createNEFingerprint({appId: "9d0ef7e0905d422cba1ecf7e73d77e67", timeout: 6000}).getToken();
        resolve(String(result?.token || ""));
      } catch { resolve(""); }
    };
    if (typeof window.createNEFingerprint === "function") finish();
    else {
      const script = document.createElement("script");
      script.src = "https://st.music.163.com/device/signature/create/deviceid.js";
      script.onload = finish;
      script.onerror = () => resolve("");
      document.head.appendChild(script);
      setTimeout(() => resolve(""), 7000);
    }
  });
  return ydDeviceTokenPromise;
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    credentials: options.credentials || "same-origin",
    ...options,
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`服务返回异常（HTTP ${response.status}）`); }
  if (!response.ok || !data.ok) {
    const error = new Error(data?.error?.message || `请求失败（HTTP ${response.status}）`);
    error.code = data?.error?.code;
    error.status = response.status;
    throw error;
  }
  return data;
}

function notify(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `show${error ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.className = "", 4200);
}

function busy(button, value, text = "处理中…") {
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = value;
  button.textContent = value ? text : button.dataset.label;
}

async function ensureCookieCsrf() {
  const data = await api("/api/auth/csrf");
  cookieCsrfToken = String(data.csrf_token || "");
  if (!cookieCsrfToken) throw new Error("无法准备 Cookie 导入验证，请刷新页面重试");
  return cookieCsrfToken;
}

async function readCookieInput(fileSelector, textSelector) {
  const fileInput = $(fileSelector);
  const pasted = $(textSelector).value.trim();
  if (fileInput.files.length && pasted) throw new Error("请只选择上传文件或粘贴内容其中一种方式");
  let raw = pasted;
  if (fileInput.files.length) raw = await fileInput.files[0].text();
  if (!raw.trim()) throw new Error("请上传或粘贴 Cookie JSON");
  let parsed;
  try { parsed = JSON.parse(raw); }
  catch { throw new Error("Cookie JSON 格式不正确"); }
  if (!Array.isArray(parsed) && !(parsed && Array.isArray(parsed.cookies))) {
    throw new Error("Cookie JSON 必须是浏览器导出的数组格式");
  }
  return parsed;
}

function clearCookieInput(fileSelector, textSelector) {
  $(fileSelector).value = "";
  $(textSelector).value = "";
}

async function refreshStatus() {
  try {
    const data = await api("/api/status");
    accountLoggedIn = data.logged_in;
    accountRole = data.role || null;
    neteaseBound = Boolean(data.netease_bound);
    const pill = $("#accountPill");
    pill.classList.toggle("online", data.logged_in);
    pill.lastElementChild.textContent = data.logged_in ? `已登录 · ${data.profile.username || data.profile.nickname}` : "尚未登录";
    $("#loggedOut").classList.toggle("hidden", data.logged_in);
    $("#loggedIn").classList.toggle("hidden", !data.logged_in);
    if (!data.logged_in) $("#neteaseReauthForm").classList.add("hidden");
    $("#manageAllowlist").classList.toggle("hidden", !data.logged_in || accountRole !== "admin");
    if (data.logged_in) {
      $("#nickname").textContent = `${data.profile.username || data.profile.nickname} · ${data.profile.nickname || ""}`;
      $("#avatar").src = data.profile.avatarUrl || "";
      $("#neteaseBindingStatus").textContent = neteaseBound ? "网站账号已登录 · 已绑定网易云账号" : "网站账号已登录 · 尚未绑定网易云";
      refreshQueue();
    } else {
      stopQueuePolling();
      clearQueueView();
    }
    updateRenderAvailability();
  } catch (error) { notify(error.message, true); }
}

async function inspectSong(value, shouldScroll = true) {
  const button = $("#inspect");
  busy(button, true, "读取中…");
  try {
    const data = await api("/api/song/inspect", {method: "POST", body: JSON.stringify({song: value})});
    showSong(data.song, data.local, shouldScroll);
  } catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
}

function showSong(song, local = null, shouldScroll = true) {
  selectedSong = {...song};
  localStorage.setItem("cloudmusic2ktv.selectedSongId", String(song.id));
  $("#songInput").value = song.id;
  $("#cover").src = song.cover_url;
  $("#songName").textContent = song.name;
  $("#songMeta").textContent = `${song.artist} · ${song.album} · ID ${song.id}`;
  $("#songPreview").classList.remove("hidden");
  $("#castPanel").classList.remove("hidden");
  $("#searchResults").classList.add("hidden");
  updateVideoSongTarget();
  updateCastSongTarget();
  refreshSelectedVideoStatus();
  if (local) applyLocalStatus(local);
  else {
    applyLocalStatus({status: "checking", ready: false, message: "正在检查共享素材"});
    refreshSelectedLocalStatus();
  }
  if (shouldScroll) $("#songPreview").scrollIntoView({behavior: "smooth", block: "center"});
}

function updateCastSongTarget() {
  if (!selectedSong) return;
  const summary = $("#videoSummaryStatus");
  summary.dataset.status = "checking";
  $("#videoSummaryTitle").textContent = "正在检查";
  $("#videoSummaryMessage").textContent = "正在扫描本地成品";
  $("#castMissingText").textContent = "正在扫描这首歌的本地视频";
  $("#castMissing").classList.remove("hidden");
  $("#castControls").classList.add("hidden");
  selectedSongVideos = [];
  updateCastDirectLink();
}

async function refreshSelectedVideoStatus() {
  if (!selectedSong) return;
  const songId = selectedSong.id;
  const requestNumber = ++videoStatusRequest;
  try {
    const data = await api(`/api/video/local/${encodeURIComponent(songId)}`, {headers: {}});
    if (requestNumber === videoStatusRequest && selectedSong?.id === songId) applyCastStatus(data.local);
  } catch (error) {
    if (requestNumber === videoStatusRequest && selectedSong?.id === songId) {
      applyCastStatus({status: "error", ready: false, message: error.message, videos: []});
    }
  }
}

function applyCastStatus(local) {
  selectedSongVideos = Array.isArray(local?.videos) ? local.videos : [];
  const ready = Boolean(local?.ready && selectedSongVideos.length);
  const summary = $("#videoSummaryStatus");
  summary.dataset.status = ready ? "ready" : local?.status === "error" ? "error" : "missing";
  $("#videoSummaryTitle").textContent = ready ? `已有 ${selectedSongVideos.length} 个` : "暂无视频";
  $("#videoSummaryMessage").textContent = ready
    ? "可以直接播放、投屏或下载"
    : local?.message || "制作完成后会显示在这里";
  $("#castMissingText").textContent = local?.message || "完成素材准备和画面设置后即可生成";
  $("#castMissing").classList.toggle("hidden", ready);
  $("#castControls").classList.toggle("hidden", !ready);

  const select = $("#castVideoSelect");
  select.replaceChildren();
  for (const video of selectedSongVideos) {
    const option = document.createElement("option");
    option.value = video.filename;
    option.textContent = `${video.resolution} · ${formatFileSize(video.size)} · ${formatUpdatedAt(video.updated_at)}`;
    select.append(option);
  }
  updateCastDirectLink();
}

function selectedCastVideo() {
  const filename = $("#castVideoSelect").value;
  return selectedSongVideos.find(video => video.filename === filename) || selectedSongVideos[0] || null;
}

function updateCastDirectLink() {
  const video = selectedCastVideo();
  const link = $("#castDirectLink");
  const media = $("#castMediaElement");
  const browserButton = $("#browserCast");
  if (!video) {
    link.removeAttribute("href");
    media.removeAttribute("src");
    media.load();
    browserButton.disabled = true;
    updateBrowserCastHelp();
    return;
  }
  const url = new URL(video.url, window.location.href).href;
  link.href = url;
  if (media.src !== url) {
    media.src = url;
    media.load();
  }
  browserButton.disabled = false;
  updateBrowserCastHelp();
}

function browserCastMethod() {
  const media = $("#castMediaElement");
  if (media.remote && typeof media.remote.prompt === "function") return "remote-playback";
  if (typeof media.webkitShowPlaybackTargetPicker === "function") return "webkit-picker";
  return null;
}

function updateBrowserCastHelp(message = "") {
  const help = $("#browserCastHelp");
  if (message) {
    help.textContent = message;
    return;
  }
  if (!selectedCastVideo()) {
    help.textContent = "生成完整视频后，才可以尝试浏览器原生投屏。";
    return;
  }
  const method = browserCastMethod();
  if (method === "remote-playback") {
    help.textContent = "当前浏览器提供 Remote Playback API；点击实验按钮后，请在浏览器设备列表中检查是否出现纯 K 或其他 DLNA 设备。";
  } else if (method === "webkit-picker") {
    help.textContent = "当前浏览器提供 Apple 原生播放目标选择器；它通常只能发现 AirPlay 设备。";
  } else if (!window.isSecureContext) {
    help.textContent = "当前局域网 HTTP 页面没有获得浏览器远程播放能力；未来通过公网 HTTPS 访问时请再次测试。";
  } else {
    help.textContent = "当前浏览器没有提供网页可调用的远程播放接口，可以换用最新版 Chrome、Edge 或 Safari 现场测试。";
  }
}

async function tryBrowserCast() {
  const selectedVideo = selectedCastVideo();
  if (!selectedSong || !selectedVideo) return notify("这首歌还没有可投屏的本地视频", true);
  const media = $("#castMediaElement");
  const url = new URL(selectedVideo.url, window.location.href).href;
  if (media.src !== url) {
    media.src = url;
    media.load();
  }

  const method = browserCastMethod();
  if (!method) {
    const reason = !window.isSecureContext
      ? "当前局域网 HTTP 页面没有浏览器投屏权限；请在 HTTPS 环境中再次测试"
      : "当前浏览器不支持网页端远程播放设备选择器";
    updateBrowserCastHelp(reason);
    return notify(reason, true);
  }

  try {
    if (method === "remote-playback") {
      await media.remote.prompt();
      const states = {connected: "已连接远程设备", connecting: "正在连接远程设备", disconnected: "未连接远程设备"};
      notify(states[media.remote.state] || "浏览器设备选择器已关闭");
    } else {
      media.webkitShowPlaybackTargetPicker();
      notify("已打开浏览器原生播放目标选择器");
    }
  } catch (error) {
    if (error?.name === "AbortError") return;
    const messages = {
      NotAllowedError: "浏览器拒绝打开设备列表；请确认页面使用 HTTPS，并直接点击实验按钮重试",
      NotFoundError: "浏览器没有发现可用的远程播放设备",
      NotSupportedError: "浏览器不支持将这个 MP4 发送到远程设备",
      InvalidStateError: "视频尚未准备好，无法请求远程播放",
    };
    const message = messages[error?.name] || error?.message || "浏览器投屏请求失败";
    updateBrowserCastHelp(message);
    notify(message, true);
  }
}

function formatFileSize(bytes) {
  const size = Number(bytes || 0);
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatUpdatedAt(seconds) {
  if (!seconds) return "时间未知";
  return new Date(Number(seconds) * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

async function copyText(value) {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return true;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  return copied;
}

async function openCastApp() {
  const video = selectedCastVideo();
  if (!selectedSong || !video) return notify("这首歌还没有可投屏的本地视频", true);
  const url = new URL(video.url, window.location.href).href;
  const shareData = {
    title: `${selectedSong.name} — ${selectedSong.artist}`,
    text: "CloudMusic2KTV 视频",
    url,
  };
  if (typeof navigator.share === "function") {
    try {
      if (typeof navigator.canShare !== "function" || navigator.canShare(shareData)) {
        await navigator.share(shareData);
        return;
      }
    } catch (error) {
      if (error?.name === "AbortError") return;
    }
  }
  try {
    if (await copyText(url)) {
      notify("当前页面无法打开系统分享，播放地址已复制；请在 BubbleUPnP 中打开该网络地址");
      return;
    }
  } catch {}
  notify("无法调用系统分享，请长按“先测试播放”复制视频地址", true);
}

function downloadCastVideo() {
  const video = selectedCastVideo();
  if (!selectedSong || !video) return notify("这首歌还没有可下载的本地视频", true);
  const url = new URL(video.url, window.location.href);
  url.searchParams.set("download", "1");
  const link = document.createElement("a");
  link.href = url.href;
  link.download = video.download_name || video.filename;
  document.body.append(link);
  link.click();
  link.remove();
  notify(`已开始下载 ${video.resolution} 视频`);
}

function updateVideoSongTarget(inputPending = false) {
  if (!selectedSong) return;
  $("#selectionPending").classList.toggle("hidden", !inputPending);
}

async function refreshSelectedLocalStatus() {
  if (!selectedSong) return;
  const songId = selectedSong.id;
  try {
    const data = await api(`/api/song/local/${encodeURIComponent(songId)}`, {headers: {}});
    if (selectedSong?.id === songId) applyLocalStatus(data.local);
  } catch (error) {
    if (selectedSong?.id === songId) {
      applyLocalStatus({status: "error", ready: false, message: error.message});
    }
  }
}

function applyLocalStatus(local) {
  selectedSongLocal = local || {status: "missing", ready: false, message: "本地没有素材"};
  const box = $("#materialStatus");
  box.dataset.status = selectedSongLocal.status;
  $("#builderMaterial").dataset.status = selectedSongLocal.status;
  const titles = {
    checking: "正在检查共享素材",
    missing: "尚未下载",
    partial: "素材不完整",
    downloading: "正在下载",
    ready: "共享素材可用",
    error: "无法检查素材",
  };
  $("#materialStatusTitle").textContent = selectedSongLocal.title || titles[selectedSongLocal.status] || "共享素材状态";
  $("#materialStatusMessage").textContent = selectedSongLocal.message || "";
  $("#builderMaterialTitle").textContent = selectedSongLocal.title || titles[selectedSongLocal.status] || "歌曲素材";
  $("#builderMaterialMessage").textContent = selectedSongLocal.message || "准备音频、封面和歌词时间轴";
  const download = $("#download");
  download.textContent = selectedSongLocal.ready ? "重新下载素材" : "下载歌曲素材";
  download.classList.toggle("primary", !selectedSongLocal.ready);
  download.classList.toggle("secondary", selectedSongLocal.ready);
  download.disabled = selectedSongLocal.status === "downloading" || selectedSongLocal.status === "checking";
  $("#refreshPreview").disabled = !selectedSongLocal.ready;
  if (selectedSongLocal.ready) {
    schedulePreview(true);
  } else {
    previewRequest += 1;
    $("#videoPreviewImage").style.display = "none";
    $("#previewPlaceholder").classList.remove("hidden");
    $("#previewPlaceholder").textContent = selectedSongLocal.status === "downloading"
      ? "共享素材正在下载，完成后即可预览"
      : "请先准备这首歌的共享素材";
    $("#previewMeta").textContent = "预览尚未生成";
  }
  updateRenderAvailability();
}

function updateRenderAvailability() {
  const button = $("#renderVideo");
  const badge = $("#builderStateBadge");
  const available = Boolean(selectedSong && selectedSongLocal.ready && accountLoggedIn);
  button.disabled = !available;
  badge.classList.toggle("ready", Boolean(selectedSong && selectedSongLocal.ready));
  if (!selectedSong) {
    $("#renderAvailability").textContent = "请先选择歌曲";
    badge.textContent = "等待选择歌曲";
  } else if (!selectedSongLocal.ready) {
    $("#renderAvailability").textContent = "请先下载完整的歌曲素材";
    badge.textContent = "素材未准备";
  } else if (!accountLoggedIn) {
    $("#renderAvailability").textContent = "登录后可以提交视频任务";
    badge.textContent = "素材已就绪";
  } else {
    $("#renderAvailability").textContent = "已可加入共享生成队列";
    badge.textContent = "可以制作";
  }
}

function setFinderMode(mode) {
  const searchMode = mode === "search";
  $("#searchModeSearch").classList.toggle("active", searchMode);
  $("#searchModeId").classList.toggle("active", !searchMode);
  $("#searchModeSearch").setAttribute("aria-selected", String(searchMode));
  $("#searchModeId").setAttribute("aria-selected", String(!searchMode));
  $("#finderSearchPanel").classList.toggle("hidden", !searchMode);
  $("#finderIdPanel").classList.toggle("hidden", searchMode);
  const target = searchMode ? $("#searchInput") : $("#songInput");
  requestAnimationFrame(() => target.focus());
}

function openAccountModal() {
  closeNeteaseReauth();
  $("#loggedOut").classList.toggle("hidden", accountLoggedIn);
  $("#loggedIn").classList.toggle("hidden", !accountLoggedIn);
  $("#accountModal").classList.remove("hidden");
  document.body.classList.add("modal-open");
  $(accountRole === "admin" ? "#manageAllowlist" : accountLoggedIn ? "#logout" : "#websiteUsername").focus();
}

function closeAccountModal() {
  stopQrPolling();
  closeNeteaseReauth();
  clearCookieInput("#registerCookieFile", "#registerCookieText");
  clearCookieInput("#reauthCookieFile", "#reauthCookieText");
  $("#accountModal").classList.add("hidden");
  document.body.classList.remove("modal-open");
  $("#accountPill").focus();
}

function setWebsiteAuthMode(mode) {
  const register = mode === "register";
  stopQrPolling();
  registerUsingLegacy = register;
  registerQrVerified = false;
  $("#smsRegisterFields").classList.remove("hidden");
  $("#startQrRegister").classList.remove("hidden");
  $("#qrRegisterPanel").classList.add("hidden");
  $("#websiteLoginMode").classList.toggle("active", !register);
  $("#websiteRegisterMode").classList.toggle("active", register);
  $("#websiteLoginForm").classList.toggle("hidden", register);
  $("#websiteRegisterForm").classList.toggle("hidden", !register);
  $("#cookieRegisterFields").classList.toggle("hidden", !register || registerUsingLegacy);
  $("#legacyRegisterMethods").classList.toggle("hidden", !register || !registerUsingLegacy);
  $("#showLegacyRegister").textContent = registerUsingLegacy ? "使用 Cookie 导入" : "使用验证码/二维码";
  $("#register").textContent = registerUsingLegacy ? "验证并创建用户" : "导入 Cookie 并创建用户";
  if (register) $("#registerUsername").focus();
  else $("#websiteUsername").focus();
}

function stopQrPolling() {
  qrFlowId += 1;
  if (qrPollTimer) { clearInterval(qrPollTimer); qrPollTimer = null; }
  if (qrExpireTimer) { clearTimeout(qrExpireTimer); qrExpireTimer = null; }
}

async function startQrFlow({image, status, link, button, onVerified}) {
  stopQrPolling();
  const flowId = qrFlowId;
  busy(button, true, "获取中…");
  try {
    const data = await api("/api/auth/qr/start", {
      method: "POST",
      body: JSON.stringify({browser_user_agent: navigator.userAgent}),
    });
    image.src = `https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=${encodeURIComponent(data.qr_url)}`;
    link.href = data.qr_url;
    status.textContent = "请使用网易云音乐 App 扫描二维码。";
    const ydDeviceToken = await getYdDeviceToken();
    if (flowId !== qrFlowId) return;
    let pollInFlight = false;
    qrExpireTimer = setTimeout(() => {
      if (flowId !== qrFlowId) return;
      stopQrPolling();
      status.textContent = "二维码已过期，请重新获取。";
      notify("二维码已过期，请重新获取", true);
    }, 300000);
    qrPollTimer = setInterval(async () => {
      if (flowId !== qrFlowId || pollInFlight) return;
      pollInFlight = true;
      try {
        const result = await api("/api/auth/qr/poll", {
          method: "POST",
          body: JSON.stringify({
            yd_device_token: ydDeviceToken,
            browser_user_agent: navigator.userAgent,
          }),
        });
        if (flowId !== qrFlowId) return;
        if (result.status === "scanned") status.textContent = "已扫描，请在网易云音乐 App 中确认登录。";
        if (result.status === "verified") {
          stopQrPolling();
          status.textContent = `已验证：${result.profile?.nickname || "网易云用户"}`;
          onVerified(result);
        }
      } catch (error) {
        stopQrPolling();
        status.textContent = error.message;
        notify(error.message, true);
      } finally {
        pollInFlight = false;
      }
    }, 1200);
  } catch (error) {
    status.textContent = error.message;
    notify(error.message, true);
  }
  finally { busy(button, false); }
}

function openNeteaseReauth() {
  if (!accountLoggedIn) return openAccountModal();
  $("#accountModal").classList.remove("hidden");
  $("#neteaseReauthForm").classList.remove("hidden");
  $("#cookieReauthFields").classList.add("hidden");
  $("#reauthCookieSubmit").classList.add("hidden");
  $("#legacyReauthMethods").classList.remove("hidden");
  $("#smsReauthFields").classList.remove("hidden");
  $("#startQrReauth").classList.remove("hidden");
  $("#qrReauthPanel").classList.add("hidden");
  $("#showLegacyReauth").textContent = "使用 Cookie 导入";
  $("#loggedIn").classList.add("hidden");
  $("#loggedOut").classList.add("hidden");
  document.body.classList.add("modal-open");
  $("#reauthPhone").focus();
}

function closeNeteaseReauth() {
  stopQrPolling();
  $("#neteaseReauthForm").classList.add("hidden");
  if (accountLoggedIn) $("#loggedIn").classList.remove("hidden");
}

async function checkNeteaseCookieStatus(button) {
  busy(button, true, "检查中…");
  try {
    const data = await api("/api/auth/netease-status");
    if (data.valid) {
      $("#neteaseBindingStatus").textContent = "网站账号已登录 · 网易云 Cookie 有效";
      notify("网易云 Cookie 当前有效");
    } else {
      $("#neteaseBindingStatus").textContent = "网站账号已登录 · 网易云 Cookie 已失效";
      notify(data.message || "网易云 Cookie 已失效，请重新验证", true);
    }
  } catch (error) {
    notify(error.message, true);
  } finally {
    busy(button, false);
  }
}

function openAdminModal() {
  if (accountRole !== "admin") return notify("只有管理员可以管理允许名单", true);
  $("#adminModal").classList.remove("hidden");
  document.body.classList.add("modal-open");
  refreshAdminUsers();
  $("#adminUserSearchInput").focus();
}

function closeAdminModal() {
  $("#adminModal").classList.add("hidden");
  document.body.classList.remove("modal-open");
  $("#manageAllowlist").focus();
}

function renderAdminUserRow(user, removable) {
  const row = document.createElement("div");
  row.className = "admin-user-row";
  if (user.avatarUrl) {
    const image = document.createElement("img");
    image.src = user.avatarUrl;
    image.alt = "";
    row.append(image);
  }
  const copy = document.createElement("div");
  copy.className = "admin-user-copy";
  const name = document.createElement("strong");
  name.textContent = user.nickname || "网易云用户";
  const id = document.createElement("small");
  id.textContent = `ID ${user.userId}`;
  copy.append(name, id);
  row.append(copy);
  const badge = document.createElement("span");
  badge.className = "admin-role-badge";
  badge.textContent = user.role === "admin" ? "管理员" : "普通用户";
  row.append(badge);
  if (removable && user.role !== "admin") {
    const remove = document.createElement("button");
    remove.className = "secondary compact";
    remove.textContent = "删除";
    remove.addEventListener("click", () => deleteAdminUser(user.userId, remove));
    row.append(remove);
  }
  return row;
}

async function refreshAdminUsers() {
  const list = $("#adminUserList");
  list.replaceChildren();
  try {
    const data = await api("/api/admin/users");
    if (!data.users.length) {
      list.innerHTML = '<p class="admin-empty">允许名单为空，下一次成功登录会完成初始化</p>';
      return;
    }
    for (const user of data.users) list.append(renderAdminUserRow(user, true));
  } catch (error) { list.textContent = error.message; }
}

async function searchAdminUsers() {
  const query = $("#adminUserSearchInput").value.trim();
  if (!query) return notify("请输入网易云用户关键词", true);
  const button = $("#adminUserSearch");
  busy(button, true, "搜索中…");
  const results = $("#adminSearchResults");
  results.replaceChildren();
  try {
    const data = await api(`/api/admin/search-users?q=${encodeURIComponent(query)}`, {headers: {}});
    if (!data.users.length) {
      results.innerHTML = '<p class="admin-empty">没有找到用户</p>';
      return;
    }
    for (const user of data.users) {
      const row = renderAdminUserRow(user, false);
      const add = document.createElement("button");
      add.className = "primary compact";
      add.textContent = "添加";
      add.addEventListener("click", () => addAdminUser(user, add));
      row.append(add);
      results.append(row);
    }
  } catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
}

async function addAdminUser(user, button) {
  busy(button, true, "添加中…");
  try {
    await api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({userId: user.userId, role: $("#adminUserRole").value}),
    });
    notify(`已添加 ${user.nickname || "网易云用户"}`);
    await refreshAdminUsers();
  } catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
}

async function deleteAdminUser(userId, button) {
  if (!window.confirm("确定要从允许名单删除这个普通用户吗？")) return;
  busy(button, true, "删除中…");
  try {
    await api(`/api/admin/users/${encodeURIComponent(userId)}`, {method: "DELETE"});
    notify("已删除名单账号");
    await refreshAdminUsers();
  } catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
}

function setupSectionNavigation() {
  const links = [...document.querySelectorAll(".page-nav a")];
  const sections = links
    .map(link => ({link, section: document.getElementById(link.getAttribute("href").slice(1))}))
    .filter(item => item.section);
  const update = () => {
    const marker = window.scrollY + $(".topbar").getBoundingClientRect().height + 36;
    let active = sections[0];
    for (const item of sections) {
      if (item.section.classList.contains("hidden")) continue;
      if (item.section.offsetTop <= marker) active = item;
    }
    for (const item of sections) item.link.classList.toggle("active", item === active);
  };
  window.addEventListener("scroll", update, {passive: true});
  window.addEventListener("resize", update);
  requestAnimationFrame(update);
}

$("#searchModeSearch").addEventListener("click", () => setFinderMode("search"));
$("#searchModeId").addEventListener("click", () => setFinderMode("id"));
$("#accountPill").addEventListener("click", openAccountModal);
$("#closeAccountModal").addEventListener("click", closeAccountModal);
$("#accountModal").addEventListener("click", event => { if (event.target === event.currentTarget) closeAccountModal(); });
$("#manageAllowlist").addEventListener("click", openAdminModal);
$("#closeAdminModal").addEventListener("click", closeAdminModal);
$("#adminModal").addEventListener("click", event => { if (event.target === event.currentTarget) closeAdminModal(); });
$("#adminUserSearch").addEventListener("click", searchAdminUsers);
$("#adminUserSearchInput").addEventListener("keydown", event => { if (event.key === "Enter") searchAdminUsers(); });
$("#refreshAdminUsers").addEventListener("click", refreshAdminUsers);
$("#goToBuilder").addEventListener("click", () => $("#videoBuilder").scrollIntoView({behavior: "smooth", block: "start"}));

$("#websiteLoginMode").addEventListener("click", () => setWebsiteAuthMode("login"));
$("#websiteRegisterMode").addEventListener("click", () => { registerQrVerified = false; setWebsiteAuthMode("register"); });
$("#showLegacyRegister").addEventListener("click", (event) => {
  event.preventDefault();
  const showCookie = $("#cookieRegisterFields").classList.contains("hidden");
  stopQrPolling();
  registerUsingLegacy = !showCookie;
  registerQrVerified = false;
  $("#cookieRegisterFields").classList.toggle("hidden", !showCookie);
  $("#legacyRegisterMethods").classList.toggle("hidden", showCookie);
  $("#showLegacyRegister").textContent = registerUsingLegacy ? "使用 Cookie 导入" : "使用验证码/二维码";
  $("#register").textContent = registerUsingLegacy ? "验证并创建用户" : "导入 Cookie 并创建用户";
});

$("#startQrRegister").addEventListener("click", async (event) => {
  $("#smsRegisterFields").classList.add("hidden");
  $("#startQrRegister").classList.add("hidden");
  $("#qrRegisterPanel").classList.remove("hidden");
  await startQrFlow({
    image: $("#qrRegisterImage"), status: $("#qrRegisterStatus"), link: $("#qrRegisterLink"),
    button: event.currentTarget,
    onVerified: () => {
      registerQrVerified = true;
      registerUsingLegacy = true;
      $("#smsRegisterFields").classList.add("hidden");
      $("#register").textContent = "验证并创建用户";
      notify("网易云账号验证成功，请填写网站用户名和密码后创建");
    },
  });
});
$("#refreshQrRegister").addEventListener("click", () => $("#startQrRegister").click());
$("#switchSmsRegister").addEventListener("click", () => {
  stopQrPolling();
  registerQrVerified = false;
  $("#qrRegisterPanel").classList.add("hidden");
  $("#smsRegisterFields").classList.remove("hidden");
  $("#startQrRegister").classList.remove("hidden");
  $("#phone").focus();
});

$("#sendCaptcha").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  busy(button, true, "发送中…");
  try {
    await api("/api/auth/captcha", {method: "POST", body: JSON.stringify({phone: $("#phone").value, country_code: $("#countryCode").value})});
    notify("验证码已发送，请查看短信");
    let seconds = 60;
    button.dataset.label = "获取验证码";
    const timer = setInterval(() => {
      button.textContent = `${seconds--} 秒后重试`;
      if (seconds < 0) { clearInterval(timer); busy(button, false); }
    }, 1000);
  } catch (error) { busy(button, false); notify(error.message, true); }
});

$("#login").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  busy(button, true, "登录中…");
  try {
    await api("/api/auth/login", {method: "POST", body: JSON.stringify({username: $("#websiteUsername").value, password: $("#websitePassword").value})});
    $("#websitePassword").value = "";
    notify("登录成功"); await refreshStatus(); closeAccountModal();
  } catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
});

$("#register").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  busy(button, true, "创建中…");
  let cookieSubmitted = false;
  try {
    const payload = {
      username: $("#registerUsername").value,
      password: $("#registerPassword").value,
    };
    if (!registerUsingLegacy && !registerQrVerified) {
      payload.cookies = await readCookieInput("#registerCookieFile", "#registerCookieText");
      payload.csrf_token = await ensureCookieCsrf();
      cookieSubmitted = true;
    } else if (registerQrVerified) {
      payload.qr = true;
    } else {
      payload.phone = $("#phone").value;
      payload.captcha = $("#captcha").value;
      payload.country_code = $("#countryCode").value;
    }
    await api("/api/auth/register", {method: "POST", body: JSON.stringify(payload)});
    $("#registerPassword").value = "";
    $("#phone").value = "";
    $("#captcha").value = "";
    clearCookieInput("#registerCookieFile", "#registerCookieText");
    notify("网站账号创建成功");
    await refreshStatus();
    closeAccountModal();
  } catch (error) { notify(error.message, true); }
  finally {
    if (cookieSubmitted) clearCookieInput("#registerCookieFile", "#registerCookieText");
    busy(button, false);
  }
});

$("#reauthNetease").addEventListener("click", openNeteaseReauth);
$("#checkNeteaseCookie").addEventListener("click", event => checkNeteaseCookieStatus(event.currentTarget));
$("#showLegacyReauth").addEventListener("click", (event) => {
  event.preventDefault();
  const showCookie = $("#cookieReauthFields").classList.contains("hidden");
  stopQrPolling();
  $("#legacyReauthMethods").classList.toggle("hidden", showCookie);
  $("#cookieReauthFields").classList.toggle("hidden", !showCookie);
  $("#reauthCookieSubmit").classList.toggle("hidden", !showCookie);
  $("#showLegacyReauth").textContent = showCookie ? "使用验证码/二维码" : "使用 Cookie 导入";
  if (!showCookie) {
    $("#smsReauthFields").classList.remove("hidden");
    $("#startQrReauth").classList.remove("hidden");
    $("#qrReauthPanel").classList.add("hidden");
  }
});
$("#reauthCookieSubmit").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  let cookieSubmitted = false;
  busy(button, true, "验证中…");
  try {
    const cookies = await readCookieInput("#reauthCookieFile", "#reauthCookieText");
    const csrf_token = await ensureCookieCsrf();
    cookieSubmitted = true;
    await api("/api/auth/reauth", {
      method: "POST",
      body: JSON.stringify({cookies, csrf_token}),
    });
    clearCookieInput("#reauthCookieFile", "#reauthCookieText");
    notify("网易云绑定已更新");
    closeNeteaseReauth();
    await refreshStatus();
  } catch (error) { notify(error.message, true); }
  finally {
    if (cookieSubmitted) clearCookieInput("#reauthCookieFile", "#reauthCookieText");
    busy(button, false);
  }
});
$("#startQrReauth").addEventListener("click", async (event) => {
  $("#smsReauthFields").classList.add("hidden");
  $("#startQrReauth").classList.add("hidden");
  $("#qrReauthPanel").classList.remove("hidden");
  await startQrFlow({
    image: $("#qrReauthImage"), status: $("#qrReauthStatus"), link: $("#qrReauthLink"),
    button: event.currentTarget,
    onVerified: async () => {
      $("#smsReauthFields").classList.add("hidden");
      notify("网易云绑定已更新");
      closeNeteaseReauth();
      await refreshStatus();
    },
  });
});
$("#refreshQrReauth").addEventListener("click", () => $("#startQrReauth").click());
$("#switchSmsReauth").addEventListener("click", () => {
  stopQrPolling();
  $("#qrReauthPanel").classList.add("hidden");
  $("#smsReauthFields").classList.remove("hidden");
  $("#startQrReauth").classList.remove("hidden");
  $("#reauthPhone").focus();
});
$("#sendReauthCaptcha").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  busy(button, true, "发送中…");
  try {
    await api("/api/auth/captcha", {method: "POST", body: JSON.stringify({phone: $("#reauthPhone").value, country_code: $("#reauthCountryCode").value})});
    notify("验证码已发送，请查看短信");
  } catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
});

$("#reauthNeteaseSubmit").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  busy(button, true, "验证中…");
  try {
    await api("/api/auth/reauth", {
      method: "POST",
      body: JSON.stringify({phone: $("#reauthPhone").value, captcha: $("#reauthCaptcha").value, country_code: $("#reauthCountryCode").value}),
    });
    notify("网易云绑定已更新");
    $("#reauthPhone").value = "";
    $("#reauthCaptcha").value = "";
    closeNeteaseReauth();
    await refreshStatus();
  } catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
});

$("#logout").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  busy(button, true);
  try { await api("/api/auth/logout", {method: "POST", body: "{}"}); notify("已退出登录"); await refreshStatus(); }
  catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
});

$("#inspect").addEventListener("click", () => inspectSong($("#songInput").value));
$("#songInput").addEventListener("keydown", (event) => { if (event.key === "Enter") inspectSong(event.currentTarget.value); });
$("#songInput").addEventListener("input", (event) => {
  if (!selectedSong) return;
  updateVideoSongTarget(event.currentTarget.value.trim() !== String(selectedSong.id));
});

$("#search").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const query = $("#searchInput").value.trim();
  if (!query) return notify("请输入歌名或歌手", true);
  busy(button, true, "搜索中…");
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}`, {headers: {}});
    const container = $("#searchResults");
    container.replaceChildren();
    for (const song of data.songs) {
      const row = document.createElement("button"); row.className = "result-row";
      const copy = document.createElement("span");
      const title = document.createElement("strong"); title.textContent = song.name;
      const meta = document.createElement("small"); meta.textContent = `${song.artist} · ${song.album}`;
      copy.append(title, meta); row.append(copy);
      row.addEventListener("click", async () => {
        row.disabled = true;
        await inspectSong(song.id);
        row.disabled = false;
      });
      container.append(row);
    }
    if (!data.songs.length) container.textContent = "没有找到歌曲";
    container.classList.remove("hidden");
  } catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
});

$("#searchInput").addEventListener("keydown", (event) => { if (event.key === "Enter") $("#search").click(); });

$("#download").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  if (!selectedSong) return notify("请先读取或选择歌曲", true);
  if (selectedSongLocal.ready && !window.confirm("共享素材已经存在。确定要重新下载并覆盖当前素材吗？")) return;
  busy(button, true, "正在下载并校验…");
  applyLocalStatus({status: "downloading", ready: false, message: "正在下载并校验共享素材"});
  let completedLocal = null;
  try {
    const data = await api("/api/song/download", {method: "POST", body: JSON.stringify({song: selectedSong.id, level: $("#quality").value})});
    const value = data.result;
    const qualityLabels = {standard: "标准", higher: "较高", exhigh: "极高", lossless: "无损", hires: "Hi-Res"};
    const lyricLabels = {original: "原文", translation: "翻译", romanization: "罗马音", karaoke: "逐字/卡拉 OK"};
    const lyricTypes = (value.lyric_types || []).map(type => lyricLabels[type] || type);
    completedLocal = {
      ...value.local,
      title: "素材保存完成",
      message: `${value.song.name} — ${value.song.artist} · ${qualityLabels[value.quality] || value.quality || "自动音质"} · 歌词：${lyricTypes.join("、") || "原文"}`,
    };
    notify("下载完成");
    applyLocalStatus(completedLocal);
    await refreshVideoPreview(false);
  } catch (error) {
    if (error.code === "netease_reauth_required") openNeteaseReauth();
    notify(error.message, true);
  }
  finally {
    busy(button, false);
    if (!completedLocal) await refreshSelectedLocalStatus();
  }
});

function videoOptions() {
  return {
    lyric_mode: $("#lyricMode").value,
    background_mode: $("#backgroundMode").value,
    background_color: $("#backgroundColor").value,
    accent_mode: $("#accentMode").value,
    accent_color: $("#accentColor").value,
    spectrum: $("#spectrumEnabled").checked,
    spectrum_opacity: Number($("#spectrumOpacity").value) / 100,
    resolution: $("#videoResolution").value,
    quality: $("#videoQuality").value,
    opening: $("#openingEnabled").checked,
    interlude_cue: $("#interludeEnabled").checked,
  };
}

function updateConditionalOptions() {
  const background = $("#backgroundMode").value;
  $("#backgroundColorWrap").classList.toggle("hidden", background !== "solid");
  $("#customBackgroundWrap").classList.toggle("hidden", background !== "custom");
  $("#accentColorWrap").classList.toggle("hidden", $("#accentMode").value !== "custom");
  $("#spectrumOpacity").disabled = !$("#spectrumEnabled").checked;
  $("#spectrumValue").textContent = `${$("#spectrumOpacity").value}%`;
}

function schedulePreview(silent = false) {
  clearTimeout(previewTimer);
  if (!selectedSongLocal.ready) return;
  previewTimer = setTimeout(() => refreshVideoPreview(silent), 420);
}

async function refreshVideoPreview(silent = false) {
  if (!selectedSong || !selectedSongLocal.ready) return;
  const requestNumber = ++previewRequest;
  const button = $("#refreshPreview");
  busy(button, true, "生成中…");
  $("#previewPlaceholder").textContent = "正在渲染实际画面…";
  try {
    const data = await api("/api/video/preview", {
      method: "POST",
      body: JSON.stringify({song: selectedSong.id, options: videoOptions()}),
    });
    if (requestNumber !== previewRequest) return;
    const image = $("#videoPreviewImage");
    image.src = `${data.preview.url}&client=${Date.now()}`;
    image.style.display = "block";
    $("#previewPlaceholder").classList.add("hidden");
    const preRoll = data.preview.pre_roll_ms ? ` · 含 ${data.preview.pre_roll_ms / 1000} 秒开场预卷` : "";
    $("#previewMeta").textContent = `${data.preview.width} × ${data.preview.height} · 扫色 ${data.preview.accent}${preRoll}`;
  } catch (error) {
    $("#videoPreviewImage").style.display = "none";
    $("#previewPlaceholder").classList.remove("hidden");
    $("#previewPlaceholder").textContent = error.message.includes("本地还没有") ? "请先下载全部素材，再生成画面预览" : error.message;
    $("#previewMeta").textContent = "预览尚未生成";
    if (!silent) notify(error.message, true);
  } finally { busy(button, false); }
}

$("#refreshPreview").addEventListener("click", () => refreshVideoPreview(false));

for (const element of document.querySelectorAll("#videoBuilder select, #videoBuilder input:not([type=file])")) {
  const eventName = element.type === "range" || element.type === "color" ? "input" : "change";
  element.addEventListener(eventName, () => {
    updateConditionalOptions();
    schedulePreview(false);
  });
}

$("#customBackground").addEventListener("change", async (event) => {
  if (!selectedSong || !selectedSongLocal.ready || !event.currentTarget.files.length) {
    if (event.currentTarget.files.length) notify("请先准备当前歌曲的共享素材", true);
    return;
  }
  const data = new FormData();
  data.append("song", selectedSong.id);
  data.append("background", event.currentTarget.files[0]);
  try {
    const response = await fetch("/api/video/background", {method: "POST", body: data});
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result?.error?.message || "上传背景失败");
    notify("自定义背景已保存");
    await refreshVideoPreview(false);
  } catch (error) { notify(error.message, true); }
});

$("#renderVideo").addEventListener("click", async (event) => {
  if (!selectedSong || !selectedSongLocal.ready) return notify("请先准备当前歌曲的共享素材", true);
  if (!accountLoggedIn) return notify("请先登录网站账号", true);
  const button = event.currentTarget;
  const taskSong = Object.freeze({...selectedSong});
  const taskOptions = Object.freeze({...videoOptions()});
  busy(button, true, "正在提交…");
  try {
    const data = await api("/api/video/render", {
      method: "POST",
      body: JSON.stringify({song: taskSong.id, options: taskOptions}),
    });
    const position = Number(data.job.position || 0);
    if (data.job.deduplicated) notify("相同的视频任务已在队列中");
    else notify(position ? `已加入队列，前方有 ${position} 个任务` : "任务已提交，即将开始生成");
    await refreshQueue();
  } catch (error) {
    notify(error.message, true);
  } finally {
    busy(button, false);
    updateRenderAvailability();
  }
});

function stopQueuePolling() {
  clearTimeout(queueTimer);
  queueTimer = null;
}

function clearQueueView() {
  latestQueue = {current: null, queued: [], queued_count: 0, recent: null};
  $("#queueCount").textContent = "等待 0";
  $("#queueIdle strong").textContent = "登录后查看生成队列";
  $("#queueIdle span:not(.dock-mark)").textContent = "网站账号登录后会显示任务进度";
  $("#queueIdle").classList.remove("hidden");
  $("#queueCurrent").classList.add("hidden");
  $("#queueRecent").classList.add("hidden");
  $("#queueProgressFill").style.width = "0%";
}

async function refreshQueue() {
  stopQueuePolling();
  if (!accountLoggedIn) {
    clearQueueView();
    return;
  }
  try {
    const data = await api("/api/video/queue", {headers: {}});
    showQueue(data.queue);
  } catch {
    $("#queueIdle strong").textContent = "暂时无法取得队列状态";
    $("#queueIdle span:not(.dock-mark)").textContent = "请检查服务是否正常运行";
    $("#queueIdle").classList.remove("hidden");
    $("#queueCurrent").classList.add("hidden");
    $("#queueRecent").classList.add("hidden");
    $("#queueProgressFill").style.width = "0%";
  } finally {
    if (accountLoggedIn) queueTimer = setTimeout(refreshQueue, 1500);
  }
}

function showQueue(queue) {
  latestQueue = queue;
  const current = queue.current;
  $("#queueCount").textContent = `等待 ${queue.queued_count || 0}`;
  $("#queueIdle strong").textContent = "当前没有生成任务";
  $("#queueIdle span:not(.dock-mark)").textContent = "生成进度会显示在这里";
  $("#queueIdle").classList.toggle("hidden", Boolean(current || queue.recent));
  $("#queueCurrent").classList.toggle("hidden", !current);
  if (current) {
    const song = current.song || {};
    $("#queueCover").src = song.cover_url || "";
    $("#queueCover").classList.toggle("hidden", !song.cover_url);
    $("#queueSong").textContent = `${song.name || `歌曲 ${current.song_id}`} — ${song.artist || "未知歌手"}`;
    $("#queueMessage").textContent = `${current.resolution} · ${current.message || "等待渲染"}`;
    const percent = Number(current.progress || 0);
    $("#queuePercent").textContent = `${percent}%`;
    $("#queueProgressFill").style.width = `${percent}%`;
  } else {
    $("#queueProgressFill").style.width = "0%";
  }

  const recent = $("#queueRecent");
  recent.replaceChildren();
  if (!queue.recent) {
    recent.classList.add("hidden");
    if (!$("#queueModal").classList.contains("hidden")) renderQueueDetails();
    return;
  }
  const latest = queue.recent;
  const latestArtifactUrl = latest.status === "done" ? latest.result?.url : null;
  if (latestArtifactUrl && latestArtifactUrl !== lastQueueArtifactUrl) {
    lastQueueArtifactUrl = latestArtifactUrl;
    if (selectedSong?.id === latest.song_id) refreshSelectedVideoStatus();
  }
  const label = document.createElement("span");
  const name = latest.song?.name || `歌曲 ${latest.song_id}`;
  const artist = latest.song?.artist || "未知歌手";
  label.textContent = latest.status === "done"
    ? `最近完成：${name} — ${artist} · ${latest.resolution}`
    : `最近失败：${name} — ${artist} · ${latest.resolution} · ${latest.error || "未知错误"}`;
  recent.append(label);
  if (latest.status === "done" && latest.result?.url) {
    const link = document.createElement("a");
    link.href = latest.result.url;
    link.target = "_blank";
    link.textContent = "打开视频";
    recent.append(link);
  }
  recent.classList.toggle("hidden", Boolean(current));
  if (!$("#queueModal").classList.contains("hidden")) renderQueueDetails();
}

function renderQueueDetails() {
  const list = $("#queueDetailList");
  list.replaceChildren();
  const jobs = [];
  if (latestQueue.current) jobs.push({job: latestQueue.current, state: "正在生成"});
  for (const job of latestQueue.queued || []) jobs.push({job, state: `等待第 ${job.position} 位`});
  if (!jobs.length) {
    const empty = document.createElement("p");
    empty.className = "queue-detail-empty";
    empty.textContent = "当前没有正在生成或等待的任务";
    list.append(empty);
    return;
  }
  for (const {job, state} of jobs) {
    const row = document.createElement("article"); row.className = "queue-detail-row";
    if (job.song?.cover_url) {
      const image = document.createElement("img"); image.src = job.song.cover_url; image.alt = "";
      row.append(image);
    }
    const copy = document.createElement("div");
    const title = document.createElement("strong"); title.textContent = job.song?.name || `歌曲 ${job.song_id}`;
    const meta = document.createElement("span"); meta.textContent = `${job.song?.artist || "未知歌手"} · ${job.resolution}`;
    copy.append(title, meta);
    const badge = document.createElement("span"); badge.className = "queue-detail-state"; badge.textContent = state;
    row.append(copy, badge); list.append(row);
  }
}

function openQueueModal() {
  renderQueueDetails();
  $("#queueModal").classList.remove("hidden");
  document.body.classList.add("modal-open");
  $("#closeQueueModal").focus();
}

function closeQueueModal() {
  $("#queueModal").classList.add("hidden");
  document.body.classList.remove("modal-open");
  $("#queueCount").focus();
}

$("#queueCount").addEventListener("click", openQueueModal);
$("#castVideoSelect").addEventListener("change", updateCastDirectLink);
$("#openCastApp").addEventListener("click", openCastApp);
$("#browserCast").addEventListener("click", tryBrowserCast);
$("#downloadCastVideo").addEventListener("click", downloadCastVideo);
$("#closeQueueModal").addEventListener("click", closeQueueModal);
$("#queueModal").addEventListener("click", event => { if (event.target === event.currentTarget) closeQueueModal(); });
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !$("#queueModal").classList.contains("hidden")) closeQueueModal();
  else if (event.key === "Escape" && !$("#adminModal").classList.contains("hidden")) closeAdminModal();
  else if (event.key === "Escape" && !$("#accountModal").classList.contains("hidden")) closeAccountModal();
});

updateConditionalOptions();
setupSectionNavigation();
const castRemote = $("#castMediaElement").remote;
if (castRemote) {
  castRemote.addEventListener("connecting", () => updateBrowserCastHelp("浏览器正在连接远程播放设备…"));
  castRemote.addEventListener("connect", () => {
    updateBrowserCastHelp("浏览器已连接远程播放设备；播放控制由当前网页和浏览器共同管理。");
    notify("浏览器已连接远程播放设备");
  });
  castRemote.addEventListener("disconnect", () => updateBrowserCastHelp());
}
$("#castHelp").textContent = typeof navigator.share === "function"
  ? "点击后在系统分享菜单中选择 BubbleUPnP，再由应用选择局域网内的播放设备。"
  : "当前浏览器或局域网 HTTP 页面不能调用系统分享；点击后会复制播放地址，请在 BubbleUPnP 中打开网络地址。";
refreshStatus();
const rememberedSongId = localStorage.getItem("cloudmusic2ktv.selectedSongId");
if (rememberedSongId) {
  $("#songInput").value = rememberedSongId;
  inspectSong(rememberedSongId, false);
}
