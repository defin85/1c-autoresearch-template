import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import {
  App,
  Registry,
  RoutingPreviewSummary,
  ToolInventory,
  groupEvents,
} from "./App";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => [] }),
  );
});

test("shows repository-owned workspace entry", async () => {
  render(<App />);
  expect(
    await screen.findByText("Исследование конфигурации 1С"),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Репозиторий хранит состояние процесса/),
  ).toBeInTheDocument();
});

test("groups workflow events as run, job, step and attempt", () => {
  const grouped = groupEvents([
    {
      sequence: 1,
      timestamp: "2026-01-01T00:00:00Z",
      type: "run.created",
      run_id: "run-1",
      payload: {},
    },
    {
      sequence: 2,
      timestamp: "2026-01-01T00:00:01Z",
      type: "step.started",
      run_id: "run-1",
      job_id: "job-1",
      step_id: "step-1",
      attempt: 1,
      payload: {},
    },
  ]);
  expect(grouped["run-1"].run.run[0]).toHaveLength(1);
  expect(grouped["run-1"]["job-1"]["step-1"][1][0].sequence).toBe(2);
});

test("shows accessible source tool installations and current use", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          complete: true,
          tools: [
            {
              tool_id: "ibcmd",
              status: "ready",
              purpose: "exporter",
              instances: [
                {
                  version: "8.3.27.1989",
                  status: "ready",
                  path: "/opt/1cv8/ibcmd",
                },
              ],
            },
          ],
        }),
      }),
  );
  render(
    <ToolInventory
      project={{ id: "p", name: "p", root: "/repo" }}
      selectedProfile="ibcmd+form-aware/v1"
    />,
  );
  expect(await screen.findByText("проверка завершена")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Показать установки, версии и пути"));
  expect(
    screen.getByRole("table", {
      name: "Установки инструментов получения исходников",
    }),
  ).toBeInTheDocument();
  expect(screen.getByText("требуется текущим маршрутом")).toBeInTheDocument();
});

test("shows semantic and raw extension registries with an accessible empty state", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        diff_generation_id: "a".repeat(64),
        items: [],
        has_more: false,
      }),
    }),
  );
  render(<Registry project={{ id: "p", name: "p", root: "/repo" }} />);
  expect(
    await screen.findByText("В выбранном реестре нет записей."),
  ).toBeInTheDocument();
  fireEvent.mouseDown(screen.getByLabelText("Реестр"));
  expect(
    screen.getByRole("option", { name: "extension-diff" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("option", { name: "extension-dependencies" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("option", { name: "extension-physical-diff" }),
  ).toBeInTheDocument();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  await waitFor(() =>
    expect(
      screen.queryByRole("option", { name: "extension-diff" }),
    ).not.toBeInTheDocument(),
  );
});

test("pages registries with a generation guard and renders dependency and raw audit records", async () => {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url.includes("offset=100"))
      return Promise.resolve({
        ok: false,
        json: async () => ({ detail: "stale diff generation" }),
      });
    if (url.includes("extension-dependencies"))
      return Promise.resolve({
        ok: true,
        json: async () => ({
          diff_generation_id: "a".repeat(64),
          items: [{ dependency_id: "DEP-A", outcome: "unresolved" }],
          has_more: false,
        }),
      });
    if (url.includes("extension-physical-diff"))
      return Promise.resolve({
        ok: true,
        json: async () => ({
          diff_generation_id: "a".repeat(64),
          items: [{ stable_diff_id: "DIF-RAW", path: "extensions/x/raw.xml" }],
          has_more: false,
        }),
      });
    return Promise.resolve({
      ok: true,
      json: async () => ({
        diff_generation_id: "a".repeat(64),
        items: [{ stable_diff_id: "DIF-A" }],
        has_more: true,
      }),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  const { container } = render(<Registry project={{ id: "p", name: "p", root: "/repo" }} />);
  const registry = within(container);
  expect(await registry.findByText(/"stable_diff_id": "DIF-A"/)).toBeInTheDocument();
  fireEvent.click(registry.getByText("Далее"));
  expect(await registry.findByText("stale diff generation")).toBeInTheDocument();
  expect(fetchMock.mock.calls[1][0]).toContain(
    `expected_generation=${"a".repeat(64)}`,
  );

  fireEvent.mouseDown(registry.getByLabelText("Реестр"));
  fireEvent.click(screen.getByRole("option", { name: "extension-dependencies" }));
  expect(await registry.findByText(/"outcome": "unresolved"/)).toBeInTheDocument();
  fireEvent.mouseDown(registry.getByLabelText("Реестр"));
  fireEvent.click(screen.getByRole("option", { name: "extension-physical-diff" }));
  expect(await registry.findByText(/extensions\/x\/raw.xml/)).toBeInTheDocument();
});

test.each([
  [{ managed: 1, ordinary: 0, inconclusive: 0 }, "managed_only", []],
  [{ managed: 0, ordinary: 1, inconclusive: 0 }, "ordinary_form_present", []],
  [{ managed: 1, ordinary: 1, inconclusive: 0 }, "ordinary_form_present", []],
  [
    { managed: 0, ordinary: 0, inconclusive: 1 },
    "inconclusive_form_payload",
    ["next_vendor"],
  ],
])(
  "shows routing preview matrix %#",
  (form_counts, routing_reason, absent_roles) => {
    const { container } = render(
      <RoutingPreviewSummary
        preview={{
          preview_id: "p",
          status: "ready",
          routing_plan_fingerprint: "sha256:test",
          required_tools: ["ibcmd", "v8unpack"],
          routing_manifest: {
            groups: [
              {
                routing_group_id: "configuration",
                form_counts,
                routing_reason,
                exporter: "ibcmd",
                representation_schema: ordinaryOrUnknown(form_counts)
                  ? "v8unpack/v1"
                  : "xml-hierarchical/v1",
                absent_roles,
              },
            ],
          },
        }}
      />,
    );
    expect(container).toHaveTextContent(
      `Управляемые: ${form_counts.managed}; обычные: ${form_counts.ordinary}; неопределённые: ${form_counts.inconclusive}`,
    );
    expect(container).toHaveTextContent(routing_reason);
    if (absent_roles.length)
      expect(container).toHaveTextContent("Отсутствуют роли: next_vendor");
  },
);

function ordinaryOrUnknown(counts: { ordinary: number; inconclusive: number }) {
  return counts.ordinary > 0 || counts.inconclusive > 0;
}
