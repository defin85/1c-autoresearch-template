import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  App,
  AgentProfiles,
  Events,
  Indexes,
  Registry,
  RoutingPreviewSummary,
  Sources,
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

test("journal target initializes the exact run group and invocation filter", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      events: [{ sequence: 1, timestamp: "2026-01-01T00:00:00Z", type: "invocation.started", run_id: "run-1", payload: { invocation_id: "invoke-1" } }],
      next_cursor: 1,
      resync_required: false,
    }),
  }));
  render(<Events project={{ id: "p", name: "p", root: "/repo" }} target={{ runId: "run-1", invocationId: "invoke-1" }} />);
  expect(await screen.findByRole("button", { name: /Запуск run-1/ })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("textbox", { name: "Поиск в событиях и журналах" })).toHaveValue("invoke-1");
});

test("journal shows the failure reason on the collapsed run card", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      events: [{
        sequence: 1,
        timestamp: "2026-01-01T00:00:00Z",
        type: "step.finished",
        run_id: "run-1",
        payload: { status: "failed", exit: { blocker: { code: "dispatcher.graph.failed", message: "stale DIF classification bindings", action: "analyze-dif" } } },
      }],
      next_cursor: 1,
      resync_required: false,
    }),
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<Events project={{ id: "p", name: "p", root: "/repo" }} />);
  expect(await screen.findByText("dispatcher.graph.failed: stale DIF classification bindings")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Запуск run-1/ })).toHaveAttribute("aria-expanded", "false");
  expect(String(fetchMock.mock.calls[0][0])).toContain("cursor=0&limit=500&tail=true");
});

test("registry target requests only the exact stable item", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [], has_more: false }) });
  vi.stubGlobal("fetch", fetchMock);
  render(<Registry project={{ id: "p", name: "p", root: "/repo" }} target={{ registry: "diff-inventory", itemId: "DIF-EXACT" }} />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(String(fetchMock.mock.calls[0][0])).toContain("item_id=DIF-EXACT");
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

test("shows a running routing preview before route groups are available", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ complete: true, tools: [] }),
    }),
  );
  render(
    <>
      <RoutingPreviewSummary
        preview={{ preview_id: "preview", status: "running", routing_plan_fingerprint: "", routing_manifest: {}, progress: { phase: "probe", completed: 1, total: 3, subject: "configuration:target_cf" } }}
      />
      <ToolInventory
        project={{ id: "p", name: "p", root: "/repo" }}
        selectedProfile="ibcmd+form-aware/v1"
        routingPreview={{ preview_id: "preview", status: "running", routing_plan_fingerprint: "", routing_manifest: {} }}
      />
    </>,
  );
  expect(screen.getByText(/Состояние: running/)).toBeInTheDocument();
  expect(screen.getByText("Проверено компонентов: 1 из 3. Текущий: configuration:target_cf")).toBeInTheDocument();
  expect(await screen.findByText("проверка завершена")).toBeInTheDocument();
});

test("saved infobase connection parameters are visible and editable without exposing passwords", async () => {
  const setup = {
    profiles: ["ibcmd+form-aware/v1"],
    infobases: {
      acquisition_profile: "ibcmd+form-aware/v1",
      roles: {
        vendor_baseline: {
          connection_profile: "local-baseline",
          configuration_name: "СППР",
          root_uuid: "00000000-0000-0000-0000-000000000001",
          version: "2.0.14.9",
        },
      },
    },
    infobases_fingerprint: "sha256:manifest",
    extension_scope: {
      extensions: [{
        uuid: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        decision: "",
        rationale: "",
        dormant: false,
        roles: {
          vendor_baseline: { present: true, name: "Service", version: "1", active: false },
          target_cf: { present: false, name: "", version: "", active: false },
          next_vendor: { present: false, name: "", version: "", active: false },
        },
      }],
      included: [],
      excluded: [],
      dormant: [],
      unreviewed: ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"],
    },
    external_artifacts: { artifacts: [] },
    upload_draft_fingerprint: "sha256:draft",
    connection_profiles: {
      "local-baseline": {
        available: true,
        tested: true,
        profile_id: "ibcmd+form-aware/v1",
        platform_path: "/opt/1cv8/x86_64/8.3.27.1989",
        server: "localhost",
        reference: "example",
        dbms: "PostgreSQL",
        db_server: "localhost port=5432",
        db_name: "example",
        db_user: "postgres",
        infobase_user: "chatgpt",
        client_connection: "/Slocalhost/example",
        db_password_set: true,
        infobase_password_set: true,
        extension_count: 0,
        extensions: [],
        tool_versions: {},
      },
    },
    active_source: {},
    active_diff: {},
  };
  let finishReview!: (value: { ok: boolean; json: () => Promise<Record<string, unknown>> }) => void;
  const reviewRequest = new Promise<{ ok: boolean; json: () => Promise<Record<string, unknown>> }>(
    (resolve) => { finishReview = resolve; },
  );
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string, init?: RequestInit) =>
    url.endsWith("/actions") && init?.method === "POST"
      ? reviewRequest
      : Promise.resolve({
      ok: true,
      json: async () => url.endsWith("/source-setup")
        ? setup
        : ({ complete: true, tools: [] }),
    }),
  ));
  const scrollIntoView = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: scrollIntoView,
  });
  render(
    <Sources
      project={{ id: "p", name: "p", root: "/repo" }}
      snapshot={{ workflow_fingerprint: "sha256:workflow" } as never}
      refreshWorkflow={() => {}}
      initialExtensionUuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: /Подключения к базам/ }));
  expect(await screen.findByText("/Slocalhost/example")).toBeInTheDocument();
  expect(screen.getByText("localhost port=5432 / example / postgres")).toBeInTheDocument();
  expect(screen.getByText("PostgreSQL: сохранён; 1С: сохранён")).toBeInTheDocument();
  expect(screen.queryByText("private-db")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Изменить параметры" }));
  expect(screen.getByLabelText("Имя базы 1С")).toHaveValue("example");
  expect(screen.getByLabelText("Пароль PostgreSQL")).toHaveValue("");
  expect(screen.getAllByText(/Пароль сохранён/)).toHaveLength(2);
  const extensionSection = screen.getByRole("button", { name: /Расширения конфигурации/ });
  if (extensionSection.getAttribute("aria-expanded") !== "true") fireEvent.click(extensionSection);
  expect(screen.getByText("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")).toBeInTheDocument();
  await waitFor(() => {
    expect(document.activeElement).toHaveAttribute(
      "id",
      "extension-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    );
    expect(scrollIntoView).toHaveBeenCalled();
  });
  fireEvent.change(screen.getByRole("combobox", { name: "Решение для расширения aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" }), {
    target: { value: "exclude" },
  });
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Решение для расширения aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" })).toHaveValue("exclude"));
  const rationale = await screen.findByLabelText("Обоснование исключения для расширения aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa");
  expect(rationale).toBeRequired();
  fireEvent.change(rationale, { target: { value: "Техническое" } });
  expect(rationale).toHaveValue("Техническое");
  fireEvent.click(screen.getByRole("button", { name: "Просмотреть выбор" }));
  expect(await screen.findByText("Проверяется выбор…")).toBeInTheDocument();
  finishReview({
    ok: true,
    json: async () => ({ operation: "sources.configure", preview: true, comparison_epoch_changed: true }),
  });
  expect(await screen.findByText("Будет начата новая эпоха сравнения.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Применить выбор" })).toBeEnabled();
});

test("agent profile explains the read-only environment and persists it", async () => {
  const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) =>
    Promise.resolve({
      ok: true,
      json: async () => init?.method === "PUT"
        ? {}
        : _url.endsWith("/agent-capabilities")
          ? { models: [{ id: "gpt-5.6-sol", name: "GPT-5.6-Sol", default_reasoning_effort: "low", reasoning_efforts: ["low", "max", "ultra"], input_context_tokens: 272000, context_estimator_version: "utf8-v1", capability_fingerprint: "sha256:test" }] }
          : { items: {} },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<AgentProfiles project={{ id: "p", name: "p", root: "/repo" }} />);
  expect(await screen.findByText(/local-read-only запрещает запись/)).toBeInTheDocument();
  expect(screen.getByText(/allowed_paths ограничивают контекст инструкции/)).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Модель" })).toHaveTextContent("GPT-5.6-Sol");
  fireEvent.click(screen.getByRole("button", { name: "Проверить и сохранить профиль" }));
  await waitFor(() => {
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({
      profile: { environment_preset: "local-read-only" },
    });
  });
});

test("agent profile persists the complete bounded source search policy", async () => {
  const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) =>
    Promise.resolve({
      ok: true,
      json: async () => init?.method === "PUT"
        ? {}
        : _url.endsWith("/agent-capabilities")
          ? { models: [{ id: "gpt-5.6-sol", name: "GPT-5.6-Sol", default_reasoning_effort: "low", reasoning_efforts: ["low"], input_context_tokens: 272000, context_estimator_version: "utf8-v1", capability_fingerprint: "sha256:test" }] }
          : { items: {} },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<AgentProfiles project={{ id: "p", name: "p", root: "/repo" }} />);
  fireEvent.click(await screen.findByText("Дополнительные параметры"));
  fireEvent.click(screen.getByRole("checkbox", { name: "Разрешить ограниченный поиск по исходникам" }));
  expect(await screen.findByText(/Резерв контекста:/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Проверить и сохранить профиль" }));
  await waitFor(() => {
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    const profile = JSON.parse(String(put?.[1]?.body)).profile;
    expect(profile.source_search).toMatchObject({
      operations: expect.arrayContaining(["search_text", "find_symbol"]),
      max_calls: expect.any(Number),
      max_backend_seconds: expect.any(Number),
      max_total_returned_bytes: expect.any(Number),
    });
    expect(profile.source_search.backend).toBeUndefined();
  });
});

test("index validation is an explicit progress-visible action", async () => {
  let finishValidation!: (value: unknown) => void;
  const validation = new Promise((resolve) => {
    finishValidation = resolve;
  });
  const fetchMock = vi.fn().mockImplementation((url: string) =>
    url.endsWith("/actions")
      ? validation
      : Promise.resolve({
      ok: true,
      json: async () => url.endsWith("/indexes")
        ? {
            items: [],
            configuration: {
              schema_version: "2",
              backends: [{ adapter_id: "rlm-tools-bsl", engine_version: "1.30.0" }],
              routes: { "text-search": ["rlm-tools-bsl"] },
            },
            configuration_fingerprint: "sha256:config",
          }
          : {},
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<Indexes
    project={{ id: "p", name: "p", root: "/repo" }}
    snapshot={{ workflow_fingerprint: "sha256:workflow" } as never}
  />);
  fireEvent.click(await screen.findByRole("button", { name: "Проверить готовность" }));
  expect(await screen.findByText("Проверяется готовность индексов…")).toBeInTheDocument();
  await waitFor(() => {
    const action = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/actions"));
    expect(JSON.parse(String(action?.[1]?.body))).toMatchObject({
      operation: "indexes.build",
      payload: { mode: "validate" },
    });
  });
  finishValidation({
    ok: true,
    json: async () => ({ operation: "indexes.validate", components: [] }),
  });
});

test("index workspace shows mixed backend readiness and reviewed route impact", async () => {
  const items = [
    {
      component_id: "target_cf:configuration",
      source_generation_id: "gen",
      fingerprint: "sha256:source",
      adapter_id: "bsl-analyzer",
      adapter_version: "bsl-analyzer-workspace/v1",
      engine_version: "0.2.63",
      bsl_file_count: 5,
      status: "ready",
      last_validation: "2026-07-29T00:00:00Z",
      index_fingerprint: "sha256:bsl",
      contract_version: "1.1",
      capabilities: ["text-search"],
      route_priorities: { "text-search": 0 },
    },
    {
      component_id: "target_cf:configuration",
      source_generation_id: "gen",
      fingerprint: "sha256:source",
      adapter_id: "rlm-tools-bsl",
      adapter_version: "rlm-index/v1",
      engine_version: "1.30.0",
      bsl_file_count: 5,
      status: "unavailable",
      capabilities: [],
      route_priorities: {},
      failure_code: "backend.executable_unavailable",
      failure_summary: "approved launcher is unavailable",
    },
  ];
  const fetchMock = vi.fn().mockImplementation((url: string) =>
    Promise.resolve({
      ok: true,
      json: async () => url.endsWith("/configuration-preview")
        ? {
            plan_fingerprint: "sha256:plan",
            degraded_routes: ["text-search"],
            rebuild_backends: [],
          }
        : url.endsWith("/indexes")
          ? {
              items,
              configuration: {
                schema_version: "2",
                backends: [
                  { adapter_id: "bsl-analyzer", engine_version: "0.2.63" },
                  { adapter_id: "rlm-tools-bsl", engine_version: "1.30.0" },
                ],
                routes: {
                  "text-search": ["bsl-analyzer", "rlm-tools-bsl"],
                },
              },
              configuration_fingerprint: "sha256:config",
            }
          : {},
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<Indexes
    project={{ id: "p", name: "p", root: "/repo" }}
    snapshot={{ workflow_fingerprint: "sha256:workflow" } as never}
  />);
  expect(await screen.findByText(/bsl-analyzer 0.2.63/)).toBeInTheDocument();
  expect(screen.getByText(/rlm-tools-bsl 1.30.0/)).toBeInTheDocument();
  expect(screen.getByText(/backend.executable_unavailable/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("Настройка адаптеров и маршрутов"));
  fireEvent.click(screen.getByRole("button", { name: "Проверить изменения" }));
  expect(await screen.findByText(/degraded_routes/)).toBeInTheDocument();
  expect(screen.getByRole("button", {
    name: "Применить проверенный план",
  })).toBeEnabled();
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
      job_id: "analyze-dif",
      step: {
        id: "step-1",
        operation: "dif.classify-next",
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
      job_id: "analyze-dif",
      step: {
        id: "step-1",
        operation: "dif.classify-next",
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
      `Формы в группе, сумма по компонентам — управляемые: ${form_counts.managed}; обычные: ${form_counts.ordinary}; неопределённые: ${form_counts.inconclusive}`,
    );
    expect(container).toHaveTextContent(routing_reason);
    if (absent_roles.length)
      expect(container).toHaveTextContent("Отсутствуют роли: next_vendor");
  },
);

function ordinaryOrUnknown(counts: { ordinary: number; inconclusive: number }) {
  return counts.ordinary > 0 || counts.inconclusive > 0;
}
