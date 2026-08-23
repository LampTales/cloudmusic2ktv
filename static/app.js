const $ = (selector) => document.querySelector(selector);
let selectedSong = null;
let toastTimer = null;
let previewTimer = null;
let previewRequest = 0;
let renderJobActive = false;
let renderTaskSong = null;

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
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
    const pill = $("#accountPill");
    pill.classList.toggle("online", data.logged_in);
    pill.lastElementChild.textContent = data.logged_in ? `已登录 · ${data.profile.nickname}` : "尚未登录";
    $("#loggedOut").classList.toggle("hidden", data.logged_in);
    $("#loggedIn").classList.toggle("hidden", !data.logged_in);
    if (data.logged_in) {
      $("#nickname").textContent = data.profile.nickname;
      $("#avatar").src = data.profile.avatarUrl || "";
    }
  } catch (error) { notify(error.message, true); }
}

async function inspectSong(value, shouldScroll = true) {
  const button = $("#inspect");
  busy(button, true, "读取中…");
  try {
    const data = await api("/api/song/inspect", {method: "POST", body: JSON.stringify({song: value})});
    showSong(data.song, shouldScroll);
  } catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
}

function showSong(song, shouldScroll = true) {
  selectedSong = {...song};
  localStorage.setItem("cloudmusic2ktv.selectedSongId", String(song.id));
  $("#songInput").value = song.id;
  $("#cover").src = song.cover_url;
  $("#songName").textContent = song.name;
  $("#songMeta").textContent = `${song.artist} · ${song.album} · ID ${song.id}`;
  $("#songPreview").classList.remove("hidden");
  $("#videoBuilder").classList.remove("hidden");
  $("#downloadResult").classList.add("hidden");
  $("#videoResult").classList.add("hidden");
  updateVideoSongTarget();
  schedulePreview(true);
  if (shouldScroll) $("#songPreview").scrollIntoView({behavior: "smooth", block: "center"});
}

function updateVideoSongTarget(inputPending = false) {
  if (!selectedSong) return;
  $("#videoSongCover").src = selectedSong.cover_url;
  $("#videoSongTitle").textContent = `${selectedSong.name} — ${selectedSong.artist}`;
  $("#videoSongMeta").textContent = `${selectedSong.album} · ID ${selectedSong.id}`;
  const state = $("#videoSongState");
  state.classList.toggle("pending", inputPending);
  if (inputPending) {
    state.textContent = "输入尚未应用";
    $("#videoSongHint").textContent = "请点击 02 区的“读取”；读取成功后，这里才会切换到输入框中的歌曲。";
  } else if (renderJobActive && renderTaskSong?.id !== selectedSong.id) {
    state.textContent = "下次生成使用";
    $("#videoSongHint").textContent = `当前任务仍锁定 ID ${renderTaskSong.id}；这里的新选择只影响下一次生成。`;
  } else {
    state.textContent = "已同步 02 区";
    $("#videoSongHint").textContent = "03 区跟随 02 区最后一次成功读取的歌曲；仅修改输入框不会切换歌曲。";
  }
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
      const image = document.createElement("img"); image.src = song.cover_url; image.alt = "";
      const copy = document.createElement("span");
      const title = document.createElement("strong"); title.textContent = song.name;
      const meta = document.createElement("small"); meta.textContent = `${song.artist} · ${song.album}`;
      copy.append(title, meta); row.append(image, copy); row.addEventListener("click", () => showSong(song));
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
  busy(button, true, "正在下载并校验…");
  try {
    const data = await api("/api/song/download", {method: "POST", body: JSON.stringify({song: selectedSong.id, level: $("#quality").value})});
    const value = data.result;
    const result = $("#downloadResult");
    result.innerHTML = "";
    const title = document.createElement("h3"); title.textContent = "素材已完整保存";
    const lines = [
      `目录：${value.directory}`,
      `音频：${value.quality || "自动"} · ${value.bitrate ? Math.round(value.bitrate / 1000) + " kbps" : "码率未知"} · ${(value.size / 1024 / 1024).toFixed(1)} MB`,
      `歌词：${value.timeline_lines} 行毫秒时间轴`,
    ];
    result.append(title, ...lines.map(text => { const p = document.createElement("p"); p.textContent = text; return p; }));
    result.classList.remove("hidden"); result.scrollIntoView({behavior: "smooth", block: "center"});
    notify("下载完成");
    await refreshVideoPreview(false);
  } catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
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
  previewTimer = setTimeout(() => refreshVideoPreview(silent), 420);
}

async function refreshVideoPreview(silent = false) {
  if (!selectedSong) return;
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
  if (!selectedSong || !event.currentTarget.files.length) return;
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
  if (!selectedSong) return notify("请先选择歌曲", true);
  const button = event.currentTarget;
  const taskSong = Object.freeze({...selectedSong});
  const taskOptions = Object.freeze({...videoOptions()});
  renderTaskSong = taskSong;
  renderJobActive = true;
  $("#renderTaskSong").textContent = `本次任务已锁定：${taskSong.name} — ${taskSong.artist}（ID ${taskSong.id}）`;
  $("#renderTaskSong").classList.remove("hidden");
  updateVideoSongTarget();
  busy(button, true, "正在生成视频…");
  $("#renderProgress").classList.remove("hidden");
  $("#videoResult").classList.add("hidden");
  setRenderProgress(0, "正在创建任务");
  try {
    const data = await api("/api/video/render", {
      method: "POST",
      body: JSON.stringify({song: taskSong.id, options: taskOptions}),
    });
    await pollVideoJob(data.job.id, taskSong);
  } catch (error) {
    notify(error.message, true);
    setRenderProgress(0, `生成失败：${error.message}`);
  } finally {
    renderJobActive = false;
    updateVideoSongTarget();
    busy(button, false);
  }
});

async function pollVideoJob(jobId, taskSong) {
  while (true) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    const data = await api(`/api/video/jobs/${encodeURIComponent(jobId)}`, {headers: {}});
    const job = data.job;
    setRenderProgress(job.progress || 0, job.message || "正在处理");
    if (job.status === "error") throw new Error(job.error || "视频生成失败");
    if (job.status === "done") {
      showVideoResult(job.result, taskSong);
      notify("完整视频已经生成");
      return;
    }
  }
}

function setRenderProgress(percent, message) {
  $("#progressFill").style.width = `${percent}%`;
  $("#progressPercent").textContent = `${percent}%`;
  $("#progressMessage").textContent = message;
}

function showVideoResult(value, taskSong) {
  const result = $("#videoResult");
  result.replaceChildren();
  const title = document.createElement("h3"); title.textContent = `${taskSong.name} 视频生成完成`;
  const song = document.createElement("p"); song.textContent = `任务歌曲：${taskSong.name} — ${taskSong.artist} · ID ${taskSong.id}`;
  const path = document.createElement("p"); path.textContent = `文件：${value.path}`;
  const detail = document.createElement("p");
  detail.textContent = `${value.resolution} · ${(value.duration_ms / 1000).toFixed(1)} 秒 · ${(value.size / 1024 / 1024).toFixed(1)} MB`;
  const link = document.createElement("a"); link.href = value.url; link.textContent = "在浏览器中打开生成的视频"; link.target = "_blank";
  result.append(title, song, path, detail, link);
  result.classList.remove("hidden");
}

updateConditionalOptions();
refreshStatus();
const rememberedSongId = localStorage.getItem("cloudmusic2ktv.selectedSongId");
if (rememberedSongId) $("#songInput").value = rememberedSongId;
inspectSong($("#songInput").value, false);
