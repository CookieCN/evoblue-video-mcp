import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

describe("App", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
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
