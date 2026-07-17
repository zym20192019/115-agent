# 115 Agent WebUI

独立的 FastAPI + React/Vite 控制台，复用上级 `agent_115` SDK。提供文件浏览、云解压任务、Cookie 配置和实时 SSE 日志。

## 后端

```bash
pip install -e ".[web]"
$env:WEB_ADMIN_USER = "admin"
$env:WEB_ADMIN_PASSWORD = "请修改为强密码"
uvicorn web.backend.app:app --reload
```

后端默认 `http://localhost:8000`。开发前端运行后，访问 `http://localhost:5173`。

## 前端

```bash
cd web/frontend
npm install
npm run dev
```

首次登录账号来自 `WEB_ADMIN_USER` / `WEB_ADMIN_PASSWORD`，默认值是 `admin` / `change-me`，仅适合本地开发。登录后在右上角设置中写入 115 Cookie。Cookie 保存在当前服务进程内存，不写入仓库文件；重启服务后需要重新配置。

## 实时任务

云解压在后端线程池执行，浏览器通过 `/api/jobs/{job_id}/events` 接收 SSE 事件。页面断开不会停止任务，重新打开页面可读取任务状态。密码压缩包会暂停在 `waiting_password`，通过页面提交密码后继续。

生产部署需要在 HTTPS、反向代理和正式持久化会话/密钥存储后使用；当前任务和登录存储为内存实现，适合单机开发与内网测试。
