const $ = (selector) => document.querySelector(selector);
let selectedSong = null;
let toastTimer = null;

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
  selectedSong = song;
  $("#songInput").value = song.id;
  $("#cover").src = song.cover_url;
  $("#songName").textContent = song.name;
  $("#songMeta").textContent = `${song.artist} · ${song.album} · ID ${song.id}`;
  $("#songPreview").classList.remove("hidden");
  $("#downloadResult").classList.add("hidden");
  if (shouldScroll) $("#songPreview").scrollIntoView({behavior: "smooth", block: "center"});
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
  } catch (error) { notify(error.message, true); }
  finally { busy(button, false); }
});

refreshStatus();
inspectSong($("#songInput").value, false);
