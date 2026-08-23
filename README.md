# CloudMusic2KTV

第一阶段：从网易云音乐网页播放器所用的接口获取一首歌制作个人练唱视频所需的素材。

当前可用能力：

- 手机号 + 短信验证码登录，使用登录账号的网页播放权益；
- 通过歌曲 ID、歌曲链接，或“歌名 + 歌手”搜索歌曲；
- 下载完整音频和高清封面，并校验音频大小与 MD5；
- 保存原文、翻译、罗马音、卡拉 OK 原始歌词；
- 生成统一的 `lyrics_timeline.json` 毫秒时间轴，包含当前行的开始/结束时间，供下一阶段的视频渲染使用。

## 启动

建议使用 Python 3.11 或更新版本：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

浏览器打开 <http://127.0.0.1:7860>。

项目默认只监听 `127.0.0.1`，不会把登录界面开放到局域网。首次登录成功后，会话 Cookie 保存在 `instance/netease_cookies.json`；手机号与验证码不会写入磁盘。点击“退出”会同时清除网易云会话和本地 Cookie 文件。

## 产物

每首歌曲位于 `outputs/<歌曲ID>_<歌手>_<歌名>/`：

```text
audio.mp3 / audio.flac
cover.jpg / cover.png
metadata.json
lyrics.lrc
lyrics_translated.lrc
lyrics_romanized.lrc
lyrics_karaoke_raw.lrc
lyrics_raw.json
lyrics_timeline.json
```

网易云并非给每首歌都提供逐字歌词。`lyrics_timeline.json` 总会尽量提供逐行时间；如果 `lyrics_karaoke_raw.lrc` 为空，下一阶段可以用相邻行时间做行内匀速标蓝，或另行生成逐字时间轴。

## 测试

```powershell
pytest -q
```

接口可能随网易云网页改版而变化。程序会保留 `lyrics_raw.json` 和音频权限响应到 `metadata.json`，便于诊断。请仅下载和处理你依法有权访问、并仅用于个人练唱的内容。
