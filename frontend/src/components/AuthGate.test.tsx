import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "./AuthGate";
import * as flowClient from "../api/flowClient";

describe("AuthGate", () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders children immediately when a key is already stored", () => {
    vi.spyOn(flowClient, "getApiKey").mockReturnValue("existing-key");
    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );
    expect(screen.getByText("protected content")).toBeInTheDocument();
  });

  it("prompts for a key when none is stored", () => {
    vi.spyOn(flowClient, "getApiKey").mockReturnValue(null);
    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("API-ключ")).toBeInTheDocument();
  });

  it("validates the entered key and renders children on success", async () => {
    vi.spyOn(flowClient, "getApiKey").mockReturnValue(null);
    const setApiKeyMock = vi.spyOn(flowClient, "setApiKey").mockImplementation(() => {});
    vi.spyOn(flowClient, "fetchFlows").mockResolvedValue([]);

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    fireEvent.change(screen.getByPlaceholderText("API-ключ"), { target: { value: "good-key" } });
    fireEvent.click(screen.getByText("Войти"));

    await waitFor(() => expect(screen.getByText("protected content")).toBeInTheDocument());
    expect(setApiKeyMock).toHaveBeenCalledWith("good-key");
  });

  it("shows an error and clears the key on validation failure, letting the user retry", async () => {
    vi.spyOn(flowClient, "getApiKey").mockReturnValue(null);
    const setApiKeyMock = vi.spyOn(flowClient, "setApiKey").mockImplementation(() => {});
    vi.spyOn(flowClient, "fetchFlows").mockRejectedValue(new Error("неверный ключ"));

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    fireEvent.change(screen.getByPlaceholderText("API-ключ"), { target: { value: "bad-key" } });
    fireEvent.click(screen.getByText("Войти"));

    expect(await screen.findByText("неверный ключ")).toBeInTheDocument();
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    expect(setApiKeyMock).toHaveBeenLastCalledWith("");
  });

  it("rejects an empty submission without calling the API", () => {
    vi.spyOn(flowClient, "getApiKey").mockReturnValue(null);
    const fetchFlowsMock = vi.spyOn(flowClient, "fetchFlows");

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    fireEvent.click(screen.getByText("Войти"));
    expect(screen.getByText("введите API-ключ")).toBeInTheDocument();
    expect(fetchFlowsMock).not.toHaveBeenCalled();
  });
});
