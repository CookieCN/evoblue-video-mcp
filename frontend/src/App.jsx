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
        <Link
          to="/settings"
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          设置
        </Link>
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
      </section>
    </main>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="*" element={<Home />} />
    </Routes>
  );
}
