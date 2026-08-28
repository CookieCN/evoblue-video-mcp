import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";

function Home() {
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("/api/jobs")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => setJobs(data.items ?? []))
      .catch((e) => setError(e.message));
  }, []);

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <header className="flex items-start justify-between">
        <div>
          <p className="text-sm font-semibold tracking-wide text-cyan-700">LOCAL CONTROL CENTER</p>
          <h1 className="mt-3 text-4xl font-semibold tracking-tight text-slate-950">
            EvoBlue Video MCP
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/models"
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            模型
          </Link>
          <Link
            to="/settings"
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            设置
          </Link>
        </div>
      </header>

      <section aria-label="分析记录" className="mt-10">
        <h2 className="text-lg font-medium text-slate-900">分析记录</h2>
        {error ? (
          <p className="mt-4 rounded-xl bg-red-50 p-4 text-red-700">{error}</p>
        ) : jobs.length === 0 ? (
          <p className="mt-4 rounded-xl border border-dashed border-slate-300 p-8 text-center text-slate-500">
            还没有分析记录。完成首次设置后即可提交视频链接。
          </p>
        ) : (
          <ul className="mt-4 divide-y divide-slate-200 rounded-xl border border-slate-200 bg-white">
            {jobs.map((job) => (
              <li key={job.job_id} className="flex items-center justify-between px-4 py-3">
                <span className="font-mono text-sm text-slate-700">{job.job_id}</span>
                <span className="text-sm text-slate-500">{job.status}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function Settings() {
  const [settings, setSettings] = useState(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    fetch("/api/settings")
      .then((r) => r.json())
      .then(setSettings)
      .catch(() => {});
  }, []);

  async function completeSetup() {
    setSaving(true);
    setMessage(null);
    try {
      const r = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ setup_completed: true }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setSettings(data);
      setMessage("设置已保存");
    } catch (e) {
      setMessage(`保存失败：${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  async function saveAsrSettings() {
    setSaving(true);
    setMessage(null);
    try {
      const r = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          asr_provider: settings?.asr_provider ?? "auto",
          whisper_cpp_executable: settings?.whisper_cpp_executable || null,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setSettings(await r.json());
      setMessage("ASR 路由设置已保存");
    } catch (e) {
      setMessage(`保存失败：${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-950">首次设置</h1>
        <Link to="/" className="text-sm text-cyan-700 hover:underline">
          返回
        </Link>
      </header>

      <section className="mt-8 rounded-2xl border border-slate-200 bg-white p-6">
        <h2 className="text-lg font-medium text-slate-900">系统状态</h2>
        <p className="mt-2 text-slate-600">
          {settings?.setup_completed
            ? "已完成配置。"
            : "尚未完成配置。完整设置向导将在后续阶段接入。"}
        </p>

        {settings && (
          <dl className="mt-4 space-y-2 text-sm text-slate-600">
            <div className="flex gap-2">
              <dt className="w-28 shrink-0 text-slate-500">报告目录</dt>
              <dd>{settings.report_directory ?? "未设置"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-28 shrink-0 text-slate-500">LLM Provider</dt>
              <dd>{settings.llm_provider ?? "未设置"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-28 shrink-0 text-slate-500">LLM 模型</dt>
              <dd>{settings.llm_model ?? "未设置"}</dd>
            </div>
          </dl>
        )}

        <div className="mt-6 flex items-center gap-3">
          <button
            type="button"
            onClick={completeSetup}
            disabled={saving || settings?.setup_completed}
            className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-800 disabled:opacity-50"
          >
            {saving ? "保存中…" : "标记为已配置"}
          </button>
          {message && <span className="text-sm text-slate-600">{message}</span>}
        </div>

        {settings && (
          <div className="mt-8 border-t border-slate-200 pt-6">
            <h2 className="text-lg font-medium text-slate-900">ASR 路由</h2>
            <p className="mt-2 text-sm text-slate-600">
              自动模式按语言和已安装模型选择；固定 Provider 会覆盖自动路由，但不会隐式下载模型。
            </p>
            <label className="mt-4 block text-sm text-slate-700">
              Provider
              <select
                value={settings.asr_provider ?? "auto"}
                onChange={(e) => setSettings({ ...settings, asr_provider: e.target.value })}
                className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"
              >
                <option value="auto">自动</option>
                <option value="sherpa-onnx-lite">Lite（明确选择，非正式默认推荐）</option>
                <option value="sherpa-onnx-standard">Standard</option>
                <option value="whisper-cpp-base">Whisper.cpp Base</option>
              </select>
            </label>
            <label className="mt-4 block text-sm text-slate-700">
              whisper-cli 可执行文件路径（仅 Whisper 回退需要）
              <input
                value={settings.whisper_cpp_executable ?? ""}
                onChange={(e) =>
                  setSettings({ ...settings, whisper_cpp_executable: e.target.value })
                }
                className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"
                placeholder="C:\\path\\to\\whisper-cli.exe"
              />
            </label>
            <button
              type="button"
              onClick={saveAsrSettings}
              disabled={saving}
              className="mt-4 rounded-lg bg-cyan-700 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-800 disabled:opacity-50"
            >
              保存 ASR 设置
            </button>
          </div>
        )}
      </section>
    </main>
  );
}

function fmtBytes(n) {
  if (n == null) return "—";
  const mb = n / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(1)} MB`;
}

function Models() {
  const [models, setModels] = useState([]);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const r = await fetch("/api/models");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        if (!cancelled) {
          setModels(data.items ?? []);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
    }
    load();
    const id = setInterval(load, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  function tierLabel(m) {
    if (m.tier === "lite") return "Lite · 中文快速体验";
    if (m.tier === "multilingual") return "Whisper.cpp Base · 多语言回退";
    return "Standard · 中英日韩粤";
  }

  function stateOf(m) {
    if (["pending", "downloading", "verifying", "installing"].includes(m.status)) {
      return "downloading";
    }
    if (m.status === "failed") return "failed";
    if (m.status === "cancelled") return "cancelled";
    if (m.installed) return "installed";
    return "not_installed";
  }

  function progressOf(m) {
    if (!m.compressed_size_bytes) return 0;
    return Math.min(100, Math.round((m.downloaded_bytes / m.compressed_size_bytes) * 100));
  }

  async function install(m) {
    const note =
      `即将下载 ${fmtBytes(m.compressed_size_bytes)}，安装后约占用 ${fmtBytes(m.installed_size_bytes)}。\n\n继续？`;
    if (!window.confirm(note)) return;
    setMessage(null);
    try {
      const r = await fetch(`/api/models/${m.model_id}/install`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    } catch (e) {
      setError(`安装失败：${e.message}`);
    }
  }

  async function cancel(m) {
    setMessage(null);
    try {
      const r = await fetch(`/api/models/${m.model_id}/cancel`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    } catch (e) {
      setError(`取消失败：${e.message}`);
    }
  }

  async function uninstall(m) {
    if (!window.confirm(`卸载将释放约 ${fmtBytes(m.installed_size_bytes)} 磁盘空间。\n\n继续？`)) {
      return;
    }
    setMessage(null);
    try {
      const r = await fetch(`/api/models/${m.model_id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      if (data.pending_reclaim_bytes > 0) {
        setMessage(`模型已卸载，约 ${fmtBytes(data.pending_reclaim_bytes)} 空间等待系统清理`);
      } else {
        setMessage(`已卸载，释放 ${fmtBytes(data.reclaimed_bytes)}`);
      }
    } catch (e) {
      setError(`卸载失败：${e.message}`);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-950">模型管理</h1>
        <Link to="/" className="text-sm text-cyan-700 hover:underline">
          返回
        </Link>
      </header>

      <p className="mt-3 text-sm text-slate-600">
        模型按需下载，安装前会先展示体积。下载支持断点续传、取消与卸载。
      </p>

      {error && <p className="mt-4 rounded-xl bg-red-50 p-4 text-red-700">{error}</p>}
      {message && <p className="mt-4 rounded-xl bg-emerald-50 p-4 text-emerald-700">{message}</p>}

      <section aria-label="模型列表" className="mt-6 space-y-4">
        {models.map((m) => {
          const state = stateOf(m);
          return (
            <div key={m.model_id} className="rounded-2xl border border-slate-200 bg-white p-5">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-lg font-medium text-slate-900">{tierLabel(m)}</h2>
                  <p className="mt-1 font-mono text-xs text-slate-500">
                    {m.model_id} · {m.version}
                  </p>
                </div>
                <span
                  className={`rounded-full px-3 py-1 text-xs font-medium ${
                    state === "installed"
                      ? "bg-emerald-100 text-emerald-700"
                      : state === "downloading"
                        ? "bg-cyan-100 text-cyan-700"
                        : state === "failed"
                          ? "bg-red-100 text-red-700"
                          : "bg-slate-100 text-slate-600"
                  }`}
                >
                  {state === "installed"
                    ? m.active
                      ? "已激活"
                      : "已安装"
                    : state === "downloading"
                      ? `下载中 ${progressOf(m)}%`
                      : state === "failed"
                        ? "失败"
                        : state === "cancelled"
                          ? "已取消"
                          : "未安装"}
                </span>
              </div>

              <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm text-slate-600">
                <div className="flex gap-2">
                  <dt className="w-24 shrink-0 text-slate-500">语言</dt>
                  <dd>{(m.languages ?? []).join(" / ")}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-24 shrink-0 text-slate-500">下载体积</dt>
                  <dd>{fmtBytes(m.compressed_size_bytes)}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-24 shrink-0 text-slate-500">安装体积</dt>
                  <dd>{fmtBytes(m.installed_size_bytes)}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-24 shrink-0 text-slate-500">许可证</dt>
                  <dd>{m.license}</dd>
                </div>
                <div className="col-span-2 flex gap-2">
                  <dt className="w-24 shrink-0 text-slate-500">再分发</dt>
                  <dd>{m.redistribution === "upstream_only" ? "仅上游原始源" : m.redistribution}</dd>
                </div>
              </dl>

              {state === "downloading" && (
                <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-cyan-600 transition-all"
                    style={{ width: `${progressOf(m)}%` }}
                  />
                </div>
              )}
              {state === "failed" && m.error_code && (
                <p className="mt-3 text-sm text-red-600">错误码：{m.error_code}</p>
              )}
              {m.tier === "lite" && !m.formal_default && (
                <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                  精确制品许可证尚未确认：可安装和明确选择，但当前不作为正式默认推荐。
                </p>
              )}

              <div className="mt-5 flex items-center gap-3">
                {state === "downloading" ? (
                  <button
                    type="button"
                    onClick={() => cancel(m)}
                    className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  >
                    取消
                  </button>
                ) : state === "installed" ? (
                  <button
                    type="button"
                    onClick={() => uninstall(m)}
                    className="rounded-lg border border-red-300 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50"
                  >
                    卸载
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => install(m)}
                    className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-800"
                  >
                    安装
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </section>
    </main>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="/models" element={<Models />} />
      <Route path="*" element={<Home />} />
    </Routes>
  );
}
