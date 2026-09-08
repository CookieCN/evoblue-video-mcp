import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App.jsx";

function mockFetch(response) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: response.ok,
      status: response.status,
      json: async () => response.data,
    }),
  );
}

function mockFetchRouter(routes) {
  const calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((input, init) => {
      const url = String(input);
      calls.push({ url, init: init ?? {} });
      const key = Object.keys(routes)
        .filter((k) => k !== "*" && url.includes(k))
        .sort((a, b) => url.indexOf(b) - url.indexOf(a))[0];
      const response = key ? routes[key] : routes["*"];
      return Promise.resolve({
        ok: response.ok ?? true,
        status: response.status ?? 200,
        json: async () => response.data,
      });
    }),
  );
  return calls;
}

const CLIENT_LIST = {
  clients: [
    {
      client_id: "codex",
      display_name: "Codex",
      tier: "file_auto",
      installed: true,
      entry_matches_current: true,
      config_supported: true,
      handshake: "unverified",
      handshake_reason: null,
      engine_online: false,
      other_server_count: 2,
      backup_count: 1,
      notes: [],
    },
    {
      client_id: "claude_desktop",
      display_name: "Claude Desktop",
      tier: "file_auto",
      installed: false,
      handshake: "unverified",
      engine_online: false,
      other_server_count: 0,
      backup_count: 0,
      notes: [],
    },
    {
      client_id: "workbuddy",
      display_name: "WorkBuddy",
      tier: "file_auto",
      installed: true,
      handshake: "failed",
      handshake_reason: "timeout",
      engine_online: true,
      other_server_count: 3,
      backup_count: 0,
      notes: ["安装后需在 WorkBuddy 连接器管理页手动 Trust 才激活"],
    },
    {
      client_id: "claude_code",
      display_name: "Claude Code",
      tier: "cli",
      installed: null,
      handshake: "verified",
      handshake_reason: null,
      engine_online: true,
      other_server_count: null,
      backup_count: 0,
      notes: [],
    },
    {
      client_id: "deepseek",
      display_name: "DeepSeek Harness",
      tier: "manual",
      installed: null,
      handshake: "unverified",
      engine_online: true,
      other_server_count: null,
      backup_count: 0,
      notes: ["格式仍在变化，只生成可复制配置"],
    },
  ],
  display_labels: { verified: "已验证", unverified: "未验证", failed: "验证失败" },
};

function renderClients() {
  render(
    <MemoryRouter initialEntries={["/mcp"]}>
      <App />
    </MemoryRouter>,
  );
}

function cardOf(displayName) {
  const heading = screen.getByRole("heading", { name: displayName });
  return heading.closest("div.rounded-2xl");
}

describe("App", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows the empty history state and settings entry on the home page", async () => {
    mockFetch({ ok: true, status: 200, data: { items: [], total: 0, limit: 20, offset: 0 } });

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "EvoBlue Video MCP" })).toBeInTheDocument();
    expect(await screen.findByText(/还没有分析记录/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "设置" })).toBeInTheDocument();
  });

  it("lists jobs when the engine returns them", async () => {
    mockFetch({
      ok: true,
      status: 200,
      data: {
        items: [{ job_id: "job-1", status: "queued" }],
        total: 1,
        limit: 20,
        offset: 0,
      },
    });

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("job-1")).toBeInTheDocument();
    expect(screen.getByText("queued")).toBeInTheDocument();
  });

  it("warns when jobs are queued but first setup is incomplete", async () => {
    mockFetchRouter({
      "/api/jobs": { data: { items: [{ job_id: "job-9", status: "queued" }] } },
      "/api/settings": { data: { setup_completed: false } },
    });

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/任务在排队但不会开始/)).toBeInTheDocument();
  });

  it("does not warn when setup is already completed", async () => {
    mockFetchRouter({
      "/api/jobs": { data: { items: [{ job_id: "job-9", status: "queued" }] } },
      "/api/settings": { data: { setup_completed: true } },
    });

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    await screen.findByText("job-9");
    expect(screen.queryByText(/任务在排队但不会开始/)).not.toBeInTheDocument();
  });

  it("completing setup submits the full runnable LLM configuration", async () => {
    const calls = mockFetchRouter({
      "/api/settings": { data: { setup_completed: false } },
    });

    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/尚未完成配置/)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/粘贴 API Key/), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存并完成设置" }));

    await waitFor(() => {
      const put = calls.find(
        (c) => c.url.includes("/api/settings") && c.init.method === "PUT",
      );
      expect(put).toBeDefined();
      const body = JSON.parse(put.init.body);
      // the worker gate needs all four: provider/base URL/model/key
      expect(body.llm_provider).toBe("deepseek");
      expect(body.llm_base_url).toBe("https://api.deepseek.com");
      expect(body.llm_model).toBe("deepseek-chat");
      expect(body.llm_api_key).toBe("sk-test");
      expect(body.setup_completed).toBe(true);
    });
  });

  it("refuses to complete setup on a fresh install until the API key is typed", async () => {
    mockFetchRouter({ "/api/settings": { data: { setup_completed: false } } });

    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByText(/尚未完成配置/);

    const save = screen.getByRole("button", { name: "保存并完成设置" });
    // the provider/base/model defaults are prefilled — the KEY is the gate
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText(/粘贴 API Key/), {
      target: { value: "sk-test" },
    });
    expect(save).not.toBeDisabled();
    // clearing any other required field disables again
    fireEvent.change(screen.getByPlaceholderText("deepseek-chat"), {
      target: { value: "" },
    });
    expect(save).toBeDisabled();
  });

  it("allows leaving the key empty only when the provider is unchanged and configured", async () => {
    mockFetchRouter({
      "/api/settings": {
        data: {
          setup_completed: true,
          llm_provider: "deepseek",
          llm_base_url: "https://api.deepseek.com",
          llm_model: "deepseek-chat",
          llm_api_key_configured: true,
        },
      },
    });

    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByText(/已完成配置/);

    const save = screen.getByRole("button", { name: "保存并完成设置" });
    expect(save).not.toBeDisabled();

    // switching provider invalidates the stored key -> key required again
    // (two "Provider" labels exist: the setup form and the ASR routing block;
    // the form comes first in the DOM)
    const providerSelect = screen.getAllByLabelText("Provider")[0];
    fireEvent.change(providerSelect, { target: { value: "openai" } });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText(/必填，存入系统凭据库/), {
      target: { value: "sk-new" },
    });
    expect(save).not.toBeDisabled();
  });

  it("shows an error message when fetching jobs fails", async () => {
    mockFetch({ ok: false, status: 500, data: {} });

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/HTTP 500/)).toBeInTheDocument();
  });

  it("renders the first-setup page", async () => {
    mockFetch({ ok: true, status: 200, data: { setup_completed: false } });

    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "首次设置" })).toBeInTheDocument();
    expect(await screen.findByText(/尚未完成配置/)).toBeInTheDocument();
  });
});

describe("MCP clients page", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders every client card with the four honest flags", async () => {
    mockFetchRouter({ "/api/mcp-clients": { data: CLIENT_LIST } });
    renderClients();

    expect(await screen.findByText("Codex")).toBeInTheDocument();
    expect(screen.getByText("DeepSeek Harness")).toBeInTheDocument();
    // unverified renders the exact frozen label, plus the offline engine as text
    expect(screen.getAllByText("未验证").length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText("Engine 离线").length).toBe(2);
    expect(screen.getAllByText("Engine 在线").length).toBe(3);
    // WorkBuddy's manual Trust note is visible
    expect(screen.getByText(/手动 Trust 才激活/)).toBeInTheDocument();
  });

  it("offers install for auto tiers only and keeps manual copyable-only", async () => {
    mockFetchRouter({ "/api/mcp-clients": { data: CLIENT_LIST } });
    renderClients();

    await screen.findByRole("heading", { name: "Codex" });
    expect(screen.getAllByRole("button", { name: "安装配置" })).toHaveLength(4);
    const deepseek = cardOf("DeepSeek Harness");
    expect(within(deepseek).queryByRole("button", { name: "安装配置" })).toBeNull();
    expect(within(deepseek).getByText(/只生成可复制配置/)).toBeInTheDocument();
  });

  it("install posts to the client endpoint and reflects the verified verdict", async () => {
    const calls = mockFetchRouter({
      "/api/mcp-clients": { data: CLIENT_LIST },
      "/codex/install": {
        data: {
          client_id: "codex",
          operation: "install",
          performed: true,
          installed: true,
          handshake: "verified",
          handshake_reason: null,
          message: "已安装并通过真实握手验证。",
        },
      },
    });
    renderClients();

    await screen.findByRole("heading", { name: "Codex" });
    const codex = cardOf("Codex");
    fireEvent.click(within(codex).getByRole("button", { name: "安装配置" }));

    await screen.findByText("已安装并通过真实握手验证。");
    await waitFor(() => {
      const installCall = calls.find((c) => c.url.includes("/codex/install"));
      expect(installCall).toBeDefined();
      expect(installCall.init.method).toBe("POST");
    });
  });

  it("remove asks for confirmation and sends confirm:true", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const calls = mockFetchRouter({
      "/api/mcp-clients": { data: CLIENT_LIST },
      "/codex/remove": {
        data: { performed: true, message: "已移除配置（可从备份恢复）。" },
      },
    });
    renderClients();

    await screen.findByRole("heading", { name: "Codex" });
    fireEvent.click(within(cardOf("Codex")).getByRole("button", { name: "移除配置" }));

    await screen.findByText("已移除配置（可从备份恢复）。");
    expect(confirm).toHaveBeenCalledOnce();
    const removeCall = calls.find((c) => c.url.includes("/codex/remove"));
    expect(JSON.parse(removeCall.init.body)).toEqual({ confirm: true });
  });

  it("restore posts the newest backup after explicit confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const calls = mockFetchRouter({
      "/api/mcp-clients": { data: CLIENT_LIST },
      "/codex/backups": {
        data: {
          items: [
            { name: "config.toml.evoblue-backup-20260907T120001Z-ab12", matches_current: false },
            { name: "config.toml.evoblue-backup-20260907T110000Z-cd34", matches_current: false },
          ],
        },
      },
      "/codex/restore": {
        data: { performed: true, restored_from: "config.toml.evoblue-backup-20260907T120001Z-ab12" },
      },
    });
    renderClients();

    await screen.findByRole("heading", { name: "Codex" });
    fireEvent.click(within(cardOf("Codex")).getByRole("button", { name: "恢复备份" }));

    await waitFor(() => {
      const restoreCall = calls.find((c) => c.url.includes("/codex/restore"));
      expect(restoreCall).toBeDefined();
      expect(JSON.parse(restoreCall.init.body)).toEqual({
        backup_name: "config.toml.evoblue-backup-20260907T120001Z-ab12",
        confirm: true,
      });
    });
    // the covers-my-edits warning was shown before confirming
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("覆盖其后的修改"));
  });

  it("shows the failed handshake reason instead of pretending success", async () => {
    mockFetchRouter({
      "/api/mcp-clients": { data: CLIENT_LIST },
      "/codex/verify": {
        data: {
          performed: false,
          handshake: "failed",
          handshake_reason: "name_mismatch",
          message: "真实握手失败（name_mismatch）。",
        },
      },
    });
    renderClients();

    await screen.findByRole("heading", { name: "Codex" });
    fireEvent.click(within(cardOf("Codex")).getByRole("button", { name: "测试连接" }));

    await screen.findByText("真实握手失败（name_mismatch）。");
    const workbuddy = cardOf("WorkBuddy");
    expect(within(workbuddy).getByText(/验证失败（timeout）/)).toBeInTheDocument();
  });
});

describe("token bootstrap (P7)", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    window.location.hash = "";
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  function jsonResponse(status, data) {
    return { ok: status < 400, status, json: async () => data };
  }

  function headersOf(init) {
    const h = init?.headers;
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    return Object.fromEntries(Object.entries(h ?? {}).map(([k, v]) => [k.toLowerCase(), v]));
  }

  it("stores the token from the URL fragment, strips it, and sends the header", async () => {
    const calls = mockFetchRouter({ "/api/jobs": { data: { items: [] } } });
    window.location.hash = "#evoblue_token=frag-token";

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "EvoBlue Video MCP" });
    await waitFor(() => {
      expect(localStorage.getItem("evoblue.local_token")).toBe("frag-token");
    });
    expect(window.location.hash).toBe("");
    const jobsCalls = calls.filter((c) => c.url.includes("/api/jobs"));
    expect(jobsCalls.length).toBeGreaterThan(0);
    expect(headersOf(jobsCalls[0].init)["x-local-token"]).toBe("frag-token");
  });

  it("renders the paste gate on 401 and enters after a valid token", async () => {
    mockFetchRouter({
      "/api/jobs": { data: { items: [{ job_id: "job-1", status: "queued" }] } },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input, init) => {
        const url = String(input);
        const headers = headersOf(init);
        if (headers["x-local-token"] === "good-token") {
          return Promise.resolve(
            jsonResponse(200, { items: [{ job_id: "job-1", status: "running" }] }),
          );
        }
        if (url.includes("/api/")) {
          return Promise.resolve(jsonResponse(401, {}));
        }
        return Promise.resolve(jsonResponse(200, {}));
      }),
    );

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("请粘贴本机访问令牌")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("粘贴 local_token 文件内容"), {
      target: { value: "good-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "验证并进入" }));

    expect(await screen.findByRole("heading", { name: "EvoBlue Video MCP" })).toBeInTheDocument();
    expect(localStorage.getItem("evoblue.local_token")).toBe("good-token");
    expect(await screen.findByText("job-1")).toBeInTheDocument();
  });
});
