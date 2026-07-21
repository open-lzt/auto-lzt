import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "./AuthGate";
import * as flowClient from "../api/flowClient";

/** Every test states whether the SERVER enforces a key, because that is now the first thing the
 * gate asks and it decides everything after it. */
function serverRequiresKey(required: boolean) {
  return vi.spyOn(flowClient, "authRequired").mockResolvedValue({ required });
}

describe("AuthGate", () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders children with a warning when the server enforces no key", async () => {
    // The defect this guards: a stand with no key configured accepted ANY string and looked
    // protected. `require_api_key` is a no-op there, so the prompt was a painted lock.
    serverRequiresKey(false);
    const fetchFlowsMock = vi.spyOn(flowClient, "fetchFlows");

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    await waitFor(() => expect(screen.getByText("protected content")).toBeInTheDocument());
    expect(screen.queryByPlaceholderText("API-ключ")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/панель открыта всем/i);
    expect(fetchFlowsMock).not.toHaveBeenCalled();
  });

  it("renders children immediately when a key is already stored", async () => {
    serverRequiresKey(true);
    vi.spyOn(flowClient, "getApiKey").mockReturnValue("existing-key");
    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );
    await waitFor(() => expect(screen.getByText("protected content")).toBeInTheDocument());
  });

  it("prompts for a key when the server enforces one and none is stored", async () => {
    serverRequiresKey(true);
    vi.spyOn(flowClient, "getApiKey").mockReturnValue(null);
    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );
    expect(await screen.findByPlaceholderText("API-ключ")).toBeInTheDocument();
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
  });

  it("prompts rather than opens when the check itself fails", async () => {
    // Failing closed here costs a needless login screen; failing open would hide an
    // unprotected stand behind a guess.
    vi.spyOn(flowClient, "authRequired").mockRejectedValue(new Error("сеть недоступна"));
    vi.spyOn(flowClient, "getApiKey").mockReturnValue(null);
    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );
    expect(await screen.findByPlaceholderText("API-ключ")).toBeInTheDocument();
  });

  it("validates the entered key and renders children on success", async () => {
    serverRequiresKey(true);
    vi.spyOn(flowClient, "getApiKey").mockReturnValue(null);
    const setApiKeyMock = vi.spyOn(flowClient, "setApiKey").mockImplementation(() => {});
    vi.spyOn(flowClient, "fetchFlows").mockResolvedValue([]);

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    fireEvent.change(await screen.findByPlaceholderText("API-ключ"), {
      target: { value: "good-key" },
    });
    fireEvent.click(screen.getByText("Войти"));

    await waitFor(() => expect(screen.getByText("protected content")).toBeInTheDocument());
    expect(setApiKeyMock).toHaveBeenCalledWith("good-key");
  });

  it("shows an error and clears the key on validation failure, letting the user retry", async () => {
    serverRequiresKey(true);
    vi.spyOn(flowClient, "getApiKey").mockReturnValue(null);
    const setApiKeyMock = vi.spyOn(flowClient, "setApiKey").mockImplementation(() => {});
    vi.spyOn(flowClient, "fetchFlows").mockRejectedValue(new Error("неверный ключ"));

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    fireEvent.change(await screen.findByPlaceholderText("API-ключ"), {
      target: { value: "bad-key" },
    });
    fireEvent.click(screen.getByText("Войти"));

    expect(await screen.findByText("неверный ключ")).toBeInTheDocument();
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    expect(setApiKeyMock).toHaveBeenLastCalledWith("");
  });

  it("rejects an empty submission without calling the API", async () => {
    serverRequiresKey(true);
    vi.spyOn(flowClient, "getApiKey").mockReturnValue(null);
    const fetchFlowsMock = vi.spyOn(flowClient, "fetchFlows");

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    fireEvent.click(await screen.findByText("Войти"));
    expect(screen.getByText("введите API-ключ")).toBeInTheDocument();
    expect(fetchFlowsMock).not.toHaveBeenCalled();
  });
});
