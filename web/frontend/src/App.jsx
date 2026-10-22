import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Archive, ArrowUp, ChevronRight, File, Folder, FolderPlus, HardDrive, KeyRound, Loader2,
  LogOut, Pencil, RefreshCw, Search, X,
} from 'lucide-react'
import './styles.css'

// ───────────────────────── 工具 ─────────────────────────

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status }
}

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    credentials: 'include',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = data?.detail
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(d => d.msg).join('；') : `请求失败（HTTP ${res.status}）`
    throw new ApiError(message, res.status)
  }
  return data
}

const fmtSize = (n) => {
  let size = Number(n) || 0
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (size < 1024) return `${size.toFixed(unit === 'B' ? 0 : 1)} ${unit}`
    size /= 1024
  }
  return `${size.toFixed(1)} PB`
}

const fmtTime = (value) => {
  if (!value) return '—'
  const num = Number(value)
  if (Number.isFinite(num) && num > 1e9) return new Date(num * 1000).toLocaleString('zh-CN', { hour12: false })
  return String(value)
}

const STATUS_TEXT = {
  queued: '排队中', running: '进行中', waiting_password: '等待密码', success: '完成', failed: '失败',
  cancelled: '已取消', timeout: '超时', incomplete: '不完整', damaged: '已损坏', password_required: '需要密码', skipped: '已跳过',
}
const ACTIVE = new Set(['queued', 'running', 'waiting_password'])
const statusTone = (s) => (s === 'success' ? 'ok' : ACTIVE.has(s) ? 'active' : ['failed', 'damaged', 'timeout'].includes(s) ? 'bad' : 'warn')

// ───────────────────────── 通用组件 ─────────────────────────

function useToasts() {
  const [toasts, setToasts] = useState([])
  const push = useCallback((message, tone = 'info') => {
    const id = Math.random().toString(36).slice(2)
    setToasts(list => [...list, { id, message, tone }])
    setTimeout(() => setToasts(list => list.filter(t => t.id !== id)), 4200)
  }, [])
  const view = <div className="toasts">{toasts.map(t => <div key={t.id} className={`toast ${t.tone}`}>{t.message}</div>)}</div>
  return [push, view]
}

function Modal({ title, onClose, children, footer }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="backdrop" onMouseDown={onClose}>
      <div className="modal" role="dialog" aria-modal="true" onMouseDown={e => e.stopPropagation()}>
        <div className="modal-head"><h3>{title}</h3><button className="icon-btn" onClick={onClose} aria-label="关闭"><X size={18} /></button></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

function Button({ busy, children, className = '', ...props }) {
  return <button className={`btn ${className}`} disabled={busy || props.disabled} {...props}>{busy && <Loader2 size={14} className="spin" />}{children}</button>
}

// ───────────────────────── 登录 ─────────────────────────

function Login({ onLogin }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try { await api('/api/auth/login', { method: 'POST', body: { username, password } }); onLogin() }
    catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }
  return (
    <main className="login">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand"><span className="brand-logo">115</span><div><strong>115 Agent</strong><small>网盘操作控制台</small></div></div>
        <label className="field">管理员账号<input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" /></label>
        <label className="field">密码<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" autoFocus /></label>
        {error && <div className="form-error">{error}</div>}
        <Button className="primary" busy={busy} type="submit">登录</Button>
        <p className="hint">账号密码由服务端环境变量 WEB_ADMIN_USER / WEB_ADMIN_PASSWORD 设置；未设置密码时，服务启动日志中会打印本次的临时密码。</p>
      </form>
    </main>
  )
}

// ───────────────────────── 解压对话框 ─────────────────────────

function UnzipDialog({ entry, parentCid, onClose, onSubmitted, toast }) {
  const [mode, setMode] = useState('each')
  const [password, setPassword] = useState('')
  const [deleteZip, setDeleteZip] = useState(false)
  const [skipPre, setSkipPre] = useState(false)
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    setBusy(true)
    try {
      await api('/api/jobs/unzip', {
        method: 'POST',
        body: { parent_cid: parentCid, file_id: entry.id, mode, password, delete_zip: deleteZip, skip_pre: skipPre },
      })
      toast(`已提交解压：${entry.name}`, 'ok')
      onSubmitted()
    } catch (err) { toast(err.message, 'bad') } finally { setBusy(false) }
  }
  return (
    <Modal title="云解压" onClose={onClose} footer={<><button className="btn ghost" onClick={onClose}>取消</button><Button className="primary" busy={busy} onClick={submit}>开始解压</Button></>}>
      <p className="file-title"><Archive size={16} />{entry.name}<span className="muted">{fmtSize(entry.size)}</span></p>
      <div className="mode-grid">
        <button className={`mode ${mode === 'each' ? 'selected' : ''}`} onClick={() => setMode('each')}><strong>解压到同名文件夹</strong><small>在当前目录新建「{entry.name.replace(/\.[^.]+$/, '')}」</small></button>
        <button className={`mode ${mode === 'direct' ? 'selected' : ''}`} onClick={() => setMode('direct')}><strong>解压到当前目录</strong><small>内容直接释放到当前目录</small></button>
      </div>
      <label className="field">解压密码<input value={password} onChange={e => setPassword(e.target.value)} placeholder="没有可留空；需要时任务会暂停等待输入" /></label>
      <label className="check"><input type="checkbox" checked={deleteZip} onChange={e => setDeleteZip(e.target.checked)} />解压结果体积不小于压缩包时，删除原压缩包</label>
      <label className="check"><input type="checkbox" checked={skipPre} onChange={e => setSkipPre(e.target.checked)} />跳过预解压（压缩包已预解压过时可加快速度）</label>
      <p className="hint">解压结果明显小于压缩包时会判定为不完整：自动删除解压结果并保留压缩包。</p>
    </Modal>
  )
}

// ───────────────────────── 任务面板 ─────────────────────────

function JobCard({ job, toast, onChanged }) {
  const [open, setOpen] = useState(ACTIVE.has(job.status))
  const [events, setEvents] = useState([])
  const [password, setPassword] = useState('')
  const active = ACTIVE.has(job.status)

  useEffect(() => {
    if (!open) return undefined
    let stopped = false
    const load = () => api(`/api/jobs/${job.id}`).then(d => { if (!stopped) setEvents(d.events) }).catch(() => {})
    load()
    const timer = active ? setInterval(load, 2000) : null
    return () => { stopped = true; if (timer) clearInterval(timer) }
  }, [open, active, job.id])

  const cancel = () => api(`/api/jobs/${job.id}/cancel`, { method: 'POST' }).then(onChanged).catch(e => toast(e.message, 'bad'))
  const sendPassword = async (e) => {
    e.preventDefault()
    try { await api(`/api/jobs/${job.id}/password`, { method: 'POST', body: { password } }); setPassword(''); onChanged() }
    catch (err) { toast(err.message, 'bad') }
  }

  return (
    <div className={`job ${statusTone(job.status)}`}>
      <button className="job-head" onClick={() => setOpen(o => !o)}>
        <span className="job-dot" />
        <span className="job-title" title={job.title}>{job.title || '云解压'}</span>
        <span className="job-status">{STATUS_TEXT[job.status] || job.status}</span>
      </button>
      {active && <div className="bar"><i style={{ width: `${Math.max(job.progress, 3)}%` }} /></div>}
      <p className="job-msg">{job.message}</p>
      {job.status === 'waiting_password' && (
        <form className="inline-form" onSubmit={sendPassword}>
          <input value={password} onChange={e => setPassword(e.target.value)} placeholder="输入解压密码" autoFocus />
          <button className="btn primary sm" type="submit" disabled={!password}>提交</button>
        </form>
      )}
      {open && (
        <div className="events">
          {events.map(ev => <div key={ev.id} className={`event ${ev.level}`}><time>{new Date(ev.at * 1000).toLocaleTimeString('zh-CN', { hour12: false })}</time><span>{ev.message}</span></div>)}
        </div>
      )}
      {active && <button className="btn ghost sm danger" onClick={cancel}>取消任务</button>}
    </div>
  )
}

// ───────────────────────── 主界面 ─────────────────────────

function Workspace({ me, onLogout, toast }) {
  const [trail, setTrail] = useState([{ cid: '0', name: '根目录' }])
  const cid = trail[trail.length - 1].cid
  const [items, setItems] = useState([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('')
  const [searchResult, setSearchResult] = useState(null)
  const [jobs, setJobs] = useState([])
  const [unzipTarget, setUnzipTarget] = useState(null)
  const [renameTarget, setRenameTarget] = useState(null)
  const [newFolder, setNewFolder] = useState(false)
  const [qps, setQps] = useState(null)
  const loadSeq = useRef(0)

  useEffect(() => { api('/api/settings/rate-limit').then(d => setQps(d.qps)).catch(() => {}) }, [])

  const loadDir = useCallback(async ({ append = false, refresh = false } = {}) => {
    const seq = ++loadSeq.current
    setLoading(true)
    try {
      const offset = append ? items.length : 0
      const data = await api(`/api/files?cid=${encodeURIComponent(cid)}&offset=${offset}&limit=200${refresh ? '&refresh=true' : ''}`)
      if (seq !== loadSeq.current) return
      setItems(list => (append ? [...list, ...data.items] : data.items))
      setHasMore(data.has_more)
    } catch (err) {
      if (err.status === 401) onLogout()
      else toast(err.message, 'bad')
    } finally {
      if (seq === loadSeq.current) setLoading(false)
    }
  }, [cid, items.length, onLogout, toast])

  // 进入目录时加载（loadDir 依赖 items.length，这里只跟随 cid 变化）
  useEffect(() => { setItems([]); setFilter(''); setSearchResult(null); loadDir() }, [cid]) // eslint-disable-line react-hooks/exhaustive-deps

  const loadJobs = useCallback(() => api('/api/jobs').then(d => setJobs(d.items)).catch(err => { if (err.status === 401) onLogout() }), [onLogout])
  const hasActive = jobs.some(j => ACTIVE.has(j.status))
  useEffect(() => { loadJobs() }, [loadJobs])
  useEffect(() => {
    if (!hasActive) return undefined
    const timer = setInterval(loadJobs, 2000)
    return () => clearInterval(timer)
  }, [hasActive, loadJobs])

  // 任务结束时刷新当前目录，解压结果立即可见
  const prevActive = useRef(0)
  useEffect(() => {
    const count = jobs.filter(j => ACTIVE.has(j.status)).length
    if (count < prevActive.current) loadDir({ refresh: true })
    prevActive.current = count
  }, [jobs]) // eslint-disable-line react-hooks/exhaustive-deps

  const enter = (entry) => setTrail(t => [...t, { cid: entry.id, name: entry.name }])
  const goTo = (index) => setTrail(t => t.slice(0, index + 1))

  const runSearch = async (e) => {
    e.preventDefault()
    if (!filter.trim()) return
    setLoading(true)
    try {
      const data = await api(`/api/files/search?q=${encodeURIComponent(filter.trim())}&cid=${encodeURIComponent(cid)}`)
      setSearchResult(data)
    } catch (err) { toast(err.message, 'bad') } finally { setLoading(false) }
  }

  const rows = useMemo(() => {
    if (searchResult) return searchResult.items
    const q = filter.trim().toLowerCase()
    return q ? items.filter(f => f.name.toLowerCase().includes(q)) : items
  }, [items, filter, searchResult])

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand"><span className="brand-logo">115</span><div><strong>115 Agent</strong><small>网盘操作控制台</small></div></div>
        <div className="spacer" />
        <span className={`pill ${me['115_cookie_configured'] ? 'ok' : 'bad'}`}><HardDrive size={14} />{me['115_cookie_configured'] ? '115 已连接' : '未配置 115 Cookie'}</span>
        {qps && <span className="pill muted-pill" title="所有 115 请求跨进程串行执行">限速 {qps} 次/秒</span>}
        <span className="user">{me.username}</span>
        <button className="icon-btn" title="退出登录" onClick={() => api('/api/auth/logout', { method: 'POST' }).finally(onLogout)}><LogOut size={18} /></button>
      </header>

      <div className="layout">
        <section className="card browser">
          <div className="browser-head">
            <nav className="crumbs">
              {trail.map((t, i) => (
                <span key={`${t.cid}-${i}`} className="crumb">
                  {i > 0 && <ChevronRight size={14} />}
                  <button disabled={i === trail.length - 1} onClick={() => goTo(i)}>{t.name}</button>
                </span>
              ))}
            </nav>
            <div className="spacer" />
            <button className="btn sm" disabled={trail.length < 2} onClick={() => goTo(trail.length - 2)}><ArrowUp size={14} />上一级</button>
            <button className="btn sm" onClick={() => setNewFolder(true)}><FolderPlus size={14} />新建文件夹</button>
            <button className="btn sm" onClick={() => loadDir({ refresh: true })} disabled={loading}><RefreshCw size={14} className={loading ? 'spin' : ''} />刷新</button>
          </div>

          <form className="toolbar" onSubmit={runSearch}>
            <div className="search"><Search size={15} /><input value={filter} onChange={e => { setFilter(e.target.value); setSearchResult(null) }} placeholder="筛选当前目录；按回车在此目录下全盘搜索" /></div>
            {searchResult && <span className="muted">搜索到 {searchResult.count} 条 <button type="button" className="link" onClick={() => { setSearchResult(null); setFilter('') }}>返回目录</button></span>}
            {!searchResult && <span className="muted">{rows.length} 项{hasMore ? '（未全部加载）' : ''}</span>}
          </form>

          <div className="table-wrap">
            <table>
              <thead><tr><th>名称</th><th className="num">大小</th><th>修改时间</th><th className="num">操作</th></tr></thead>
              <tbody>
                {rows.map(f => (
                  <tr key={f.id} className={f.is_dir ? 'clickable' : ''} onClick={() => f.is_dir && !searchResult && enter(f)}>
                    <td>
                      <span className="name">
                        {f.is_dir ? <Folder size={17} className="ic-dir" /> : f.is_archive ? <Archive size={17} className="ic-zip" /> : <File size={17} className="ic-file" />}
                        <span title={f.name}>{f.name}</span>
                      </span>
                    </td>
                    <td className="num muted">{f.is_dir ? '—' : fmtSize(f.size)}</td>
                    <td className="muted nowrap">{fmtTime(f.updated_at)}</td>
                    <td className="num" onClick={e => e.stopPropagation()}>
                      <div className="actions">
                        {f.is_archive && <button className="btn sm primary" onClick={() => setUnzipTarget({ entry: f, parentCid: searchResult ? f.cid : cid })}>解压</button>}
                        <button className="icon-btn" title="重命名" onClick={() => setRenameTarget(f)}><Pencil size={15} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!loading && rows.length === 0 && <div className="empty">{searchResult || filter ? '没有匹配的文件' : '空目录'}</div>}
            {loading && rows.length === 0 && <div className="empty"><Loader2 className="spin" size={18} /> 加载中…</div>}
            {hasMore && !searchResult && <div className="more"><Button busy={loading} onClick={() => loadDir({ append: true })}>加载更多</Button></div>}
          </div>
        </section>

        <aside className="card jobs">
          <div className="jobs-head"><h3>任务</h3>{hasActive && <span className="pill active-pill"><Loader2 size={13} className="spin" />运行中</span>}</div>
          {jobs.length === 0
            ? <div className="empty small">还没有任务。<br />点击压缩包右侧的「解压」开始。</div>
            : jobs.map(j => <JobCard key={j.id} job={j} toast={toast} onChanged={loadJobs} />)}
        </aside>
      </div>

      {unzipTarget && <UnzipDialog {...unzipTarget} toast={toast} onClose={() => setUnzipTarget(null)} onSubmitted={() => { setUnzipTarget(null); loadJobs() }} />}
      {renameTarget && <NameDialog title="重命名" initial={renameTarget.name} onClose={() => setRenameTarget(null)} onSubmit={async (name) => {
        await api('/api/files/rename', { method: 'POST', body: { id: renameTarget.id, name } })
        toast('已重命名', 'ok'); setRenameTarget(null); loadDir({ refresh: true })
      }} />}
      {newFolder && <NameDialog title="新建文件夹" initial="" onClose={() => setNewFolder(false)} onSubmit={async (name) => {
        await api('/api/files/folder', { method: 'POST', body: { name, parent_cid: cid } })
        toast(`已创建「${name}」`, 'ok'); setNewFolder(false); loadDir({ refresh: true })
      }} />}
    </div>
  )
}

function NameDialog({ title, initial, onClose, onSubmit }) {
  const [name, setName] = useState(initial)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e) => {
    e?.preventDefault()
    if (!name.trim()) { setError('名称不能为空'); return }
    setBusy(true); setError('')
    try { await onSubmit(name.trim()) } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return (
    <Modal title={title} onClose={onClose} footer={<><button className="btn ghost" onClick={onClose}>取消</button><Button className="primary" busy={busy} onClick={submit}>确定</Button></>}>
      <form onSubmit={submit}><label className="field">名称<input value={name} onChange={e => setName(e.target.value)} autoFocus /></label></form>
      {error && <div className="form-error">{error}</div>}
    </Modal>
  )
}

export default function App() {
  const [me, setMe] = useState(undefined)
  const [toast, toastView] = useToasts()
  const loadMe = useCallback(() => api('/api/auth/me').then(setMe).catch(() => setMe(null)), [])
  useEffect(() => { loadMe() }, [loadMe])
  const logout = useCallback(() => setMe(null), [])

  return (
    <>
      {me === undefined && <div className="boot"><Loader2 className="spin" /></div>}
      {me === null && <Login onLogin={loadMe} />}
      {me && <Workspace me={me} onLogout={logout} toast={toast} />}
      {toastView}
    </>
  )
}
