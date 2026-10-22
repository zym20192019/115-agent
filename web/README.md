# 115 Agent WebUI

FastAPI + React/Vite 控制台，复用上级 `agent_115` SDK：目录浏览（面包屑导航）、筛选 / 全盘搜索、新建文件夹、重命名、云解压任务（同名文件夹 / 当前目录、密码输入、取消、实时日志）。

## 部署

```bash
pip install -e ".[web]"
cd web/frontend && npm ci && npm run build && cd ../..

export WEB_ADMIN_USER=admin
export WEB_ADMIN_PASSWORD='请设置强密码'
uvicorn web.backend.app:app --host 0.0.0.0 --port 8000
```

后端会直接托管 `web/frontend/dist`，访问 `http://服务器:8000` 即可。前端与 API 必须同源（已移除 CORS），对外暴露时请放在 HTTPS 反向代理后面。

- `WEB_ADMIN_PASSWORD` 未设置或为 `change-me` 时，启动时会随机生成一次性密码并打印到日志。
- 115 Cookie 只在服务端读取（`PAN115_COOKIE` 环境变量 / `.env` / `$PAN115_HOME/.env`），每次请求实时读取，修改 `.env` 后无需重启。页面右上角显示是否已配置。
- 115 Cookie 失效时接口返回 409（不会把你踢回登录页）；网络错误返回 502。
- 登录会话保存在内存，有效期 24 小时，重启服务后需重新登录。

## 开发

```bash
uvicorn web.backend.app:app --reload        # http://localhost:8000
cd web/frontend && npm ci && npm run dev    # http://localhost:5173，/api 代理到 8000
```

## 任务

云解压在后端线程池执行，页面每 2 秒刷新任务状态，也可通过 `/api/jobs/{job_id}/events` 订阅 SSE（任务结束后发送 `event: end` 并关闭）。关闭页面不会停止任务。

- 加密压缩包会暂停在「等待密码」，在任务卡片中输入密码继续；10 分钟未输入则结束为 `password_required`。
- 进行中的任务可取消。
- 内存中保留最近 100 个已结束任务。

接口：`POST /api/jobs/unzip`，参数 `{parent_cid, file_id, mode: "each"|"direct", password, delete_zip, skip_pre}`（兼容旧的 `path` 参数）。
