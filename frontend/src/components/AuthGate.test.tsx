import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "./AuthGate";
import * as flowClient from "../api/flowClient";
import * as httpClient from "../api/httpClient";

/** Every test states the SERVER's posture, because that is the first thing the gate asks and it
 * decides everything after it.
 *
 * Both flags, never one: `required: false` alone is ambiguous — it is the answer both for "the dev
 * hatch is on, anyone is admitted" and for "no key is configured and the server 401s everything".
 * Reading it as the first when it meant the second is the bug these tests now pin. */
function serverPosture(required: boolean, open: boolean) {
  return vi.spyOn(flowClient, "authRequired").mockResolvedValue({ required, open });
}

describe("AuthGate", () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("says the server refuses everything when there is no key AND no hatch", async () => {
    // The defect this guards: this posture — the stock self-host default — was read as "open".
    // The panel rendered a full dashboard whose every call 401s, under a banner announcing it was
    // open to anyone: simultaneously unusable and lying about its own exposure.
    serverPosture(false, false);

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(/отклоняет все запросы/i);
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Ключ доступа")).not.toBeInTheDocument();
  });

  it("renders children with a warning when the server admits everyone", async () => {
    // The hatch is on: `require_api_key` really is a no-op, so a prompt would be a painted lock —
    // any string typed into it would "work".
    serverPosture(false, true);
    const fetchFlowsMock = vi.spyOn(flowClient, "fetchFlows");

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    await waitFor(() => expect(screen.getByText("protected content")).toBeInTheDocument());
    expect(screen.queryByLabelText("Ключ доступа")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/панель открыта всем/i);
    expect(fetchFlowsMock).not.toHaveBeenCalled();
  });

  it("renders children immediately when a key is already stored", async () => {
    serverPosture(true, false);
    vi.spyOn(httpClient, "getApiKey").mockReturnValue("existing-key");
    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );
    await waitFor(() => expect(screen.getByText("protected content")).toBeInTheDocument());
  });

  it("prompts for a key when the server enforces one and none is stored", async () => {
    serverPosture(true, false);
    vi.spyOn(httpClient, "getApiKey").mockReturnValue(null);
    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );
    expect(await screen.findByLabelText("Ключ доступа")).toBeInTheDocument();
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
  });

  it("prompts rather than opens when the check itself fails", async () => {
    // Failing closed here costs a needless login screen; failing open would hide an
    // unprotected stand behind a guess.
    vi.spyOn(flowClient, "authRequired").mockRejectedValue(new Error("сеть недоступна"));
    vi.spyOn(httpClient, "getApiKey").mockReturnValue(null);
    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );
    expect(await screen.findByLabelText("Ключ доступа")).toBeInTheDocument();
  });

  it("validates the entered key and renders children on success", async () => {
    serverPosture(true, false);
    vi.spyOn(httpClient, "getApiKey").mockReturnValue(null);
    const setApiKeyMock = vi.spyOn(httpClient, "setApiKey").mockImplementation(() => {});
    vi.spyOn(flowClient, "fetchFlows").mockResolvedValue([]);

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    fireEvent.change(await screen.findByLabelText("Ключ доступа"), {
      target: { value: "good-key" },
    });
    fireEvent.click(screen.getByText("Войти"));

    await waitFor(() => expect(screen.getByText("protected content")).toBeInTheDocument());
    expect(setApiKeyMock).toHaveBeenCalledWith("good-key");
  });

  it("shows an error and clears the key on validation failure, letting the user retry", async () => {
    serverPosture(true, false);
    vi.spyOn(httpClient, "getApiKey").mockReturnValue(null);
    const setApiKeyMock = vi.spyOn(httpClient, "setApiKey").mockImplementation(() => {});
    vi.spyOn(flowClient, "fetchFlows").mockRejectedValue(new Error("неверный ключ"));

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    fireEvent.change(await screen.findByLabelText("Ключ доступа"), {
      target: { value: "bad-key" },
    });
    fireEvent.click(screen.getByText("Войти"));

    expect(await screen.findByText("неверный ключ")).toBeInTheDocument();
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    expect(setApiKeyMock).toHaveBeenLastCalledWith("");
  });

  it("rejects an empty submission without calling the API", async () => {
    serverPosture(true, false);
    vi.spyOn(httpClient, "getApiKey").mockReturnValue(null);
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

  it("draws a skeleton while the posture check is in flight", () => {
    // This state used to render null: the first paint was blank, then a card appeared out of
    // nowhere and moved everything the eye had just settled on.
    vi.spyOn(flowClient, "authRequired").mockReturnValue(new Promise(() => {}));
    vi.spyOn(httpClient, "getApiKey").mockReturnValue(null);

    const { container } = render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
  });

  it("reveals the key on demand so a truncated paste can be spotted", async () => {
    serverPosture(true, false);
    vi.spyOn(httpClient, "getApiKey").mockReturnValue(null);

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    const field = await screen.findByLabelText("Ключ доступа");
    expect(field).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Показать ключ" }));
    expect(field).toHaveAttribute("type", "text");

    fireEvent.click(screen.getByRole("button", { name: "Скрыть ключ" }));
    expect(field).toHaveAttribute("type", "password");
  });
});
