import { Route, Routes } from "react-router-dom";

function Home() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <p className="text-sm font-semibold tracking-wide text-cyan-700">LOCAL CONTROL CENTER</p>
      <h1 className="mt-3 text-4xl font-semibold tracking-tight text-slate-950">
        EvoBlue Video MCP
      </h1>
      <p className="mt-5 max-w-2xl text-lg leading-8 text-slate-600">
        P0 架构骨架已就绪。首次设置、视频分析和历史报告将在后续阶段接入真实 Local
        Engine 状态。
      </p>
      <section aria-label="系统状态" className="mt-10 rounded-2xl border border-slate-200 bg-white p-6">
        <h2 className="text-lg font-medium text-slate-900">系统状态</h2>
        <p className="mt-2 text-slate-600">尚未配置。当前页面不启动任务，也不会发送数据。</p>
      </section>
    </main>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="*" element={<Home />} />
    </Routes>
  );
}

