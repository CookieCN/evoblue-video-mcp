import { useEffect, useRef, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";

const LOCAL_TOKEN_STORAGE_KEY = "evoblue.local_token";
const TOKEN_FRAGMENT_KEY = "evoblue_token";
const UNAUTHORIZED_EVENT = "evoblue:unauthorized";

function readLocalToken() {
  try {
    return localStorage.getItem(LOCAL_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function saveLocalToken(token) {
  try {
    localStorage.setItem(LOCAL_TOKEN_STORAGE_KEY, token);
  } catch {
    return;
  }
}

function clearLocalToken() {
  try {
    localStorage.removeItem(LOCAL_TOKEN_STORAGE_KEY);
  } catch {
    return;
  }
}

// P7 token bootstrap (INSTALLER_RELEASE_CONTRACT section 4): the engine opens
// the UI at /#evoblue_token=<token>; the fragment never reaches the server or
// its access log. Read it once, store it, then strip it from the address bar.
function consumeTokenFragment() {
  try {
    const match = window.location.hash.match(
      new RegExp("[#&]" + TOKEN_FRAGMENT_KEY + "=([^&]+)"),
    );
    if (match) {
      saveLocalToken(decodeURIComponent(match[1]));
      history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  } catch {
    return;
  }
}

async function apiFetch(input, init = {}) {
  const token = readLocalToken();
  const headers = new Headers(init.headers || undefined);
  if (token) headers.set("X-Local-Token", token);
  const r = await fetch(input, { ...init, headers });
  if (r.status === 401) {
    clearLocalToken();
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
    throw new Error("unauthorized");
  }
  return r;
}

function Home() {
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState(null);
  const [settings, setSettings] = useState(null);

  useEffect(() => {
    apiFetch("/api/jobs")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => setJobs(data.items ?? []))
      .catch((e) => setError(e.message));
    // P8-002: surface why queued jobs are not starting (setup incomplete)
    apiFetch("/api/settings")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setSettings)
      .catch(() => {});
  }, []);

  const setupBlocked =
    jobs.some((job) => job.status === "queued") &&
    settings != null &&
    settings.setup_completed === false;

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
            to="/mcp"
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            客户端
          </Link>
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
        {setupBlocked && (
          <p className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
            有任务在排队但不会开始：请先完成
            <Link to="/settings" className="underline">
              初始设置
            </Link>
            。
          </p>
        )}
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

const LLM_PRESETS = {
  deepseek: { base_url: "https://api.deepseek.com", model: "deepseek-chat" },
  openai: { base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
};

function Settings() {
  const [settings, setSettings] = useState(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);
  const [form, setForm] = useState({
    llm_provider: "deepseek",
    llm_base_url: "https://api.deepseek.com",
    llm_model: "deepseek-chat",
    llm_api_key: "",
    report_directory: "",
  });
  const formLoaded = useRef(false);

  useEffect(() => {
    apiFetch("/api/settings")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        setSettings(data);
        // prefill the draft once with whatever is already configured
        if (!formLoaded.current) {
          formLoaded.current = true;
          setForm((f) => ({
            ...f,
            llm_provider: data.llm_provider || f.llm_provider,
            llm_base_url: data.llm_base_url || f.llm_base_url,
            llm_model: data.llm_model || f.llm_model,
            report_directory: data.report_directory || "",
          }));
        }
      })
      .catch(() => {});
  }, []);

  // P1 review round 2: completing setup requires a runnable LLM configuration
  // — the worker refuses to claim jobs without these. The API Key is required
  // unless the provider is UNCHANGED and the store already holds its key
  // (switching providers points the credential ref at an empty slot).
  const keyProvided = form.llm_api_key.trim().length > 0;
  const keyAlreadyOk =
    settings != null &&
    settings.llm_api_key_configured === true &&
    typeof settings.llm_provider === "string" &&
    settings.llm_provider.trim().toLowerCase() ===
      form.llm_provider.trim().toLowerCase();
  const setupReady =
    form.llm_provider.trim() &&
    form.llm_base_url.trim() &&
    form.llm_model.trim() &&
    (keyProvided || keyAlreadyOk);

  function pickProvider(provider) {
    const preset = LLM_PRESETS[provider];
    setForm((f) => ({
      ...f,
      llm_provider: provider,
      llm_base_url: preset ? preset.base_url : f.llm_base_url,
      llm_model: preset ? preset.model : f.llm_model,
    }));
  }

  async function saveSetup() {
    setSaving(true);
    setMessage(null);
    try {
      const body = {
        llm_provider: form.llm_provider.trim(),
        llm_base_url: form.llm_base_url.trim(),
        llm_model: form.llm_model.trim(),
        setup_completed: true,
      };
      const apiKey = form.llm_api_key.trim();
      if (apiKey) body.llm_api_key = apiKey;
      if (form.report_directory.trim()) {
        body.report_directory = form.report_directory.trim();
      }
      const r = await apiFetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setSettings(data);
      setForm((f) => ({ ...f, llm_api_key: "" }));
      setMessage("设置已保存，排队任务将自动开始");
    } catch (e) {
      setMessage(`保存失败：${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  // P8-003: one-click redacted diagnostics export for bug reports
  async function exportDiagnostics() {
    setMessage(null);
    try {
      const r = await apiFetch("/api/diagnostics/export");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `evoblue-diagnostics-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setMessage("诊断信息已导出（已脱敏，可附在反馈里）");
    } catch (e) {
      setMessage(`导出失败：${e.message}`);
    }
  }

  async function saveAsrSettings() {
    setSaving(true);
    setMessage(null);
    try {
      const r = await apiFetch("/api/settings", {
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
        <h2 className="text-lg font-medium text-slate-900">初始设置</h2>
        <p className="mt-2 text-slate-600">
          {settings?.setup_completed
            ? "已完成配置，可在此修改。"
            : "尚未完成配置：填写 LLM 信息后任务才会开始。"}
        </p>

        {settings?.setup_completed && (
          <dl className="mt-4 space-y-2 text-sm text-slate-600">
            <div className="flex gap-2">
              <dt className="w-28 shrink-0 text-slate-500">报告目录</dt>
              <dd>{settings.report_directory ?? "未设置（使用默认数据目录）"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-28 shrink-0 text-slate-500">LLM Provider</dt>
              <dd>{settings.llm_provider ?? "未设置"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-28 shrink-0 text-slate-500">LLM 模型</dt>
              <dd>{settings.llm_model ?? "未设置"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-28 shrink-0 text-slate-500">API Key</dt>
              <dd>{settings.llm_api_key_configured ? "已配置" : "未配置"}</dd>
            </div>
          </dl>
        )}

        <div className="mt-6 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <label className="block text-sm text-slate-700">
              Provider
              <select
                value={form.llm_provider}
                onChange={(e) => pickProvider(e.target.value)}
                className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"
              >
                <option value="deepseek">DeepSeek</option>
                <option value="openai">OpenAI</option>
                <option value="openai-compatible">OpenAI 兼容（自定义）</option>
              </select>
            </label>
            <label className="block text-sm text-slate-700">
              模型
              <input
                value={form.llm_model}
                onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
                className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"
                placeholder="deepseek-chat"
              />
            </label>
          </div>
          <label className="block text-sm text-slate-700">
            Base URL
            <input
              value={form.llm_base_url}
              onChange={(e) => setForm({ ...form, llm_base_url: e.target.value })}
              className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"
              placeholder="https://api.deepseek.com"
            />
          </label>
          <label className="block text-sm text-slate-700">
            API Key{" "}
            {keyAlreadyOk ? (
              <span className="text-xs text-emerald-600">已配置（留空保持不变）</span>
            ) : (
              <span className="text-xs text-amber-600">必填</span>
            )}
            <input
              type="password"
              value={form.llm_api_key}
              onChange={(e) => setForm({ ...form, llm_api_key: e.target.value })}
              className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"
              placeholder={
                keyAlreadyOk ? "留空保持不变" : "粘贴 API Key（必填，存入系统凭据库）"
              }
            />
          </label>
          <label className="block text-sm text-slate-700">
            报告目录（可选，留空使用默认数据目录）
            <input
              value={form.report_directory}
              onChange={(e) => setForm({ ...form, report_directory: e.target.value })}
              className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"
              placeholder="默认：数据目录下 reports"
            />
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={saveSetup}
              disabled={saving || !setupReady}
              className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-800 disabled:opacity-50"
            >
              {saving ? "保存中…" : "保存并完成设置"}
            </button>
            <button
              type="button"
              onClick={exportDiagnostics}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              导出诊断信息
            </button>
            {message && <span className="text-sm text-slate-600">{message}</span>}
          </div>
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
                <option value="sherpa-onnx-qwen3">Qwen3-ASR 0.6B（可选，约 1 GB）</option>
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
        const r = await apiFetch("/api/models");
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
    if (m.tier === "qwen3") return "Qwen3-ASR 0.6B INT8 · 多语言增强";
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
      const r = await apiFetch(`/api/models/${m.model_id}/install`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    } catch (e) {
      setError(`安装失败：${e.message}`);
    }
  }

  async function cancel(m) {
    setMessage(null);
    try {
      const r = await apiFetch(`/api/models/${m.model_id}/cancel`, { method: "POST" });
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
      const r = await apiFetch(`/api/models/${m.model_id}`, { method: "DELETE" });
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
                  <dd>{m.tier === "qwen3" ? "30 种语言 + 22 种中文方言" : (m.languages ?? []).join(" / ")}</dd>
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
                  未通过正式默认门禁（基准实体召回不足，精确制品许可证未确认）：可安装和明确选择，但当前不作为自动推荐。
                </p>
              )}
              {m.tier === "qwen3" && !m.formal_default && (
                <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                  国内从 ModelScope 下载。模型约占 941 MB，覆盖更多语言和中文方言；尚未通过 EvoBlue 基准，不作为自动下载或正式默认。
                </p>
              )}
              {m.formal_default && (
                <p className="mt-3 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">
                  已通过基准与许可门禁，作为正式默认推荐。
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

const HANDSHAKE_FALLBACK_LABELS = { verified: "已验证", unverified: "未验证", failed: "验证失败" };

function Clients() {
  const [clients, setClients] = useState([]);
  const [labels, setLabels] = useState(HANDSHAKE_FALLBACK_LABELS);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null); // client_id of the running operation
  const [busyLabel, setBusyLabel] = useState("");
  const [message, setMessage] = useState(null);
  const [copyables, setCopyables] = useState({}); // client_id -> CopyableConfig

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const r = await apiFetch("/api/mcp-clients");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        if (!cancelled) {
          setClients(data.clients ?? []);
          setLabels({ ...HANDSHAKE_FALLBACK_LABELS, ...(data.display_labels ?? {}) });
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  async function load() {
    try {
      const r = await apiFetch("/api/mcp-clients");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setClients(data.clients ?? []);
      setLabels({ ...HANDSHAKE_FALLBACK_LABELS, ...(data.display_labels ?? {}) });
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }

  async function post(client, path, body, label) {
    setBusy(client.client_id);
    setBusyLabel(label);
    setMessage(null);
    try {
      const r = await apiFetch(`/api/mcp-clients/${client.client_id}/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body ?? {}),
      });
      const data = await r.json();
      if (!r.ok) {
        setMessage(`${data?.error?.message ?? `HTTP ${r.status}`}（${data?.error?.code ?? ""}）`);
        return null;
      }
      setMessage(data.message ?? "");
      return data;
    } catch (e) {
      setMessage(`操作失败：${e.message}`);
      return null;
    } finally {
      setBusy(null);
      setBusyLabel("");
      load();
    }
  }

  async function install(c) {
    await post(c, "install", {}, "正在握手…");
  }

  async function verify(c) {
    await post(c, "verify", {}, "正在握手…");
  }

  async function remove(c) {
    if (!window.confirm(`移除后可用备份恢复 ${c.display_name} 的配置。继续？`)) return;
    await post(c, "remove", { confirm: true }, "正在移除…");
  }

  async function restore(c) {
    let backups = [];
    try {
      const r = await apiFetch(`/api/mcp-clients/${c.client_id}/backups`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      backups = (await r.json()).items ?? [];
    } catch (e) {
      setMessage(`备份读取失败：${e.message}`);
      return;
    }
    if (backups.length === 0) {
      setMessage("没有可用备份。");
      return;
    }
    const newest = backups[0];
    const diffNote = newest.matches_current === false ? "当前配置与该备份不同，恢复将覆盖其后的修改。" : "";
    if (!window.confirm(`将 ${c.display_name} 恢复到备份 ${newest.name}。${diffNote}恢复前会自动再做安全备份。继续？`)) {
      return;
    }
    await post(c, "restore", { backup_name: newest.name, confirm: true }, "正在恢复…");
  }

  async function showCopyable(c) {
    if (copyables[c.client_id]) {
      setCopyables((prev) => {
        const next = { ...prev };
        delete next[c.client_id];
        return next;
      });
      return;
    }
    try {
      const r = await apiFetch(`/api/mcp-clients/${c.client_id}/config`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setCopyables((prev) => ({ ...prev, [c.client_id]: data }));
    } catch (e) {
      setMessage(`配置生成失败：${e.message}`);
    }
  }

  async function copyText(c) {
    const copyable = copyables[c.client_id];
    if (!copyable) return;
    try {
      await navigator.clipboard.writeText(copyable.config_text);
      setMessage("配置已复制到剪贴板。");
    } catch {
      setMessage("复制失败，请手动选择文本复制。");
    }
  }

  function flag(c) {
    if (c.tier === "manual") return "手动档";
    if (c.installed === true) return "已配置";
    if (c.installed === false) return "未配置";
    return "未知";
  }

  function engineText(c) {
    if (c.engine_online === true) return "Engine 在线";
    if (c.engine_online === false) return "Engine 离线";
    return "Engine 状态未知";
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-950">MCP 客户端设置</h1>
        <Link to="/" className="text-sm text-cyan-700 hover:underline">
          返回
        </Link>
      </header>

      <p className="mt-3 text-sm text-slate-600">
        一键把 EvoBlue 的 MCP 工具接入支持的客户端：结构化合并、修改前备份、写入后真实握手验证。
        无法确认时一律显示「未验证」，不会虚报成功。
      </p>

      {error && <p className="mt-4 rounded-xl bg-red-50 p-4 text-red-700">{error}</p>}
      {message && <p className="mt-4 rounded-xl bg-emerald-50 p-4 text-emerald-700">{message}</p>}

      <section aria-label="客户端列表" className="mt-6 space-y-4">
        {clients.map((c) => {
          const manual = c.tier === "manual";
          const isBusy = busy === c.client_id;
          return (
            <div key={c.client_id} className="rounded-2xl border border-slate-200 bg-white p-5">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-lg font-medium text-slate-900">{c.display_name}</h2>
                  <p className="mt-1 font-mono text-xs text-slate-500">{c.client_id}</p>
                </div>
                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
                  {flag(c)}
                </span>
              </div>

              <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm text-slate-600">
                <div className="flex gap-2">
                  <dt className="w-20 shrink-0 text-slate-500">真实握手</dt>
                  <dd>
                    {labels[c.handshake] ?? c.handshake}
                    {c.handshake_reason ? `（${c.handshake_reason}）` : ""}
                  </dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-20 shrink-0 text-slate-500">引擎状态</dt>
                  <dd>{engineText(c)}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-20 shrink-0 text-slate-500">其他条目</dt>
                  <dd>{c.other_server_count == null ? "—" : `${c.other_server_count} 个（已保留）`}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-20 shrink-0 text-slate-500">备份</dt>
                  <dd>{c.backup_count} 份</dd>
                </div>
              </dl>

              {(c.notes ?? []).map((note) => (
                <p key={note} className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                  {note}
                </p>
              ))}

              <div className="mt-5 flex flex-wrap items-center gap-3">
                {!manual && (
                  <button
                    type="button"
                    disabled={busy != null}
                    onClick={() => install(c)}
                    className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-800 disabled:opacity-50"
                  >
                    {isBusy && busyLabel === "正在握手…" ? "正在握手…" : "安装配置"}
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy != null}
                  onClick={() => verify(c)}
                  className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  {isBusy && busyLabel === "正在握手…" && !manual ? "测试连接…" : "测试连接"}
                </button>
                {!manual && (
                  <button
                    type="button"
                    disabled={busy != null}
                    onClick={() => remove(c)}
                    className="rounded-lg border border-red-300 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
                  >
                    移除配置
                  </button>
                )}
                {c.tier === "file_auto" && (
                  <button
                    type="button"
                    disabled={busy != null || c.backup_count === 0}
                    onClick={() => restore(c)}
                    className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    恢复备份
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => showCopyable(c)}
                  className="text-sm text-cyan-700 hover:underline"
                >
                  {copyables[c.client_id] ? "收起可复制配置" : "查看可复制配置"}
                </button>
              </div>

              {copyables[c.client_id] && (
                <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
                  {(copyables[c.client_id].steps ?? []).map((step) => (
                    <p key={step} className="text-sm text-slate-600">
                      {step}
                    </p>
                  ))}
                  <pre className="mt-3 overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">
                    {copyables[c.client_id].config_text}
                  </pre>
                  <button
                    type="button"
                    onClick={() => copyText(c)}
                    className="mt-3 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  >
                    复制配置
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </section>
    </main>
  );
}


function TokenGate({ onSubmit }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState(null);

  async function submit(e) {
    e.preventDefault();
    const token = value.trim();
    if (!token) return;
    try {
      const r = await fetch("/api/jobs", { headers: { "X-Local-Token": token } });
      if (r.status === 401) {
        setError("令牌不正确，请重新粘贴。");
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      onSubmit(token);
    } catch {
      setError("验证失败，请确认 EvoBlue Engine 正在运行。");
    }
  }

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6">
      <h1 className="text-2xl font-semibold text-slate-950">请粘贴本机访问令牌</h1>
      <p className="mt-2 text-sm text-slate-600">
        本机访问令牌在数据目录的 local_token 文件中（数据目录可在下方「打开数据目录」指引中找到）。
      </p>
      <form onSubmit={submit} className="mt-4 flex items-start gap-3">
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="block w-full rounded-lg border border-slate-300 px-3 py-2 font-mono"
          placeholder="粘贴 local_token 文件内容"
        />
        <button
          type="submit"
          className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-800"
        >
          验证并进入
        </button>
      </form>
      {error && <p className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    </section>
  );
}

export function App() {
  const [needsToken, setNeedsToken] = useState(false);
  const [authEpoch, setAuthEpoch] = useState(0);
  consumeTokenFragment();

  useEffect(() => {
    const onUnauthorized = () => setNeedsToken(true);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  if (needsToken) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-16">
        <TokenGate
          onSubmit={(token) => {
            saveLocalToken(token);
            setNeedsToken(false);
            setAuthEpoch((n) => n + 1);
          }}
        />
      </main>
    );
  }

  return (
    <Routes key={authEpoch}>
      <Route path="/" element={<Home />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="/models" element={<Models />} />
      <Route path="/mcp" element={<Clients />} />
      <Route path="*" element={<Home />} />
    </Routes>
  );
}
