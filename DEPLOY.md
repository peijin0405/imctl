# Railway 部署指南 — Washon Investment Suite

## 前置条件

- [Railway 账号](https://railway.app)
- Railway CLI（可选）：`npm install -g @railway/cli`
- 项目已 push 到 GitHub

---

## 第一步：确保静态数据文件进入 git

以下文件当前为 untracked，**部署前必须 add 并 commit**：

```bash
git add web/active.jsonl           # 2.8 MB — 投资人数据库（主数据源）
git add scraper/investor_embeddings.json  # 10 MB — 语义匹配嵌入缓存
git commit -m "add investor data files for deployment"
git push
```

> **注意**：`scraper/investor_embeddings.json` 体积较大（10 MB）。
> 如果不想入库，可在 Railway 首次启动后通过 M9 功能触发嵌入重建，
> 但首次匹配会较慢（需调用 Voyage AI API）。

---

## 第二步：在 Railway 创建项目

### 方式 A：控制台（推荐新手）

1. 打开 [railway.app/new](https://railway.app/new)
2. 选择 **Deploy from GitHub repo** → 授权并选择本仓库
3. Railway 会自动检测 `railway.toml` 和 `Procfile`，直接进入下一步

### 方式 B：CLI

```bash
railway login
railway init        # 关联当前目录到新 Railway 项目
railway up          # 推送并部署
```

---

## 第三步：添加 Volume（持久化用户数据）

M9 Pipeline 和 M1 分析结果存在 `DATA_DIR` 指向的目录里。
不挂载 Volume 的话，每次 redeploy 数据会丢失。

1. Railway 控制台 → 你的服务 → **Volumes** 标签
2. 点击 **Add Volume**
3. 挂载路径填写：`/data`
4. 保存

---

## 第四步：配置环境变量

Railway 控制台 → 服务 → **Variables** 标签，逐一添加：

| 变量名 | 值 | 说明 |
|--------|----|------|
| `DATA_DIR` | `/data` | 必填，对应上面 Volume 挂载路径 |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Claude API，M9 邮件生成必需 |
| `GEMINI_API_KEY` | `AI...` | M1 BP 解析必需 |
| `VOYAGE_API_KEY` | `pa-...` | 语义匹配必需（可用 Anthropic Voyage） |
| `EXA_API_KEY` | `...` | 搜索增强（可选） |
| `SECRET_KEY` | 随机字符串 | Flask session 密钥，建议用 `openssl rand -hex 32` 生成 |
| `GROQ_API_KEY` | `...` | 可选 |
| `SEC_API_KEY` | `...` | 可选，SEC 数据抓取用 |
| `SENTRY_DSN` | `...` | 可选，错误监控 |

> `DATABASE_URL` 和 `REDIS_URL`：当前版本未强依赖数据库和 Redis，
> 可先不填；若后续开启 Celery 任务队列再添加 Railway PostgreSQL/Redis 插件。

---

## 第五步：触发部署

Railway 检测到 GitHub push 时自动部署。手动触发：

```bash
railway up
# 或在控制台点击 Deploy
```

部署日志中应看到：
```
[INFO] Starting gunicorn 21.x.x
[INFO] Listening at: http://0.0.0.0:PORT
[INFO] Worker booting (pid: ...)
```

---

## 第六步：绑定自定义域名（可选）

Railway 控制台 → 服务 → **Settings** → **Domains** → **Generate Domain**
会生成 `*.up.railway.app` 域名，也可绑定自己的域名。

---

## 常见问题

### `ModuleNotFoundError: No module named 'scraper'`

Railway 从项目根目录启动，`demo/app.py` 里已经 `sys.path.insert(0, ROOT)`，
确保根目录下 `scraper/` 文件夹存在且已 commit。

### 匹配结果为空 / 语义分数全为 0

`scraper/investor_embeddings.json` 没有入库导致首次启动无缓存。
解决方法：在 Railway shell 里执行：
```bash
python scraper/m_matcher.py --precompute
```

### 上传文件后 500 错误

确认 `DATA_DIR=/data` 已设置且 Volume 已挂载。
`UPLOAD_DIR` 会在启动时自动 mkdir，无需手动创建。

### 每次部署后 M9 数据消失

确认 Volume 已挂载到 `/data`，且 `DATA_DIR=/data` 已设置。
`m9_pipeline.json` 和 `m1_analyses.json` 会写在 `/data/` 下，Volume 保证持久化。

---

## 本地开发

```bash
cp .env.example .env
# 填写 .env 中的 API keys
pip install -r requirements.txt
python demo/app.py          # http://localhost:5001
# 或用 gunicorn 本地测试：
gunicorn demo.app:app --workers 2 --timeout 120 --bind 0.0.0.0:5001
```
