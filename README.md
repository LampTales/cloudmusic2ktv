# CloudMusic2KTV

CloudMusic2KTV 从网易云音乐获取歌曲、歌词、封面和音频，生成带歌词动画的 KTV 视频，并通过网页提供预览、播放、下载和投屏链接。

模型歌词预处理由独立的 [`lyric_align`](https://github.com/LampTales/lyric_align) 仓库提供。

项目由两个运行时组成：前端是静态页面和 `/api/*` 反向代理，后端是 API、网易云访问、素材存储和视频渲染。后端保存全部 `instance/` 与 `outputs/` 数据；前端节点只提供页面并转发媒体流。

使用网易云内容时请遵守当地法律、平台条款和版权方要求。本项目不提供音乐版权。

## 选择部署方式

| 方式 | 适用场景 | 文件 |
| --- | --- | --- |
| 前后端分机 Docker | 推荐的正式部署 | `deploy-examples/compose.backend.yml`、`deploy-examples/compose.frontend.yml` |
| 同机 Docker 或源码运行 | 开发和调试 | 见 [ARCHITECTURE.md](ARCHITECTURE.md) |

正式部署通常让公网 HTTPS 代理指向前端节点；前端再通过私有网络或 VPN 访问后端。客户端只访问前端地址。`deploy-examples/` 目录中的文件是本仓库提供的部署示例；实际 `instance/`、`outputs/` 和模型目录应放在部署环境的挂载目录中。

## 正式部署：前后端分机

需要两台能通过 ZeroTier、VPN 或其他私有网络互通的主机：后端主机负责 CPU、磁盘、模型和视频生成，前端主机负责公网 HTTPS、静态页面和代理。前端主机必须能够访问后端的 `/api/healthz`，后端防火墙只允许前端主机访问后端端口。

### 后端主机

```bash
mkdir -p cloudmusic2ktv-backend/docker-data/{instance,outputs}
cd cloudmusic2ktv-backend
curl -fsSLo compose.yml https://raw.githubusercontent.com/LampTales/cloudmusic2ktv/main/deploy-examples/compose.backend.yml
curl -fsSLo .env https://raw.githubusercontent.com/LampTales/cloudmusic2ktv/main/deploy-examples/backend.env.example
```

建议使用如下目录布局。`docker-data` 可以放在其他磁盘；模型目录不是固定路径，关键是将它通过 `LYRIC_MODELS_DIR` 挂载到容器的 `/models`。

```text
cloudmusic2ktv-backend/
├── compose.yml
├── .env
└── docker-data/
    ├── instance/
    ├── outputs/
    └── models/
        └── huggingface/
            └── hub/
                ├── models--adefossez--HTDemucs/
                └── models--NextFire--mms-300m-ForcedAligner-karaoke-ja-Latn/
```

也可以把 `models/` 放到独立的大容量磁盘，例如 `/srv/cloudmusic2ktv-models`；不要把模型路径写死为某个系统目录。

在后端主机联网下载以下固定版本，服务运行时离线加载。迁移到其他磁盘时请复制完整的 `huggingface/` 目录，避免模型文件链接失效：

```bash
mkdir -p docker-data/models
python3 -m venv /tmp/cloudmusic2ktv-hf
/tmp/cloudmusic2ktv-hf/bin/pip install --upgrade huggingface_hub
export HF_HOME="$PWD/docker-data/models/huggingface"
/tmp/cloudmusic2ktv-hf/bin/hf download adefossez/HTDemucs \
  --revision cbc8a9b1a87023b7fd74e7b3412e6321c0eab003
/tmp/cloudmusic2ktv-hf/bin/hf download NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn \
  --revision 2ab2b5f46539ee284703c281f286b01d2410ee12
```

模型来源和版本：

- [HTDemucs](https://huggingface.co/adefossez/HTDemucs)，revision `cbc8a9b1a87023b7fd74e7b3412e6321c0eab003`；
- [NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn](https://huggingface.co/NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn)，revision `2ab2b5f46539ee284703c281f286b01d2410ee12`。

请同时遵守模型各自的许可证。下载完成后，确保容器用户可读：

```bash
chmod -R a+rX docker-data/models
```

在 `.env` 至少设置：

```dotenv
CLOUDMUSIC2KTV_BACKEND_BIND_ADDRESS=<后端私有IP>
CLOUDMUSIC2KTV_BACKEND_PORT=7860
CLOUDMUSIC2KTV_BASE_PATH=/ktv       # 根路径部署则留空
LYRIC_MODELS_DIR=./docker-data/models
LYRIC_DEVICE=cpu
```

容器内部固定监听 `0.0.0.0:7860`，`CLOUDMUSIC2KTV_BACKEND_PORT` 是宿主机发布端口。给容器用户分配数据目录的写入权限，然后启动（以下权限命令适用于 Linux 后端主机）：

```bash
sudo chown -R 10001:10001 docker-data/instance docker-data/outputs
docker compose pull
docker compose up -d
curl -fsS 'http://<后端私有IP>:7860/api/healthz'
```

将检查命令中的地址和端口替换为 `.env` 中的值；正常返回 `{"ok":true,"status":"healthy"}`。

### 前端主机

```bash
mkdir -p cloudmusic2ktv-frontend
cd cloudmusic2ktv-frontend
curl -fsSLo compose.yml https://raw.githubusercontent.com/LampTales/cloudmusic2ktv/main/deploy-examples/compose.frontend.yml
curl -fsSLo .env https://raw.githubusercontent.com/LampTales/cloudmusic2ktv/main/deploy-examples/frontend.env.example
```

编辑 `.env`：

```dotenv
CLOUDMUSIC2KTV_BACKEND_UPSTREAM=http://<后端私有IP>:7860
CLOUDMUSIC2KTV_FRONTEND_BIND_ADDRESS=127.0.0.1
CLOUDMUSIC2KTV_FRONTEND_PORT=8080
```

```bash
docker compose pull
docker compose up -d
```

让公网 Nginx、Caddy 或同类代理把域名转发到前端 `127.0.0.1:8080`。如果使用 `/ktv/` 路径前缀，设置后端 `CLOUDMUSIC2KTV_BASE_PATH=/ktv`，并让公网代理在转发到前端容器时剥离 `/ktv`：例如外部 `/ktv/api/healthz` 应转发为 `/api/healthz`。入口 `/ktv` 应重定向到带尾部斜杠的 `/ktv/`。

## 首次使用

1. 打开前端地址，注册网站账号。允许名单为空时，第一个成功注册的账号自动成为 `root`。
2. 按页面提示绑定网易云账号（二维码、短信或受控的 Cookie 导入方式）。Cookie 只保存在后端。
3. 搜索歌曲或浏览歌单，先下载全部素材，再在视频面板选择歌词、背景、扫色、分辨率和音频等选项。
4. 提交生成任务，在队列中查看进度。生成完成后可预览、播放、下载或生成短期投屏链接。

传统模式使用歌曲自带的歌词时间轴；模型模式自动处理字级对齐，支持纯伴奏、假名/罗马字标注和平滑扫色。使用模型模式前须完成上面的模型下载和挂载。

角色有 `root`、`admin`、`user` 三种。`root` 可管理其他成员，不能被删除；`admin` 只能管理普通用户。删除成员会撤销访问并清除其账号和网易云绑定，再次使用需要重新注册；共享素材和视频保留。

## 配置参考

部署时最常调整的变量如下：

| 变量 | 用途 |
| --- | --- |
| `CLOUDMUSIC2KTV_BASE_PATH` | 外部路径前缀，如 `/ktv`；根路径留空 |
| `CLOUDMUSIC2KTV_BACKEND_BIND_ADDRESS` / `..._PORT` | 后端宿主机监听地址和端口 |
| `CLOUDMUSIC2KTV_BACKEND_UPSTREAM` | 前端访问后端的完整 URL |
| `CLOUDMUSIC2KTV_FRONTEND_BIND_ADDRESS` / `..._PORT` | 前端宿主机监听地址和端口 |
| `CLOUDMUSIC2KTV_TRUST_PROXY` | 仅在受控反向代理后设为 `1` |
| `CLOUDMUSIC2KTV_ALLOW_INSECURE_COOKIE_IMPORT` | 仅可信 HTTP 调试网络设为 `1` |
| `CLOUDMUSIC2KTV_MEDIA_SIGNING_KEY` | 投屏 URL 的 HMAC 密钥；正式环境应固定并备份 |
| `LYRIC_MODELS_DIR` | 宿主机模型根目录（只读挂载到 `/models`） |
| `LYRIC_DEMUCS_MODEL_PATH` / `LYRIC_CTC_MODEL_PATH` | 容器内模型 snapshot 路径 |
| `LYRIC_DEVICE` | 推理设备，当前使用 `cpu` |

完整默认值见 `.env.example`、`deploy-examples/backend.env.example` 和 `deploy-examples/frontend.env.example`。对齐和平滑扫色参数建议先保留默认值；含义和兼容规则见 [ARCHITECTURE.md](ARCHITECTURE.md#模型参数与依赖)。

同机 Docker 构建和源码运行只用于开发调试，包含依赖安装、本地端口和测试命令，见 [ARCHITECTURE.md](ARCHITECTURE.md) 的“本地调试与验证”。

## 数据、备份和安全

后端节点必须备份：

```text
instance/sessions/             网站会话
instance/accounts.json         网站账号
instance/allowlist.json        允许名单和角色
instance/netease_bindings.json 网易云绑定 Cookie
instance/video_jobs.json      视频任务日志
instance/media_signing.key     投屏签名密钥
outputs/                       音频、歌词、封面、模型产物和视频
```

不要把这些文件提交到仓库或暴露给前端。生产环境只公开 HTTPS 前端，后端端口限制为前端节点可访问；Gunicorn 必须保持单 worker。JSON 存储适合可信、低流量、单实例部署，不支持多节点共享队列。

## 开发文档

代码结构、模型集成契约和测试命令见 [ARCHITECTURE.md](ARCHITECTURE.md)。
