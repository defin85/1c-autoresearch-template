import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  App,
  AgentProfiles,
  Registry,
  RoutingPreviewSummary,
  ToolInventory,
  WorkflowEditor,
  groupEvents,
} from "./App";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => [] }),
  );
});
afterEach(cleanup);

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

test("agent profile explains the read-only environment and persists it", async () => {
  const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) =>
    Promise.resolve({
      ok: true,
      json: async () => init?.method === "PUT" ? {} : ({ items: {} }),
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<AgentProfiles project={{ id: "p", name: "p", root: "/repo" }} />);
  expect(await screen.findByText(/local-read-only запрещает запись/)).toBeInTheDocument();
  expect(screen.getByText(/allowed_paths ограничивают контекст инструкции/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Проверить и сохранить профиль" }));
  await waitFor(() => {
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({
      profile: { environment_preset: "local-read-only" },
    });
  });
});

test("workflow preview is invalidated by edits and apply uses the reviewed parameters", async () => {
  const phase = {
    phase_id: "analyze-dif",
    mode: "parallel-pool",
    max_concurrency: 2,
    roles: [{
      role_id: "analyzer",
      agent_profile: "local",
      count: 2,
      instruction_supplement: "",
    }],
  };
  const configuration = {
    manifest_fingerprint: "sha256:manifest",
    jobs: [],
    steps: [{
      job_id: "discover-mrq",
      step: {
        id: "step-1",
        operation: "mrq.discover-next",
        timeout_seconds: 1800,
        agent_phases: [phase],
      },
      catalog: { executor: "agent", effect: "user-state", approval_required: false },
    }],
  };
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/workflow/configuration"))
      return Promise.resolve({ ok: true, json: async () => configuration });
    if (url.endsWith("/agent-profiles"))
      return Promise.resolve({ ok: true, json: async () => ({
        items: { local: { model: "gpt-5.6-sol", reasoning_effort: "low", instructions_version: "1", environment_preset: "local-read-only" } },
      }) });
    if (url.endsWith("/workflow/patch-preview")) {
      const body = JSON.parse(String(init?.body));
      return Promise.resolve({
        ok: true,
        json: async () => ({
          after: { ...configuration.steps[0].step, ...body.parameters },
          agent_phase_preview: [{
            phase_id: "analyze-dif",
            mode: "parallel-pool",
            effective_max_concurrency: 2,
            maximum_calls_in_current_window: 32,
            roles: [{
              role_id: "analyzer",
              agent_profile: "local",
              profile: { model: "gpt-5.6-sol", reasoning_effort: "low", instructions_version: "1", environment_preset: "local-read-only" },
            }],
            sandbox: "read-only",
            allowed_paths: "subject paths select context",
          }],
        }),
      });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <WorkflowEditor
      project={{ id: "p", name: "p", root: "/repo" }}
      snapshot={{ workflow_fingerprint: "sha256:workflow" } as never}
      refresh={() => {}}
    />,
  );
  await screen.findByLabelText("Режим analyze-dif");
  expect(screen.getByText("Анализ DIF")).toBeInTheDocument();
  expect(screen.getByLabelText("Количество агентов analyze-dif analyzer")).toHaveValue(2);
  expect(screen.queryByText(/Логические слоты/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Профиль analyze-dif analyzer")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Предварительный просмотр" }));
  expect(await screen.findByLabelText("Просмотр политики фаз")).toHaveTextContent("максимум вызовов текущего окна 32");
  expect(screen.getByLabelText("Просмотр политики фаз")).toHaveTextContent("эффективный предел 2");
  expect(screen.getByLabelText("Просмотр политики фаз")).toHaveTextContent("Песочница: read-only");
  const apply = screen.getByRole("button", { name: "Применить просмотренное изменение" });
  expect(apply).toBeEnabled();
  fireEvent.click(screen.getByText("Дополнительные параметры"));
  fireEvent.change(screen.getByLabelText("Предельное время, секунд"), { target: { value: "1900" } });
  expect(apply).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Предварительный просмотр" }));
  await waitFor(() => expect(apply).toBeEnabled());
  fireEvent.click(apply);
  await waitFor(() => {
    const action = fetchMock.mock.calls.find(([url]) => url.endsWith("/actions"));
    expect(JSON.parse(String(action?.[1]?.body)).payload.parameters.timeout_seconds).toBe(1900);
  });
});

test("workflow editor shows a server topology error", async () => {
  const configuration = {
    manifest_fingerprint: "sha256:manifest",
    jobs: [],
    steps: [{
      job_id: "discover-mrq",
      step: {
        id: "step-1",
        operation: "mrq.discover-next",
        timeout_seconds: 1800,
        agent_phases: [{
          phase_id: "analyze-dif",
          mode: "parallel-pool",
          max_concurrency: 2,
          roles: [{
            role_id: "analyzer",
            agent_profile: "local",
            count: 2,
            instruction_supplement: "",
          }],
        }],
      },
      catalog: { executor: "agent", effect: "user-state", approval_required: false },
    }],
  };
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    if (url.endsWith("/workflow/configuration"))
      return Promise.resolve({ ok: true, json: async () => configuration });
    if (url.endsWith("/agent-profiles"))
      return Promise.resolve({ ok: true, json: async () => ({ items: {
        local: { model: "gpt-5.6-sol", reasoning_effort: "low", instructions_version: "1", environment_preset: "local-read-only" },
      } }) });
    if (url.endsWith("/workflow/patch-preview"))
      return Promise.resolve({ ok: false, json: async () => ({ detail: "Недопустимая топология: требуется ровно один analyzer" }) });
    return Promise.resolve({ ok: true, json: async () => ({}) });
  }));
  render(
    <WorkflowEditor
      project={{ id: "p", name: "p", root: "/repo" }}
      snapshot={{ workflow_fingerprint: "sha256:workflow" } as never}
      refresh={() => {}}
    />,
  );
  await screen.findByLabelText("Режим analyze-dif");
  fireEvent.click(screen.getByRole("button", { name: "Предварительный просмотр" }));
  expect(await screen.findByText(/Недопустимая топология/)).toBeInTheDocument();
});

test("workflow editor opens the requested step", async () => {
  const configuration = {
    manifest_fingerprint: "sha256:manifest",
    jobs: [],
    steps: [
      { job_id: "configure", step: { id: "validate-project", operation: "project.validate", timeout_seconds: 1800 }, catalog: { executor: "local", effect: "read", approval_required: false } },
      { job_id: "classify-mrq", step: { id: "classify-mrq", operation: "mrq.classify-batches", timeout_seconds: 1800 }, catalog: { executor: "agent", effect: "user-state", approval_required: false } },
    ],
  };
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    if (url.endsWith("/workflow/configuration"))
      return Promise.resolve({ ok: true, json: async () => configuration });
    if (url.endsWith("/agent-profiles"))
      return Promise.resolve({ ok: true, json: async () => ({ items: {} }) });
    return Promise.resolve({ ok: true, json: async () => ({}) });
  }));
  render(
    <WorkflowEditor
      project={{ id: "p", name: "p", root: "/repo" }}
      snapshot={{ workflow_fingerprint: "sha256:workflow" } as never}
      refresh={() => {}}
      initialStepId="classify-mrq"
    />,
  );
  expect(await screen.findByRole("heading", { name: "Формирование пакетов" })).toBeInTheDocument();
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
