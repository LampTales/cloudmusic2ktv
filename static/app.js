const $ = (selector) => document.querySelector(selector);
let selectedSong = null;
let toastTimer = null;
let previewTimer = null;
let previewRequest = 0;
let accountLoggedIn = false;
let selectedSongLocal = {status: "missing", ready: false, message: "尚未选择歌曲"};
let selectedSongVideos = [];
let videoStatusRequest = 0;
let lastQueueArtifactUrl = null;
let queueTimer = null;
let latestQueue = {current: null, queued: [], queued_count: 0, recent: null};

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    credentials: options.credentials || "same-origin",
    ...options,
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`服务返回异常（HTTP ${response.status}）`); }
  if (!response.ok || !data.ok) throw new Error(data?.error?.message || `请求失败（HTTP ${response.status}）`);
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

async function refreshStatus() {
  try {
    const data = await api("/api/status");
    accountLoggedIn = data.logged_in;
    const pill = $("#accountPill");
    pill.classList.toggle("online", data.logged_in);
    pill.lastElementChild.textContent = data.logged_in ? `已登录 · ${data.profile.nickname}` : "尚未登录";
    $("#loggedOut").classList.toggle("hidden", data.logged_in);
    $("#loggedIn").classList.toggle("hidden", !data.logged_in);
    if (data.logged_in) {
      $("#nickname").textContent = data.profile.nickname;
      $("#avatar").src = data.profile.avatarUrl || "";
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
  $("#castSongCover").src = selectedSong.cover_url || "";
  $("#castSongTitle").textContent = `${selectedSong.name} — ${selectedSong.artist}`;
  $("#castSongMeta").textContent = `ID ${selectedSong.id} · 正在检查服务器本地视频`;
  $("#castSongState").textContent = "正在检查";
  $("#castMissing").textContent = "正在检查这首歌是否已有完整视频…";
  $("#castMissing").classList.remove("hidden");
  $("#castControls").classList.add("hidden");
  selectedSongVideos = [];
}

async function refreshSelectedVideoStatus() {
  if (!selectedSong) return;
  const songId = selectedSong.id;
  const requestNumber = ++videoStatusRequest;
  try {
    const data = await api(`/api/video/local/${encodeURIComponent(songId)}`, {headers: {}, credentials: "omit"});
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
  $("#castSongState").textContent = ready ? `已有 ${selectedSongVideos.length} 个` : "尚无视频";
  $("#castSongMeta").textContent = ready
    ? `${selectedSong.artist} · 选择一个本地版本发送给投屏应用`
    : `${selectedSong.artist} · ${local?.message || "本地还没有完整视频"}`;
  $("#castMissing").textContent = local?.message || "本地还没有这首歌的完整视频";
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
  if (!video) {
    link.removeAttribute("href");
    return;
  }
  link.href = new URL(video.url, window.location.href).href;
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

function updateVideoSongTarget(inputPending = false) {
  if (!selectedSong) return;
  $("#videoSongCover").src = selectedSong.cover_url;
  $("#videoSongTitle").textContent = `${selectedSong.name} — ${selectedSong.artist}`;
  $("#videoSongMeta").textContent = `${selectedSong.album} · ID ${selectedSong.id}`;
  const state = $("#videoSongState");
  state.classList.toggle("pending", inputPending);
  const castState = $("#castSongState");
  castState.classList.toggle("pending", inputPending);
  if (inputPending) {
    state.textContent = "输入尚未应用";
    castState.textContent = "输入尚未应用";
    $("#videoSongHint").textContent = "请点击 02 区的“读取”；读取成功后，这里才会切换到输入框中的歌曲。";
  } else {
    state.textContent = "已同步 02 区";
    castState.textContent = selectedSongVideos.length ? `已有 ${selectedSongVideos.length} 个` : "正在检查";
    $("#videoSongHint").textContent = "04 区跟随 02 区最后一次成功读取的歌曲；仅修改输入框不会切换歌曲。";
  }
}

async function refreshSelectedLocalStatus() {
  if (!selectedSong) return;
  const songId = selectedSong.id;
  try {
    const data = await api(`/api/song/local/${encodeURIComponent(songId)}`, {headers: {}, credentials: "omit"});
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
  const download = $("#download");
  download.textContent = selectedSongLocal.ready ? "重新下载素材" : "下载全部素材";
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
  const available = Boolean(selectedSong && selectedSongLocal.ready && accountLoggedIn);
  button.disabled = !available;
  if (!selectedSong) $("#renderAvailability").textContent = "请先选择歌曲";
  else if (!selectedSongLocal.ready) $("#renderAvailability").textContent = "请先下载完整的共享素材";
  else if (!accountLoggedIn) $("#renderAvailability").textContent = "登录后可以提交视频任务";
  else $("#renderAvailability").textContent = "已可加入共享生成队列";
}

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
    await api("/api/auth/login", {method: "POST", body: JSON.stringify({phone: $("#phone").value, captcha: $("#captcha").value, country_code: $("#countryCode").value})});
    $("#phone").value = ""; $("#captcha").value = "";
    notify("登录成功"); await refreshStatus();
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
  } catch (error) { notify(error.message, true); }
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
  if (!accountLoggedIn) return notify("请先登录网易云账号", true);
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

async function refreshQueue() {
  clearTimeout(queueTimer);
  try {
    const data = await api("/api/video/queue", {headers: {}, credentials: "omit"});
    showQueue(data.queue);
  } catch {
    $("#queueIdle").textContent = "暂时无法取得队列状态";
    $("#queueIdle").classList.remove("hidden");
    $("#queueCurrent").classList.add("hidden");
  } finally {
    queueTimer = setTimeout(refreshQueue, 1500);
  }
}

function showQueue(queue) {
  latestQueue = queue;
  const current = queue.current;
  $("#queueCount").textContent = `等待 ${queue.queued_count || 0}`;
  $("#queueIdle").classList.toggle("hidden", Boolean(current));
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
  recent.classList.remove("hidden");
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
$("#closeQueueModal").addEventListener("click", closeQueueModal);
$("#queueModal").addEventListener("click", event => { if (event.target === event.currentTarget) closeQueueModal(); });
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !$("#queueModal").classList.contains("hidden")) closeQueueModal();
});

updateConditionalOptions();
$("#castHelp").textContent = typeof navigator.share === "function"
  ? "点击后在系统分享菜单中选择 BubbleUPnP，再由应用选择局域网内的播放设备。"
  : "当前浏览器或局域网 HTTP 页面不能调用系统分享；点击后会复制播放地址，请在 BubbleUPnP 中打开网络地址。";
refreshStatus();
refreshQueue();
const rememberedSongId = localStorage.getItem("cloudmusic2ktv.selectedSongId");
if (rememberedSongId) $("#songInput").value = rememberedSongId;
inspectSong($("#songInput").value, false);
