import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "./App.jsx";

describe("App", () => {
  it("explains that the P0 UI is not operational yet", () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "EvoBlue Video MCP" })).toBeInTheDocument();
    expect(screen.getByText(/尚未配置/)).toBeInTheDocument();
  });
});

