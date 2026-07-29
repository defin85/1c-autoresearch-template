import {
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  AppBar,
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  Chip,
  CircularProgress,
  CssBaseline,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  LinearProgress,
  MenuItem,
  Select,
  Stack,
  Tab,
  Tabs,
  TextField,
  Toolbar,
  Typography,
} from "@mui/material";
import { api, mutationHeaders } from "./api";
import { PipelineDispatcher, type JournalTarget, type RegistryTarget } from "./dispatcher/PipelineDispatcher";
import { OfficialSubFlowReference } from "./dispatcher/OfficialSubFlowReference";
import { EnrichedSubFlowReference } from "./dispatcher/EnrichedSubFlowReference";
import { ExternalFolderImport } from "./ExternalFolderImport";
import type { DispatcherProjection } from "./dispatcher/projection";

type Blocker = { code: string; message: string; action: string; uuid?: string };
type Gate = {
  id: string;
  state: "blocked" | "ready" | "complete";
  blockers: Blocker[];
};
type Snapshot = {
  schema_version: string;
  state: string;
  workflow_fingerprint: string;
  gates: Gate[];
  extension_scope_blockers?: Blocker[];
  dispatcher?: DispatcherProjection;
};
type Project = { id: string; name: string; root: string };
type Event = {
  sequence: number;
  timestamp: string;
  type: string;
  run_id: string;
  job_id?: string;
  step_id?: string;
  attempt?: number;
  payload: Record<string, unknown>;
};
type AgentProfile = {
  provider: "codex-cli";
  model: string;
  reasoning_effort: "low" | "medium" | "high" | "xhigh" | "max" | "ultra";
  instructions_version: string;
  environment_preset: "local-read-only";
  source_search?: SourceSearchPolicy;
};
type SourceSearchPolicy = {
  operations: string[];
  max_calls: number;
  max_concurrent_calls: number;
  per_call_deadline_seconds: number;
  max_backend_seconds: number;
  max_query_bytes: number;
  max_total_query_bytes: number;
  max_results_per_call: number;
  max_total_results: number;
  max_returned_bytes_per_call: number;
  max_total_returned_bytes: number;
};
const SOURCE_SEARCH_OPERATIONS = [
  "search_text", "find_symbol", "find_references",
  "find_callers", "find_callees", "navigate_metadata",
];
const DEFAULT_SOURCE_SEARCH_POLICY: SourceSearchPolicy = {
  operations: [...SOURCE_SEARCH_OPERATIONS],
  max_calls: 8,
  max_concurrent_calls: 2,
  per_call_deadline_seconds: 30,
  max_backend_seconds: 120,
  max_query_bytes: 1024,
  max_total_query_bytes: 8192,
  max_results_per_call: 20,
  max_total_results: 100,
  max_returned_bytes_per_call: 32768,
  max_total_returned_bytes: 262144,
};
type AgentModel = {
  id: string;
  name: string;
  default_reasoning_effort: AgentProfile["reasoning_effort"];
  reasoning_efforts: AgentProfile["reasoning_effort"][];
  input_context_tokens: number;
  context_estimator_version: string;
  capability_fingerprint: string;
  structured_response_reserve_tokens: number;
  estimated_bytes_per_token: number;
  source_search_framing_bytes_per_call: number;
};
type AgentRole = {
  role_id: string;
  agent_profile: string;
  count: number;
  instruction_supplement: string;
};
type AgentPhase = {
  phase_id: string;
  mode: "sequential" | "parallel-pool" | "coordinated-pool";
  max_concurrency: number;
  roles: AgentRole[];
};
type ExternalArtifact = {
  role: string;
  kind: string;
  semantic_key: string;
  filename: string;
  declared_size_bytes: number;
  sha256?: string;
  external_artifact_id: string;
  uploaded: boolean;
};
type ExtensionScopeRow = {
  uuid: string;
  decision: "" | "include" | "exclude";
  rationale: string;
  dormant: boolean;
  roles: Record<
    string,
    { present: boolean; name: string; version: string; active: boolean }
  >;
};
type SourceSetup = {
  profiles: string[];
  infobases: {
    acquisition_profile: string;
    roles: Record<
      string,
      {
        connection_profile: string;
        configuration_name: string;
        root_uuid: string;
        version: string;
      }
    >;
  };
  infobases_fingerprint: string;
  extension_scope: {
    extensions: ExtensionScopeRow[];
    included: string[];
    excluded: string[];
    dormant: string[];
    unreviewed: string[];
  };
  external_artifacts: { artifacts: ExternalArtifact[] };
  upload_draft_fingerprint: string;
  connection_profiles: Record<
    string,
    {
      available: boolean;
      tested: boolean;
      profile_id: string;
      platform_path: string;
      server: string;
      reference: string;
      dbms: string;
      db_server: string;
      db_name: string;
      db_user: string;
      infobase_user: string;
      client_connection: string;
      db_password_set: boolean;
      infobase_password_set: boolean;
      extension_count: number;
      extensions: {
        uuid: string;
        name: string;
        version: string;
        active: boolean;
      }[];
      tool_versions: Record<string, string>;
    }
  >;
  active_source: Record<string, unknown>;
  active_diff: { generation_id?: string; row_counts?: Record<string, number> };
};
type SourceIndex = {
  component_id: string;
  source_generation_id: string;
  fingerprint: string;
  index_key?: string;
  engine?: string;
  adapter_id: string;
  adapter_version: string;
  engine_version: string;
  bsl_file_count: number;
  status: string;
  last_validation?: string;
  index_fingerprint?: string;
  target_fingerprint?: string;
  contract_version?: string;
  capabilities: string[];
  route_priorities: Record<string, number>;
  readiness_reason?: string;
  recovery_action?: string;
  failure_code?: string;
  failure_summary?: string;
};
type StepConfiguration = {
  job_id: string;
  step: {
    id: string;
    operation: string;
    timeout_seconds: number;
    max_retries?: number;
    agent_phases?: AgentPhase[];
  };
  catalog: { executor: string; effect: string; approval_required: boolean };
};
type WorkflowConfiguration = {
  manifest_fingerprint: string;
  jobs: { id: string; needs: string[] }[];
  steps: StepConfiguration[];
};
type RoutingGroup = {
  routing_group_id: string;
  form_counts: { managed: number; ordinary: number; inconclusive: number };
  routing_reason: string;
  exporter: string;
  representation_schema: string;
  absent_roles: string[];
};
type RoutingPreview = {
  preview_id: string;
  status: "pending" | "running" | "ready" | "blocked" | "failed" | "cancelled";
  routing_plan_fingerprint: string;
  routing_manifest?: { groups?: RoutingGroup[] };
  required_tools?: string[];
  blockers?: { code: string; uuid: string; action: string }[];
  progress?: {
    phase: "queued" | "probe" | "route";
    completed: number;
    total: number;
    subject: string;
  };
  error?: string;
};
type AcquisitionRun = {
  run_id: string;
  status: "running" | "completed" | "failed" | "cancelled";
  progress: {
    phase: "queued" | "connections" | "probe" | "route" | "export" | "publication";
    completed: number;
    total: number;
    subject: string;
  };
  error?: string;
};
type SourceToolInventory = {
  complete: boolean;
  tools: {
    tool_id: string;
    status: string;
    purpose: string;
    instances: { version: string; status: string; path: string }[];
  }[];
};

function SourceSection({
  step,
  title,
  summary,
  defaultExpanded = false,
  children,
}: {
  step: number;
  title: string;
  summary: string;
  defaultExpanded?: boolean;
  children: ReactNode;
}) {
  return (
    <Accordion
      defaultExpanded={defaultExpanded}
      disableGutters
      sx={{ border: 1, borderColor: "divider", boxShadow: "none" }}
    >
      <AccordionSummary
        expandIcon={<Typography aria-hidden="true">⌄</Typography>}
      >
        <Stack direction="row" spacing={1.5} alignItems="center">
          <Chip label={step} size="small" color="primary" />
          <Box>
            <Typography fontWeight={700}>{title}</Typography>
            <Typography variant="body2" color="text.secondary">
              {summary}
            </Typography>
          </Box>
        </Stack>
      </AccordionSummary>
      <AccordionDetails sx={{ pt: 0 }}>{children}</AccordionDetails>
    </Accordion>
  );
}

export function RoutingPreviewSummary({
  preview,
  onReviewExtension,
}: {
  preview: RoutingPreview;
  onReviewExtension?: (uuid: string) => void;
}) {
  return (
    <>
      <Alert severity={preview.status === "ready" ? "success" : "info"}>
        Состояние: {preview.status}. Требуемые инструменты:{" "}
        {preview.required_tools?.join(", ") || "не определены"}.
      </Alert>
      {["pending", "running"].includes(preview.status) && (
        <Box>
          <LinearProgress
            variant={preview.progress?.total ? "determinate" : "indeterminate"}
            value={
              preview.progress?.total
                ? (preview.progress.completed / preview.progress.total) * 100
                : undefined
            }
          />
          <Typography variant="caption">
            {preview.progress?.phase === "route"
              ? "Формирование маршрута"
              : preview.progress?.total
                ? `Проверено компонентов: ${preview.progress.completed} из ${preview.progress.total}. Текущий: ${preview.progress.subject}`
                : "Подготовка проверки"}
          </Typography>
        </Box>
      )}
      {preview.status === "failed" && (
        <Alert severity="error">
          {preview.error || "Не удалось построить маршрут."}
        </Alert>
      )}
      {preview.blockers?.map((blocker) => (
        <Alert
          key={`${blocker.code}:${blocker.uuid}`}
          severity="warning"
          action={
            blocker.action === "review_extension_scope" && onReviewExtension
              ? <Button color="inherit" onClick={() => onReviewExtension(blocker.uuid)}>Проверить расширение</Button>
              : undefined
          }
        >
          {blocker.code}: {blocker.uuid}
        </Alert>
      ))}
      {preview.routing_manifest?.groups?.map((group) => (
        <Card key={group.routing_group_id} variant="outlined">
          <CardContent>
            <Typography variant="subtitle2">
              {group.routing_group_id}
            </Typography>
            <Typography>
              Формы в группе, сумма по компонентам — управляемые:{" "}
              {group.form_counts.managed}; обычные:{" "}
              {group.form_counts.ordinary}; неопределённые:{" "}
              {group.form_counts.inconclusive}
            </Typography>
            <Typography>
              {group.exporter} · {group.representation_schema} ·{" "}
              {group.routing_reason}
            </Typography>
            {group.absent_roles.length > 0 && (
              <Typography color="text.secondary">
                Отсутствуют роли: {group.absent_roles.join(", ")}
              </Typography>
            )}
          </CardContent>
        </Card>
      ))}
    </>
  );
}

export function ToolInventory({
  project,
  selectedProfile,
  routingPreview,
}: {
  project: Project;
  selectedProfile: string;
  routingPreview?: RoutingPreview;
}) {
  const [inventory, setInventory] = useState<SourceToolInventory>();
  const [error, setError] = useState("");
  const refresh = useCallback(
    () =>
      api<SourceToolInventory>(`/projects/${project.id}/source-tools`)
        .then(setInventory)
        .catch((error) => setError(error.message)),
    [project.id],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const use = (tool: string) =>
    (tool === "ibcmd" && selectedProfile.startsWith("ibcmd+")) ||
    (tool === "designer" && selectedProfile.startsWith("designer+")) ||
    (tool === "v8unpack" &&
      Boolean(
        routingPreview?.routing_manifest?.groups?.some(
          (group) => group.representation_schema === "v8unpack/v1",
        ),
      ))
      ? "требуется текущим маршрутом"
      : tool === "v8unpack"
        ? "требуется при обычных или неопределённых формах"
        : tool === "edt"
          ? "только старые поколения"
          : "не выбран";
  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography fontWeight={700}>Состояние инструментов</Typography>
        {inventory && (
          <Chip
            size="small"
            color={inventory.complete ? "success" : "warning"}
            label={
              inventory.complete
                ? "проверка завершена"
                : "проверка завершена частично"
            }
          />
        )}
        <Button onClick={() => void refresh()}>Обновить</Button>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      {inventory && (
        <Box component="details">
          <Typography component="summary" sx={{ cursor: "pointer" }}>
            Показать установки, версии и пути
          </Typography>
          <Box sx={{ overflowX: "auto", mt: 1 }}>
            <Box
              component="table"
              aria-label="Установки инструментов получения исходников"
              sx={{ width: "100%", textAlign: "left" }}
            >
              <caption>
                Живая проверка установок; пути не входят в канонические
                отпечатки.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Инструмент</th>
                  <th scope="col">Состояние</th>
                  <th scope="col">Использование</th>
                  <th scope="col">Версия</th>
                  <th scope="col">Путь</th>
                </tr>
              </thead>
              <tbody>
                {inventory.tools.flatMap((tool) =>
                  tool.instances.length
                    ? tool.instances.map((item, index) => (
                        <tr key={`${tool.tool_id}:${item.path}`}>
                          <th scope="row">{index === 0 ? tool.tool_id : ""}</th>
                          <td>{item.status}</td>
                          <td>{index === 0 ? use(tool.tool_id) : ""}</td>
                          <td>{item.version || "не определена"}</td>
                          <td>{item.path}</td>
                        </tr>
                      ))
                    : [
                        <tr key={tool.tool_id}>
                          <th scope="row">{tool.tool_id}</th>
                          <td>{tool.status}</td>
                          <td>{use(tool.tool_id)}</td>
                          <td>—</td>
                          <td>не найдено</td>
                        </tr>,
                      ],
                )}
              </tbody>
            </Box>
          </Box>
        </Box>
      )}
      {inventory && !inventory.complete && (
        <Alert severity="warning">
          Проверка завершена частично: некоторые кандидаты не проверены в
          пределах лимита.
        </Alert>
      )}
    </Stack>
  );
}

function ProjectPicker({ onSelect }: { onSelect: (project: Project) => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [root, setRoot] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const refresh = () => {
    void api<Project[]>("/projects")
      .then(setProjects)
      .catch((error) => setError(error.message));
  };
  useEffect(refresh, []);
  const save = async () => {
    setSaving(true);
    try {
      const project = await api<Project>("/projects", {
        method: "POST",
        headers: mutationHeaders(`bookmark-${Date.now()}`),
        body: JSON.stringify({ name, root }),
      });
      setOpen(false);
      refresh();
      onSelect(project);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Box p={3}>
      <Typography variant="h4" mb={1}>
        Исследование конфигурации 1С
      </Typography>
      <Typography color="text.secondary" mb={3}>
        Репозиторий хранит состояние процесса; браузер только показывает его и
        отправляет типизированные действия.
      </Typography>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      <Button variant="contained" onClick={() => setOpen(true)} sx={{ mb: 3 }}>
        Открыть репозиторий
      </Button>
      <Stack spacing={2}>
        {projects.map((project) => (
          <Card key={project.id} variant="outlined">
            <CardContent>
              <Typography variant="h6">{project.name}</Typography>
              <Typography component="code">{project.root}</Typography>
              <Box mt={2}>
                <Button onClick={() => onSelect(project)}>
                  Открыть процесс
                </Button>
              </Box>
            </CardContent>
          </Card>
        ))}
      </Stack>
      <Dialog open={open} onClose={() => setOpen(false)}>
        <DialogTitle>Добавить закладку проекта</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={1} minWidth={480}>
            <TextField
              label="Название"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <TextField
              label="Путь к репозиторию"
              value={root}
              onChange={(event) => setRoot(event.target.value)}
              helperText="Закладка не является состоянием исследования."
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Отмена</Button>
          <Button variant="contained" disabled={saving} onClick={save}>
            Открыть
          </Button>
          {saving && <CircularProgress size={18} aria-label="Открытие репозитория" />}
        </DialogActions>
      </Dialog>
    </Box>
  );
}

export function AgentProfiles({ project }: { project: Project }) {
  const [items, setItems] = useState<Record<string, AgentProfile>>({});
  const [models, setModels] = useState<AgentModel[]>([]);
  const [profileId, setProfileId] = useState("local");
  const [model, setModel] = useState("gpt-5.6-sol");
  const [reasoning, setReasoning] =
    useState<AgentProfile["reasoning_effort"]>("low");
  const [version, setVersion] = useState("1");
  const [environment, setEnvironment] =
    useState<AgentProfile["environment_preset"]>("local-read-only");
  const [sourceSearchEnabled, setSourceSearchEnabled] = useState(false);
  const [sourceSearch, setSourceSearch] = useState<SourceSearchPolicy>(DEFAULT_SOURCE_SEARCH_POLICY);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const load = useCallback(
    () =>
      api<{ items: Record<string, AgentProfile> }>(
        `/projects/${project.id}/agent-profiles`,
      )
        .then((value) => {
          setItems(value.items);
          const profile = value.items[profileId];
          if (profile) {
            setModel(profile.model);
            setReasoning(profile.reasoning_effort);
            setVersion(profile.instructions_version);
            setEnvironment(profile.environment_preset);
            setSourceSearchEnabled(Boolean(profile.source_search));
            setSourceSearch(profile.source_search ?? DEFAULT_SOURCE_SEARCH_POLICY);
          }
        })
        .catch((error) =>
          setError(
            `${error.message}. Старый профиль недействителен: выберите встроенную среду и пересохраните его.`,
          ),
        ),
    [project.id],
  );
  useEffect(() => {
    void load();
    void api<{ models: AgentModel[] }>(
      `/projects/${project.id}/agent-capabilities`,
    )
      .then((value) => {
        setModels(value.models);
        if (!value.models.some((item) => item.id === model)) {
          const first = value.models[0];
          setModel(first.id);
          setReasoning(first.default_reasoning_effort);
        }
      })
      .catch((error) => setError((error as Error).message));
  }, [load]);
  const selectedModel = models.find((item) => item.id === model);
  const dynamicSearchReserve = sourceSearch.max_total_query_bytes
    + sourceSearch.max_total_returned_bytes
    + sourceSearch.max_calls * (selectedModel?.source_search_framing_bytes_per_call ?? 512);
  const preparedInputHeadroom = selectedModel
    ? Math.max(
        0,
        (selectedModel.input_context_tokens - selectedModel.structured_response_reserve_tokens)
          * selectedModel.estimated_bytes_per_token
          - dynamicSearchReserve,
      )
    : undefined;
  const selectProfile = (id: string, profile: AgentProfile) => {
    setProfileId(id);
    setModel(profile.model);
    setReasoning(profile.reasoning_effort);
    setVersion(profile.instructions_version);
    setEnvironment(profile.environment_preset);
    setSourceSearchEnabled(Boolean(profile.source_search));
    setSourceSearch(profile.source_search ?? DEFAULT_SOURCE_SEARCH_POLICY);
  };
  const save = async () => {
    setSaving(true);
    try {
      setError("");
      await api(`/projects/${project.id}/agent-profiles/${profileId}`, {
        method: "PUT",
        headers: mutationHeaders(),
        body: JSON.stringify({
          profile: {
            provider: "codex-cli",
            model,
            reasoning_effort: reasoning,
            instructions_version: version,
            environment_preset: environment,
            ...(sourceSearchEnabled ? { source_search: sourceSearch } : {}),
          },
        }),
      });
      await load();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="h6" mb={2}>
          Профили агентов
        </Typography>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "220px minmax(0, 1fr)" }, gap: 2 }}>
          <Stack spacing={1} aria-label="Профили агентов">
            {Object.entries(items).map(([id, profile]) => (
              <Button
                key={id}
                variant={id === profileId ? "contained" : "outlined"}
                onClick={() => selectProfile(id, profile)}
                sx={{ justifyContent: "flex-start" }}
              >
                {id}
              </Button>
            ))}
            <Button variant="text" onClick={() => setProfileId("")}>Новый профиль</Button>
          </Stack>
          <Stack spacing={2}>
            <TextField label="Идентификатор профиля" value={profileId} onChange={(event) => setProfileId(event.target.value)} />
            <FormControl>
              <InputLabel id="agent-model-label">Модель</InputLabel>
              <Select
                labelId="agent-model-label"
                label="Модель"
                value={models.some((item) => item.id === model) ? model : ""}
                onChange={(event) => {
                  const selected = models.find((item) => item.id === event.target.value);
                  if (!selected) return;
                  setModel(selected.id);
                  setReasoning(selected.default_reasoning_effort);
                }}
              >
                {models.map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
              </Select>
            </FormControl>
            {selectedModel && (
              <Typography variant="caption" color="text.secondary">
                Проверенная ёмкость входного контекста: {selectedModel.input_context_tokens.toLocaleString("ru-RU")} токенов · оценщик {selectedModel.context_estimator_version}
              </Typography>
            )}
            <FormControl>
              <InputLabel id="agent-reasoning-label">Уровень рассуждения</InputLabel>
              <Select labelId="agent-reasoning-label" label="Уровень рассуждения" value={selectedModel?.reasoning_efforts.includes(reasoning) ? reasoning : ""} onChange={(event) => setReasoning(event.target.value as AgentProfile["reasoning_effort"])}>
                {(selectedModel?.reasoning_efforts || []).map((value) => <MenuItem key={value} value={value}>{value}</MenuItem>)}
              </Select>
            </FormControl>
            <Accordion disableGutters sx={{ boxShadow: "none", border: 1, borderColor: "divider" }}>
              <AccordionSummary expandIcon={<Typography aria-hidden="true">⌄</Typography>}>
                <Typography fontWeight={700}>Дополнительные параметры</Typography>
              </AccordionSummary>
              <AccordionDetails>
                <Stack spacing={2}>
                  <Typography variant="caption">
                    Профили хранятся только в пользовательском каталоге. Среда local-read-only запрещает запись, но может читать репозиторий; предметные allowed_paths ограничивают контекст инструкции, а не файловый доступ.
                  </Typography>
                  <FormControl>
                    <InputLabel>Версия инструкций</InputLabel>
                    <Select label="Версия инструкций" value={version} onChange={(event) => setVersion(event.target.value)}>
                      <MenuItem value="1">1</MenuItem>
                    </Select>
                  </FormControl>
                  <FormControl>
                    <InputLabel>Среда исполнения</InputLabel>
                    <Select label="Среда исполнения" value={environment} onChange={(event) => setEnvironment(event.target.value as AgentProfile["environment_preset"])}>
                      <MenuItem value="local-read-only">local-read-only · только чтение</MenuItem>
                    </Select>
                  </FormControl>
                  <FormControlLabel
                    control={<Checkbox checked={sourceSearchEnabled} onChange={(event) => setSourceSearchEnabled(event.target.checked)} />}
                    label="Разрешить ограниченный поиск по исходникам"
                  />
                  {sourceSearchEnabled && <>
                    <Typography variant="caption">
                      Резерв контекста: {dynamicSearchReserve.toLocaleString("ru-RU")} байт.
                      {preparedInputHeadroom !== undefined && ` Максимальный остаток для подготовленного контекста: ${preparedInputHeadroom.toLocaleString("ru-RU")} байт.`}
                      {" "}Изменение применяется только к новым вызовам.
                    </Typography>
                    <Stack direction="row" useFlexGap flexWrap="wrap">
                      {SOURCE_SEARCH_OPERATIONS.map((operation) => <FormControlLabel
                        key={operation}
                        control={<Checkbox
                          checked={sourceSearch.operations.includes(operation)}
                          onChange={(event) => setSourceSearch((current) => ({
                            ...current,
                            operations: event.target.checked
                              ? [...current.operations, operation]
                              : current.operations.filter((item) => item !== operation),
                          }))}
                        />}
                        label={operation}
                      />)}
                    </Stack>
                    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(2, 1fr)" }, gap: 1 }}>
                      {(Object.keys(DEFAULT_SOURCE_SEARCH_POLICY) as Array<keyof SourceSearchPolicy>)
                        .filter((field) => field !== "operations")
                        .map((field) => <TextField
                          key={field}
                          type="number"
                          label={field}
                          value={sourceSearch[field]}
                          inputProps={{ min: 1 }}
                          onChange={(event) => setSourceSearch((current) => ({ ...current, [field]: Number(event.target.value) }))}
                        />)}
                    </Box>
                  </>}
                </Stack>
              </AccordionDetails>
            </Accordion>
            <Button variant="contained" disabled={saving || !profileId.trim()} onClick={() => void save()}>
              Проверить и сохранить профиль
            </Button>
            {saving && (
              <Stack direction="row" spacing={1} alignItems="center" role="status" aria-live="polite">
                <CircularProgress size={18} />
                <Typography variant="body2">Проверяется и сохраняется профиль…</Typography>
              </Stack>
            )}
          </Stack>
        </Box>
      </CardContent>
    </Card>
  );
}

type WorkflowPatchPreview = {
  before?: StepConfiguration["step"];
  after?: StepConfiguration["step"];
  agent_phase_preview?: Array<{
    phase_id: string;
    mode: AgentPhase["mode"];
    effective_max_concurrency: number;
    maximum_calls_in_current_window: number;
    roles: Array<{
      role_id: string;
      agent_profile: string;
      profile: Partial<AgentProfile>;
    }>;
    sandbox: string;
    allowed_paths: string;
  }>;
};

const STEP_NAMES: Record<string, string> = {
  "validate-project": "Проверка проекта",
  "acquire-sources": "Получение исходников",
  "build-diffs": "Построение различий",
  "index-sources": "Индексация",
  "analyze-dif": "Анализ DIF",
  "consolidate-mrq": "Формирование и консолидация MRQ",
  "classify-mrq": "Формирование пакетов",
  "decide-mrq": "Исследование цели",
  "build-projections": "Построение проекций",
  "verify-workflow": "Проверка процесса",
};

const PHASE_NAMES: Record<string, string> = {
  "analyze-dif": "Анализ DIF",
  "form-mrq": "Формирование MRQ",
  "classify-batches": "Формирование пакетов",
  "research-target": "Исследование цели",
};

const ROLE_NAMES: Record<string, string> = {
  analyzer: "Анализатор",
  coordinator: "Координатор",
  grouper: "Группировщик",
  classifier: "Классификатор",
  researcher: "Исследователь",
};

export function WorkflowEditor({
  project,
  snapshot,
  refresh,
  initialStepId,
}: {
  project: Project;
  snapshot: Snapshot;
  refresh: () => void;
  initialStepId?: string;
}) {
  const [configuration, setConfiguration] = useState<WorkflowConfiguration>();
  const [selected, setSelected] = useState("");
  const [timeout, setTimeoutValue] = useState(1800);
  const [retries, setRetries] = useState(0);
  const [phases, setPhases] = useState<AgentPhase[]>([]);
  const [preview, setPreview] = useState<{
    response: WorkflowPatchPreview;
    parameters: Record<string, unknown>;
  }>();
  const [error, setError] = useState("");
  const [pendingAction, setPendingAction] = useState<"preview" | "apply">();
  const [agentProfiles, setAgentProfiles] = useState<
    Record<string, AgentProfile>
  >({});
  const load = useCallback(
    () =>
      api<WorkflowConfiguration>(
        `/projects/${project.id}/workflow/configuration`,
        )
        .then((value) => {
          setConfiguration(value);
          setSelected((previous) =>
            previous ||
            value.steps.find((item) => item.step.id === initialStepId)?.step.id ||
            value.steps[0]?.step.id ||
            "",
          );
        })
        .catch((error) => setError(error.message)),
    [initialStepId, project.id],
  );
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    void api<{ items: Record<string, AgentProfile> }>(
      `/projects/${project.id}/agent-profiles`,
    )
      .then((value) => setAgentProfiles(value.items))
      .catch((caught) => setError((caught as Error).message));
  }, [project.id]);
  const current = configuration?.steps.find(
    (item) => item.step.id === selected,
  );
  const hasMissingProfiles = phases.some((phase) =>
    phase.roles.some((role) => !agentProfiles[role.agent_profile]),
  );
  useEffect(() => {
    if (current) {
      setTimeoutValue(current.step.timeout_seconds);
      setRetries(current.step.max_retries || 0);
      setPhases(current.step.agent_phases || []);
      setPreview(undefined);
    }
  }, [current]);
  const parameters = () => ({
    timeout_seconds: timeout,
    ...(["diff.build", "projections.build"].includes(
      current?.step.operation || "",
    )
      ? { max_retries: retries }
      : {}),
    ...(current?.step.agent_phases
      ? { agent_phases: phases }
      : {}),
  });
  const invalidatePreview = () => setPreview(undefined);
  const updatePhase = (phaseId: string, patch: Partial<AgentPhase>) => {
    invalidatePreview();
    setPhases((items) =>
      items.map((phase) =>
        phase.phase_id === phaseId ? { ...phase, ...patch } : phase,
      ),
    );
  };
  const updateRole = (
    phaseId: string,
    roleId: string,
    patch: Partial<AgentRole>,
  ) => {
    invalidatePreview();
    setPhases((items) =>
      items.map((phase) =>
        phase.phase_id === phaseId
          ? {
              ...phase,
              roles: phase.roles.map((role) =>
                role.role_id === roleId ? { ...role, ...patch } : role,
              ),
            }
          : phase,
      ),
    );
  };
  const showPreview = async () => {
    setPendingAction("preview");
    try {
      setError("");
      const selectedParameters = parameters();
      const response = await api<WorkflowPatchPreview>(
        `/projects/${project.id}/workflow/patch-preview`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            step_id: selected,
            parameters: selectedParameters,
            expected_manifest_fingerprint: configuration?.manifest_fingerprint,
          }),
        },
      );
      setPreview({ response, parameters: selectedParameters });
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setPendingAction(undefined);
    }
  };
  const apply = async () => {
    setPendingAction("apply");
    try {
      setError("");
      await api(`/projects/${project.id}/actions`, {
        method: "POST",
        headers: mutationHeaders(),
        body: JSON.stringify({
          operation: "workflow.patch-step",
          payload: {
            step_id: selected,
            parameters: preview?.parameters,
            expected_manifest_fingerprint: configuration?.manifest_fingerprint,
          },
          expected_fingerprint: snapshot.workflow_fingerprint,
        }),
      });
      setPreview(undefined);
      await load();
      refresh();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setPendingAction(undefined);
    }
  };
  if (!configuration || !current) return <CircularProgress />;
  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="h6" mb={2}>
          Этапы процесса
        </Typography>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "260px minmax(0, 1fr)" }, gap: 2 }}>
          <Stack spacing={0.75} component="nav" aria-label="Этапы процесса">
            {configuration.steps.map((item) => (
              <Button
                key={item.step.id}
                variant={selected === item.step.id ? "contained" : "text"}
                onClick={() => setSelected(item.step.id)}
                sx={{ justifyContent: "flex-start", textAlign: "left", py: 1 }}
              >
                <Box>
                  <Typography component="span" display="block" fontWeight={700}>
                    {STEP_NAMES[item.step.id] || item.step.id}
                  </Typography>
                  <Typography component="span" display="block" variant="caption" sx={{ opacity: 0.75 }}>
                    {item.step.id}
                  </Typography>
                </Box>
              </Button>
            ))}
          </Stack>
          <Stack spacing={2} minWidth={0}>
            <Box>
              <Typography variant="h6">{STEP_NAMES[current.step.id] || current.step.id}</Typography>
              <Typography variant="caption" color="text.secondary">{current.step.id} · {current.step.operation}</Typography>
            </Box>
          {phases.map((phase) => (
            <Card key={phase.phase_id} variant="outlined">
              <CardContent>
                <Typography id={`phase-${phase.phase_id}`} fontWeight={700}>
                  {PHASE_NAMES[phase.phase_id] || phase.phase_id}
                </Typography>
                <Typography variant="caption" color="text.secondary">{phase.phase_id}</Typography>
                <Stack spacing={2} mt={1}>
                  {phase.phase_id !== "form-mrq" && (
                    <FormControl>
                      <InputLabel id={`mode-${phase.phase_id}`}>Режим</InputLabel>
                      <Select
                        aria-label={`Режим ${phase.phase_id}`}
                        label="Режим"
                        labelId={`mode-${phase.phase_id}`}
                        value={phase.mode}
                        onChange={(event) => {
                          const mode = event.target.value as AgentPhase["mode"];
                          updatePhase(phase.phase_id, {
                            mode,
                            max_concurrency: mode === "sequential" ? 1 : phase.max_concurrency,
                            roles: phase.roles.map((role) => ({ ...role, count: mode === "sequential" ? 1 : role.count })),
                          });
                        }}
                      >
                        <MenuItem value="sequential">Последовательно</MenuItem>
                        <MenuItem value="parallel-pool">Параллельный пул</MenuItem>
                      </Select>
                    </FormControl>
                  )}
                  <TextField
                    type="number"
                    label="Предел одновременности"
                    inputProps={{ min: 1, "aria-label": `Предел одновременности ${phase.phase_id}` }}
                    value={phase.max_concurrency}
                    onChange={(event) => updatePhase(phase.phase_id, { max_concurrency: Number(event.target.value) })}
                    disabled={phase.mode === "sequential"}
                  />
                  {phase.roles.map((role) => (
                    <Stack key={role.role_id} spacing={1}>
                      <Typography id={`role-${phase.phase_id}-${role.role_id}`} variant="subtitle2">{ROLE_NAMES[role.role_id] || role.role_id}</Typography>
                      <FormControl error={!agentProfiles[role.agent_profile]}>
                        <InputLabel id={`profile-${phase.phase_id}-${role.role_id}`}>Профиль</InputLabel>
                        <Select aria-label={`Профиль ${phase.phase_id} ${role.role_id}`} label="Профиль" labelId={`profile-${phase.phase_id}-${role.role_id}`} value={role.agent_profile} onChange={(event) => updateRole(phase.phase_id, role.role_id, { agent_profile: event.target.value })}>
                          {Object.keys(agentProfiles).map((value) => <MenuItem key={value} value={value}>{value}</MenuItem>)}
                          {!agentProfiles[role.agent_profile] && <MenuItem value={role.agent_profile}>{role.agent_profile} — отсутствует</MenuItem>}
                        </Select>
                      </FormControl>
                      {!agentProfiles[role.agent_profile] && (
                        <Alert severity="error">
                          Профиль {role.agent_profile} отсутствует. Создайте или
                          пересохраните его перед просмотром.
                        </Alert>
                      )}
                      <TextField
                        type="number"
                        label="Количество агентов"
                        value={role.count}
                        disabled={role.role_id === "coordinator" || phase.mode === "sequential"}
                        onChange={(event) => updateRole(phase.phase_id, role.role_id, { count: Number(event.target.value) })}
                        inputProps={{ min: 1, "aria-label": `Количество агентов ${phase.phase_id} ${role.role_id}` }}
                      />
                    </Stack>
                  ))}
                </Stack>
              </CardContent>
            </Card>
          ))}
          {phases.length > 0 && (
            <Alert severity="info">
              Пределы независимы для каждой фазы; общего предела компьютера нет.
            </Alert>
          )}
          <Accordion disableGutters sx={{ boxShadow: "none", border: 1, borderColor: "divider" }}>
            <AccordionSummary expandIcon={<Typography aria-hidden="true">⌄</Typography>}>
              <Typography fontWeight={700}>Дополнительные параметры</Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={2}>
                <TextField
                  type="number"
                  label="Предельное время, секунд"
                  value={timeout}
                  onChange={(event) => {
                    invalidatePreview();
                    setTimeoutValue(Number(event.target.value));
                  }}
                  inputProps={{ min: 30, max: 86400 }}
                />
                {["diff.build", "projections.build"].includes(current.step.operation) && (
                  <TextField
                    type="number"
                    label="Повторные попытки"
                    value={retries}
                    onChange={(event) => {
                      invalidatePreview();
                      setRetries(Number(event.target.value));
                    }}
                    inputProps={{ min: 0, max: 1 }}
                  />
                )}
                {phases.flatMap((phase) => phase.roles.map((role) => (
                  <TextField
                    key={`${phase.phase_id}-${role.role_id}`}
                    multiline
                    minRows={2}
                    label={`Дополнительная инструкция · ${ROLE_NAMES[role.role_id] || role.role_id}`}
                    value={role.instruction_supplement}
                    onChange={(event) => updateRole(phase.phase_id, role.role_id, { instruction_supplement: event.target.value })}
                    inputProps={{ maxLength: 4000 }}
                  />
                )))}
              </Stack>
            </AccordionDetails>
          </Accordion>
          <Stack direction="row" spacing={1}>
            <Button
              disabled={Boolean(pendingAction) || hasMissingProfiles}
              onClick={() => void showPreview()}
            >
              Предварительный просмотр
            </Button>
            <Button
              disabled={Boolean(pendingAction) || !preview}
              variant="contained"
              onClick={() => void apply()}
            >
              Применить просмотренное изменение
            </Button>
            {pendingAction && (
              <Stack direction="row" spacing={1} alignItems="center" role="status" aria-live="polite">
                <CircularProgress size={18} />
                <Typography variant="body2">
                  {pendingAction === "preview" ? "Строится предварительный просмотр…" : "Применяется изменение…"}
                </Typography>
              </Stack>
            )}
          </Stack>
          {preview && (
            <Card variant="outlined" aria-label="Просмотр политики фаз">
              <CardContent>
                <Typography fontWeight={700}>Итоговая политика</Typography>
                {(preview.response.agent_phase_preview || []).map((phase) => (
                  <Stack key={phase.phase_id} spacing={0.25}>
                    <Typography variant="body2">
                      {phase.phase_id}: {phase.mode}; эффективный предел{" "}
                      {phase.effective_max_concurrency}; максимум вызовов текущего
                      окна {phase.maximum_calls_in_current_window}
                    </Typography>
                    <Typography variant="caption">
                      {phase.roles
                        .map(
                          (role) =>
                            `${role.role_id}=${role.agent_profile} (${role.profile.model || "профиль недоступен"}, ${role.profile.reasoning_effort || "уровень не задан"}, ${role.profile.environment_preset || "среда недоступна"})`,
                        )
                        .join("; ")}
                    </Typography>
                    <Typography variant="caption">
                      Песочница: {phase.sandbox}. Предметные разрешённые пути
                      выбирают контекст, но не сужают доступ песочницы к файлам.
                    </Typography>
                  </Stack>
                ))}
                <Typography variant="caption">
                  Стоимость и точная серверная ревизия модели не вычисляются.
                </Typography>
              </CardContent>
            </Card>
          )}
          </Stack>
        </Box>
      </CardContent>
    </Card>
  );
}

export function Sources({
  project,
  snapshot,
  refreshWorkflow,
  initialExtensionUuid,
}: {
  project: Project;
  snapshot: Snapshot;
  refreshWorkflow: () => void;
  initialExtensionUuid?: string;
}) {
  const [setup, setSetup] = useState<SourceSetup>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [profiles, setProfiles] = useState<
    Record<string, Record<string, string>>
  >({});
  const [editingProfiles, setEditingProfiles] = useState<Record<string, boolean>>(
    {},
  );
  const [artifactHashes, setArtifactHashes] = useState<Record<string, string>>(
    {},
  );
  const [sourceProfile, setSourceProfile] = useState("");
  const [roleProfiles, setRoleProfiles] = useState<Record<string, string>>({});
  const [sourcePreview, setSourcePreview] = useState<Record<string, unknown>>();
  const [extensionDecisions, setExtensionDecisions] = useState<
    Record<string, { decision: "" | "include" | "exclude"; rationale: string }>
  >({});
  const [extensionPreview, setExtensionPreview] =
    useState<Record<string, unknown>>();
  const [extensionAction, setExtensionAction] = useState<"review" | "apply">();
  const [sourceAction, setSourceAction] = useState("");
  const [routingPreview, setRoutingPreview] = useState<RoutingPreview>();
  const [acquisitionRun, setAcquisitionRun] = useState<AcquisitionRun>();
  const [acquisitionResult, setAcquisitionResult] =
    useState<Record<string, unknown>>();
  const refreshSequence = useRef(0);
  const refresh = useCallback(
    () => {
      const sequence = ++refreshSequence.current;
      return (
      api<SourceSetup>(`/projects/${project.id}/source-setup`)
        .then((value) => {
          if (sequence !== refreshSequence.current) return;
          const normalized = {
            ...value,
            extension_scope: value.extension_scope || {
              extensions: [],
              included: [],
              excluded: [],
              dormant: [],
              unreviewed: [],
            },
          };
          setSetup(normalized);
          setExtensionDecisions((current) => ({
            ...Object.fromEntries(
              normalized.extension_scope.extensions.map((row) => [
                row.uuid,
                {
                  decision: row.decision,
                  rationale: row.rationale,
                },
              ]),
            ),
            ...current,
          }));
          setProfiles(
            Object.fromEntries(
              Object.entries(value.connection_profiles).map(([id, profile]) => [
                id,
                Object.fromEntries(
                  [
                    "server",
                    "reference",
                    "platform_path",
                    "db_server",
                    "db_name",
                    "db_user",
                    "infobase_user",
                    "client_connection",
                  ].map((field) => [
                    field,
                    String(profile[field as keyof typeof profile] || ""),
                  ]),
                ),
              ]),
            ),
          );
          setSourceProfile(
            (current) => current || value.infobases.acquisition_profile,
          );
          setRoleProfiles((current) =>
            Object.keys(current).length
              ? current
              : Object.fromEntries(
                  Object.entries(value.infobases.roles).map(([role, item]) => [
                    role,
                    item.connection_profile,
                  ]),
                ),
          );
        })
        .catch((error) => {
          if (sequence === refreshSequence.current) setError(error.message);
        })
      );
    },
    [project.id],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    if (!setup || !initialExtensionUuid) return;
    const row = document.getElementById(`extension-${initialExtensionUuid}`);
    row?.scrollIntoView({ block: "center" });
    row?.focus();
  }, [setup, initialExtensionUuid]);
  const field = (id: string, name: string, fallback = "") =>
    profiles[id]?.[name] ?? fallback;
  const setField = (id: string, name: string, next: string) =>
    setProfiles((value) => ({
      ...value,
      [id]: { ...(value[id] || {}), [name]: next },
    }));
  const save = async (id: string) => {
    const value = profiles[id] || {};
    setBusy(true);
    setSourceAction("Проверяется и сохраняется подключение…");
    setError("");
    try {
      await api(`/projects/${project.id}/connection-profiles/${id}`, {
        method: "PUT",
        headers: mutationHeaders(),
        body: JSON.stringify({
          profile: {
            kind: "server",
            server: value.server || "localhost",
            reference: value.reference,
            profile_id: setup?.infobases.acquisition_profile,
            platform_path:
              value.platform_path || "/opt/1cv8/x86_64/8.3.27.1989",
            dbms: "PostgreSQL",
            db_server: value.db_server || "localhost port=5432",
            db_name: value.db_name,
            db_user: value.db_user || "postgres",
            db_password: value.db_password,
            infobase_user: value.infobase_user,
            infobase_password: value.infobase_password,
            client_connection:
              value.client_connection ||
              `/S${value.server || "localhost"}/${value.reference}`,
          },
        }),
      });
      await refresh();
      setEditingProfiles((current) => ({ ...current, [id]: false }));
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setSourceAction("");
      setBusy(false);
    }
  };
  const upload = async (artifact: ExternalArtifact, file: File) => {
    const digest = (
      artifact.sha256 ||
      artifactHashes[artifact.external_artifact_id] ||
      ""
    ).replace("sha256:", "");
    if (
      !/^[0-9a-f]{64}$/.test(digest) ||
      file.size !== artifact.declared_size_bytes
    ) {
      setError("Файл должен иметь объявленный размер и SHA-256.");
      return;
    }
    setBusy(true);
    setSourceAction(`Загружается ${artifact.filename}…`);
    setError("");
    try {
      const query = new URLSearchParams({
        filename: artifact.filename,
        declared_length: String(artifact.declared_size_bytes),
        declared_sha256: digest,
        expected_draft_fingerprint: setup?.upload_draft_fingerprint || "",
      });
      await api(
        `/projects/${project.id}/external-uploads/${artifact.role}/${artifact.external_artifact_id}?${query}`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/octet-stream",
            "Idempotency-Key": crypto.randomUUID(),
          },
          body: file,
        },
      );
      await refresh();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setSourceAction("");
      setBusy(false);
    }
  };
  const configure = async () => {
    if (
      !sourcePreview ||
      !window.confirm(
        "Применить просмотренные назначения и начать новую эпоху сравнения?",
      )
    )
      return;
    setBusy(true);
    setSourceAction("Применяются назначения источников…");
    setError("");
    try {
      await api(`/projects/${project.id}/actions`, {
        method: "POST",
        headers: mutationHeaders(),
        body: JSON.stringify({
          operation: "sources.configure",
          payload: {
            acquisition_profile: sourceProfile,
            connection_profiles: roleProfiles,
            expected_manifest_fingerprint: setup?.infobases_fingerprint,
            confirm_new_epoch: true,
          },
          expected_fingerprint: snapshot.workflow_fingerprint,
        }),
      });
      setSetup(undefined);
      setSourceProfile("");
      setRoleProfiles({});
      setSourcePreview(undefined);
      await refresh();
      refreshWorkflow();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setSourceAction("");
      setBusy(false);
    }
  };
  const extensionPayload = (confirm_new_epoch: boolean) => ({
    extension_decisions: Object.entries(extensionDecisions)
      .filter(([, value]) => value.decision)
      .map(([uuid, value]) => ({
        uuid,
        decision: value.decision,
        rationale: value.decision === "exclude" ? value.rationale : "",
      })),
    expected_manifest_fingerprint: setup?.infobases_fingerprint,
    confirm_new_epoch,
  });
  const reviewExtensions = async () => {
    const missingDecision = setup?.extension_scope.extensions.find(
      (row) => !row.dormant && !extensionDecisions[row.uuid]?.decision,
    );
    if (missingDecision) {
      setError(`Для расширения ${missingDecision.uuid} требуется решение.`);
      return;
    }
    const missingRationale = Object.entries(extensionDecisions).find(
      ([, value]) => value.decision === "exclude" && !value.rationale.trim(),
    );
    if (missingRationale) {
      setError(`Для исключённого расширения ${missingRationale[0]} требуется обоснование.`);
      return;
    }
    setBusy(true);
    setExtensionAction("review");
    setError("");
    try {
      const response = await api<Record<string, unknown>>(
        `/projects/${project.id}/actions`,
        {
          method: "POST",
          headers: mutationHeaders(),
          body: JSON.stringify({
            operation: "sources.configure",
            payload: extensionPayload(false),
            expected_fingerprint: snapshot.workflow_fingerprint,
          }),
        },
      );
      setExtensionPreview(response);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setExtensionAction(undefined);
      setBusy(false);
    }
  };
  const applyExtensions = async () => {
    if (!extensionPreview || !window.confirm("Применить просмотренный выбор расширений и начать новую эпоху сравнения?")) return;
    setBusy(true);
    setExtensionAction("apply");
    setError("");
    try {
      await api(`/projects/${project.id}/actions`, {
        method: "POST",
        headers: mutationHeaders(),
        body: JSON.stringify({
          operation: "sources.configure",
          payload: extensionPayload(true),
          expected_fingerprint: snapshot.workflow_fingerprint,
        }),
      });
      setExtensionDecisions({});
      setExtensionPreview(undefined);
      setRoutingPreview(undefined);
      await refresh();
      refreshWorkflow();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setExtensionAction(undefined);
      setBusy(false);
    }
  };
  const previewRouting = async () => {
    setBusy(true);
    setError("");
    try {
      const created = await api<RoutingPreview>(
        `/projects/${project.id}/source-routing-previews`,
        { method: "POST", headers: mutationHeaders() },
      );
      for (;;) {
        const current = await api<RoutingPreview>(
          `/projects/${project.id}/source-routing-previews/${created.preview_id}`,
        );
        setRoutingPreview(current);
        if (!["pending", "running"].includes(current.status)) return;
        await new Promise((resolve) => window.setTimeout(resolve, 250));
      }
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const cancelRouting = async () => {
    if (!routingPreview) return;
    await api(
      `/projects/${project.id}/source-routing-previews/${routingPreview.preview_id}`,
      { method: "DELETE", headers: mutationHeaders() },
    );
    setRoutingPreview({ ...routingPreview, status: "cancelled" });
  };
  const acquire = async () => {
    if (
      routingPreview?.status !== "ready" ||
      !window.confirm(
        "Получить и атомарно опубликовать полное поколение трёх баз по просмотренному маршруту?",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      const created = await api<AcquisitionRun>(
        `/projects/${project.id}/source-acquisition-runs`,
        {
          method: "POST",
          headers: mutationHeaders(),
          body: JSON.stringify({
            expected_fingerprint: snapshot.workflow_fingerprint,
            approved_operations: ["sources.acquire"],
            source_routing_preview_id: routingPreview.preview_id,
            routing_plan_fingerprint: routingPreview.routing_plan_fingerprint,
          }),
        },
      );
      for (;;) {
        await new Promise((resolve) => window.setTimeout(resolve, 500));
        const current = await api<AcquisitionRun>(
          `/projects/${project.id}/source-acquisition-runs/${created.run_id}`,
        );
        setAcquisitionRun(current);
        if (current.status === "running") continue;
        if (current.status === "failed")
          throw new Error(current.error || "Получение поколения завершилось с ошибкой.");
        setAcquisitionResult(current);
        break;
      }
      await refresh();
      refreshWorkflow();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const cancelAcquisition = async () => {
    if (!acquisitionRun) return;
    await api(
      `/projects/${project.id}/runs/${acquisitionRun.run_id}/cancel`,
      {
        method: "POST",
        headers: mutationHeaders(),
        body: JSON.stringify({ actor: "local-user" }),
      },
    );
  };
  if (!setup) return <CircularProgress />;
  const ready = Object.values(setup.infobases.roles).every(
    (item) => setup.connection_profiles[item.connection_profile]?.tested,
  );
  const platformRoots = new Set(
    Object.values(setup.infobases.roles)
      .map(
        (item) =>
          setup.connection_profiles[item.connection_profile]?.platform_path,
      )
      .filter(Boolean),
  );
  const roleEntries = Object.entries(setup.infobases.roles);
  const testedCount = roleEntries.filter(
    ([, item]) => setup.connection_profiles[item.connection_profile]?.tested,
  ).length;
  const artifacts = setup.external_artifacts.artifacts;
  const uploadedCount = artifacts.filter((item) => item.uploaded).length;
  const roleLabels: Record<string, string> = {
    vendor_baseline: "Исходная версия поставщика",
    target_cf: "Рабочая конфигурация",
    next_vendor: "Новая версия поставщика",
  };
  const legacyProfile =
    sourceProfile !== "" && !setup.profiles.includes(sourceProfile);
  const routeStatus =
    routingPreview?.status === "ready"
      ? "маршрут проверен"
      : routingPreview
        ? "проверка не завершена"
        : "маршрут ещё не проверен";
  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      {sourceAction && (
        <Stack direction="row" spacing={1} alignItems="center" role="status" aria-live="polite">
          <CircularProgress size={18} />
          <Typography variant="body2">{sourceAction}</Typography>
        </Stack>
      )}
      <Box>
        <Typography variant="h5" fontWeight={750}>
          Источники
        </Typography>
        <Typography color="text.secondary">
          Подготовьте подключения и внешние файлы, затем проверьте маршрут и
          получите новое поколение.
        </Typography>
      </Box>
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
        <Chip
          color={ready ? "success" : "warning"}
          label={`Подключения: ${testedCount}/${roleEntries.length}`}
        />
        <Chip
          color={uploadedCount === artifacts.length ? "success" : "warning"}
          label={
            artifacts.length
              ? `Внешние файлы: ${uploadedCount}/${artifacts.length}`
              : "Внешние файлы не объявлены"
          }
        />
        <Chip
          color={routingPreview?.status === "ready" ? "success" : "default"}
          label={routeStatus}
        />
      </Stack>
      {platformRoots.size > 1 && (
        <Alert severity="error">
          Для одного поколения выбраны разные каталоги платформы 1С. Повторно
          проверьте профили на одном каталоге.
        </Alert>
      )}
      <SourceSection
        step={1}
        title="Способ получения"
        summary={`Экспортёр: ${sourceProfile || "не выбран"}`}
        defaultExpanded
      >
        <Stack spacing={2}>
          <Alert severity="info">
            Один экспортёр применяется ко всем трём базам. Представление
            выбирается автоматически для каждой группы компонентов.
          </Alert>
          <FormControl fullWidth>
            <InputLabel>Экспортёр получения исходников</InputLabel>
            <Select
              label="Экспортёр получения исходников"
              value={sourceProfile}
              onChange={(event) => {
                setSourceProfile(event.target.value);
                setSourcePreview(undefined);
                setRoutingPreview(undefined);
              }}
            >
              {legacyProfile && (
                <MenuItem value={sourceProfile} disabled>
                  {sourceProfile} — устаревший профиль
                </MenuItem>
              )}
              {setup.profiles.map((value) => (
                <MenuItem key={value} value={value}>
                  {value}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          {legacyProfile && (
            <Alert severity="warning">
              Для нового получения выберите профиль с пометкой `form-aware` и
              примените назначения подключений.
            </Alert>
          )}
          <ToolInventory
            project={project}
            selectedProfile={sourceProfile}
            routingPreview={routingPreview}
          />
        </Stack>
      </SourceSection>

      <SourceSection
        step={2}
        title="Подключения к базам"
        summary={`${testedCount} из ${roleEntries.length} соединений проверено`}
        defaultExpanded={!ready}
      >
        <Stack spacing={2}>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
              gap: 2,
            }}
          >
            {roleEntries.map(([role, item]) => {
              const id = item.connection_profile;
              const current = setup.connection_profiles[id];
              return (
                <Card key={role} variant="outlined">
                  <CardContent>
                    <Typography variant="overline">
                      {roleLabels[role] || role}
                    </Typography>
                    <Typography fontWeight={700}>
                      {item.configuration_name} · {item.version}
                    </Typography>
                    <TextField
                      fullWidth
                      size="small"
                      sx={{ my: 1.5 }}
                      label="Профиль соединения"
                      value={roleProfiles[role] || ""}
                      onChange={(event) => {
                        setRoleProfiles((value) => ({
                          ...value,
                          [role]: event.target.value,
                        }));
                        setSourcePreview(undefined);
                        setRoutingPreview(undefined);
                      }}
                    />
                    <Typography
                      variant="caption"
                      component="div"
                      color="text.secondary"
                      sx={{ overflowWrap: "anywhere" }}
                    >
                      UUID: {item.root_uuid}
                    </Typography>
                    {current?.tested && !editingProfiles[id] ? (
                      <Stack spacing={1} mt={1.5}>
                        <Stack
                          direction="row"
                          spacing={1}
                          useFlexGap
                          flexWrap="wrap"
                        >
                          <Chip
                            size="small"
                            color="success"
                            label="Соединение проверено"
                          />
                          <Chip
                            size="small"
                            label={`Расширения: ${current.extension_count}`}
                          />
                        </Stack>
                        {current.extensions.length > 0 && (
                          <Box component="details">
                            <Typography
                              component="summary"
                              variant="caption"
                              sx={{ cursor: "pointer" }}
                            >
                              Показать расширения
                            </Typography>
                            {current.extensions.map((extension) => (
                              <Typography
                                key={extension.uuid}
                                variant="caption"
                                component="div"
                              >
                                {extension.active ? "●" : "○"} {extension.name}{" "}
                                {extension.version}
                              </Typography>
                            ))}
                          </Box>
                        )}
                        <Box component="dl" sx={{ m: 0, "& dt": { fontWeight: 700 }, "& dd": { ml: 0, mb: 1, overflowWrap: "anywhere" } }}>
                          <Typography component="dt" variant="caption">Каталог платформы 1С</Typography>
                          <Typography component="dd" variant="body2">{current.platform_path}</Typography>
                          <Typography component="dt" variant="caption">Подключение 1С</Typography>
                          <Typography component="dd" variant="body2">{current.client_connection || `/S${current.server}/${current.reference}`}</Typography>
                          <Typography component="dt" variant="caption">PostgreSQL</Typography>
                          <Typography component="dd" variant="body2">{current.db_server} / {current.db_name} / {current.db_user}</Typography>
                          <Typography component="dt" variant="caption">Пользователь 1С</Typography>
                          <Typography component="dd" variant="body2">{current.infobase_user || "не указан"}</Typography>
                          <Typography component="dt" variant="caption">Пароли</Typography>
                          <Typography component="dd" variant="body2">
                            PostgreSQL: {current.db_password_set ? "сохранён" : "не задан"}; 1С: {current.infobase_password_set ? "сохранён" : "не задан"}
                          </Typography>
                        </Box>
                        <Button onClick={() => setEditingProfiles((value) => ({ ...value, [id]: true }))}>
                          Изменить параметры
                        </Button>
                      </Stack>
                    ) : (
                      <Stack spacing={1.5} mt={2}>
                        <TextField
                          label="Каталог платформы 1С"
                          value={field(
                            id,
                            "platform_path",
                            "/opt/1cv8/x86_64/8.3.27.1989",
                          )}
                          onChange={(event) =>
                            setField(id, "platform_path", event.target.value)
                          }
                        />
                        <TextField
                          label="Сервер 1С"
                          value={field(id, "server", "localhost")}
                          onChange={(event) =>
                            setField(id, "server", event.target.value)
                          }
                        />
                        <TextField
                          label="Имя базы 1С"
                          value={field(id, "reference")}
                          onChange={(event) =>
                            setField(id, "reference", event.target.value)
                          }
                        />
                        <TextField
                          label="Имя базы PostgreSQL"
                          value={field(id, "db_name")}
                          onChange={(event) =>
                            setField(id, "db_name", event.target.value)
                          }
                        />
                        <TextField
                          label="Пользователь PostgreSQL"
                          value={field(id, "db_user", "postgres")}
                          onChange={(event) =>
                            setField(id, "db_user", event.target.value)
                          }
                        />
                        <TextField
                          type="password"
                          label="Пароль PostgreSQL"
                          helperText={current?.db_password_set ? "Пароль сохранён. Оставьте поле пустым, чтобы не менять его." : ""}
                          value={field(id, "db_password")}
                          onChange={(event) =>
                            setField(id, "db_password", event.target.value)
                          }
                        />
                        <TextField
                          label="Пользователь 1С"
                          value={field(id, "infobase_user")}
                          onChange={(event) =>
                            setField(id, "infobase_user", event.target.value)
                          }
                        />
                        <TextField
                          type="password"
                          label="Пароль 1С"
                          helperText={current?.infobase_password_set ? "Пароль сохранён. Оставьте поле пустым, чтобы не менять его." : ""}
                          value={field(id, "infobase_password")}
                          onChange={(event) =>
                            setField(
                              id,
                              "infobase_password",
                              event.target.value,
                            )
                          }
                        />
                        <Stack direction="row" spacing={1}>
                          <Button disabled={busy} onClick={() => void save(id)}>
                            Проверить и сохранить профиль
                          </Button>
                          {current?.tested && (
                            <Button disabled={busy} onClick={() => setEditingProfiles((value) => ({ ...value, [id]: false }))}>
                              Отмена
                            </Button>
                          )}
                        </Stack>
                      </Stack>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </Box>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            <Button
              disabled={
                busy ||
                (sourceProfile === setup.infobases.acquisition_profile &&
                  roleEntries.every(
                    ([role, item]) =>
                      roleProfiles[role] === item.connection_profile,
                  ))
              }
              onClick={() =>
                setSourcePreview({
                  acquisition_profile: sourceProfile,
                  connection_profiles: roleProfiles,
                  comparison_epoch_changed: true,
                })
              }
            >
              Просмотреть изменения назначений
            </Button>
            <Button
              variant="contained"
              disabled={busy || !sourcePreview}
              onClick={() => void configure()}
            >
              Применить назначения
            </Button>
          </Stack>
          {sourcePreview && (
            <Alert severity="warning">
              <Typography>
                Будет начата новая эпоха сравнения; прежние DIF и MRQ станут
                устаревшими.
              </Typography>
              <Box component="pre" sx={{ whiteSpace: "pre-wrap" }}>
                {JSON.stringify(sourcePreview, null, 2)}
              </Box>
            </Alert>
          )}
        </Stack>
      </SourceSection>

      <SourceSection
        step={3}
        title="Расширения конфигурации"
        summary={
          setup.extension_scope.unreviewed.length
            ? `Требуют решения: ${setup.extension_scope.unreviewed.length}`
            : `Включено: ${setup.extension_scope.included.length}; исключено: ${setup.extension_scope.excluded.length}`
        }
        defaultExpanded={setup.extension_scope.unreviewed.length > 0}
      >
        <Stack spacing={2} id="extension-review">
          <Alert severity="info">
            Обнаружение расширения не включает его автоматически. Активность — только наблюдаемый признак. Включённое расширение участвует в сравнении; для полностью добавленного или удалённого расширения его отдельные DIF будут признаны смысловыми без вызова агента. Исключённое расширение не экспортируется и не создаёт DIF.
          </Alert>
          {setup.extension_scope.extensions.filter((row) => !row.dormant).length === 0 && (
            <Alert severity="info">В проверенных базах расширения не обнаружены.</Alert>
          )}
          {setup.extension_scope.extensions.filter((row) => !row.dormant).map((row) => {
            const value = extensionDecisions[row.uuid] || { decision: "", rationale: "" };
            const rationaleId = `extension-${row.uuid}-rationale`;
            return (
              <Card
                id={`extension-${row.uuid}`}
                key={row.uuid}
                variant="outlined"
                tabIndex={-1}
                aria-labelledby={`extension-${row.uuid}-title`}
                aria-describedby={`extension-${row.uuid}-observations extension-${row.uuid}-impact`}
              >
                <CardContent>
                  <Typography id={`extension-${row.uuid}-title`} fontWeight={700}>{row.uuid}</Typography>
                  <Box id={`extension-${row.uuid}-observations`}>
                    {Object.entries(row.roles).map(([role, observation]) => (
                      <Typography key={role} variant="body2">
                        {roleLabels[role] || role}: {observation.present
                          ? `${observation.name} ${observation.version}; ${observation.active ? "активно (информационно)" : "неактивно (информационно)"}`
                          : "отсутствует"}
                      </Typography>
                    ))}
                  </Box>
                  <Box component="label" sx={{ display: "block", mt: 1.5 }}>
                    <Typography component="span" variant="body2">Решение для расширения {row.uuid}</Typography>
                    <select
                      aria-label={`Решение для расширения ${row.uuid}`}
                      aria-describedby={`extension-${row.uuid}-impact`}
                      value={value.decision}
                      onChange={(event) => {
                        const decision = event.currentTarget.value as "" | "include" | "exclude";
                        setExtensionDecisions((current) => ({
                          ...current,
                          [row.uuid]: { decision, rationale: decision === "include" ? "" : value.rationale },
                        }));
                        setExtensionPreview(undefined);
                      }}
                      style={{ display: "block", width: "100%", marginTop: 4, padding: 8 }}
                    >
                      <option value="">Не выбрано</option>
                      <option value="include">Включить в исследование</option>
                      <option value="exclude">Исключить из исследования</option>
                    </select>
                  </Box>
                  <Typography id={`extension-${row.uuid}-impact`} variant="body2" sx={{ mt: 1 }}>
                    {value.decision === "include"
                      ? "Расширение войдёт в сравнение; при полном добавлении или удалении его DIF будут признаны смысловыми детерминированно."
                      : value.decision === "exclude"
                        ? "Расширение не будет опубликовано в поколении исходников и не создаст DIF."
                        : "Выберите включение или исключение; активность расширения не определяет решение."}
                  </Typography>
                  {value.decision === "exclude" && (
                    <TextField
                      id={rationaleId}
                      fullWidth
                      required
                      sx={{ mt: 1.5 }}
                      label={`Обоснование исключения для расширения ${row.uuid}`}
                      inputProps={{ "aria-label": `Обоснование исключения для расширения ${row.uuid}` }}
                      value={value.rationale}
                      error={!value.rationale.trim()}
                      helperText={!value.rationale.trim() ? "Укажите проверяемую причину исключения." : ""}
                      onChange={(event) => {
                        setExtensionDecisions((current) => ({
                          ...current,
                          [row.uuid]: { ...value, rationale: event.target.value },
                        }));
                        setExtensionPreview(undefined);
                      }}
                    />
                  )}
                </CardContent>
              </Card>
            );
          })}
          {setup.extension_scope.extensions.some((row) => row.dormant) && (
            <Card variant="outlined">
              <CardContent>
                <Typography fontWeight={700}>Ранее принятые решения</Typography>
                {setup.extension_scope.extensions.filter((row) => row.dormant).map((row) => (
                  <Typography key={row.uuid} variant="body2">
                    {row.uuid}: {row.decision === "include" ? "включать" : "исключать"} — сейчас не обнаружено
                  </Typography>
                ))}
              </CardContent>
            </Card>
          )}
          <Stack direction="row" spacing={1}>
            <Button disabled={busy} onClick={() => void reviewExtensions()}>Просмотреть выбор</Button>
            <Button disabled={busy || !extensionPreview} variant="contained" onClick={() => void applyExtensions()}>Применить выбор</Button>
            {extensionAction && (
              <Stack direction="row" spacing={1} alignItems="center" role="status" aria-live="polite">
                <CircularProgress size={18} />
                <Typography variant="body2">
                  {extensionAction === "review" ? "Проверяется выбор…" : "Применяется выбор…"}
                </Typography>
              </Stack>
            )}
          </Stack>
          {extensionPreview && (
            <Alert severity="warning">
              Будет начата новая эпоха сравнения.
              <Box component="pre" sx={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(extensionPreview, null, 2)}</Box>
            </Alert>
          )}
        </Stack>
      </SourceSection>

      <SourceSection
        step={4}
        title="Загруженные внешние файлы"
        summary={
          artifacts.length
            ? `${uploadedCount} из ${artifacts.length} файлов загружено`
            : "В контракте нет внешних файлов"
        }
        defaultExpanded={uploadedCount < artifacts.length}
      >
        <Stack spacing={2}>
          <ExternalFolderImport
            projectId={project.id}
            workflowFingerprint={snapshot.workflow_fingerprint}
            onComplete={() => {
              void refresh();
              refreshWorkflow();
              setRoutingPreview(undefined);
            }}
          />
          {artifacts.length === 0 && (
            <Alert severity="info">
              В отслеживаемом контракте загруженные внешние файлы не объявлены. Этот раздел предназначен для EPF, ERF, деревьев исходников и других явно объявленных файлов; расширения выбираются отдельно.
            </Alert>
          )}
          {artifacts.map((artifact) => (
            <Card
              key={`${artifact.role}:${artifact.external_artifact_id}`}
              variant="outlined"
            >
              <CardContent>
                <Typography>
                  {artifact.role} · {artifact.kind} · {artifact.semantic_key}
                </Typography>
                <Typography variant="caption">
                  {artifact.filename}, {artifact.declared_size_bytes} байт,{" "}
                  {artifact.external_artifact_id}
                </Typography>
                {!artifact.sha256 && (
                  <TextField
                    fullWidth
                    sx={{ mt: 1 }}
                    label="Объявленный SHA-256"
                    value={artifactHashes[artifact.external_artifact_id] || ""}
                    onChange={(event) =>
                      setArtifactHashes((value) => ({
                        ...value,
                        [artifact.external_artifact_id]: event.target.value,
                      }))
                    }
                  />
                )}
                {artifact.uploaded ? (
                  <Chip
                    size="small"
                    color="success"
                    sx={{ mt: 1 }}
                    label="Файл проверен и загружен"
                  />
                ) : (
                  <Button component="label" disabled={busy} sx={{ mt: 1 }}>
                    Выбрать и проверить файл
                    <input
                      hidden
                      type="file"
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) void upload(artifact, file);
                      }}
                    />
                  </Button>
                )}
              </CardContent>
            </Card>
          ))}
        </Stack>
      </SourceSection>

      <SourceSection
        step={5}
        title="Проверка и получение"
        summary={routeStatus}
        defaultExpanded
      >
        <Stack spacing={2}>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            <Button
              disabled={
                busy ||
                !ready ||
                platformRoots.size > 1 ||
                artifacts.some((item) => !item.uploaded)
              }
              onClick={() => void previewRouting()}
            >
              Проверить маршрут
            </Button>
            {routingPreview &&
              ["pending", "running"].includes(routingPreview.status) && (
                <Button onClick={() => void cancelRouting()}>Отменить</Button>
              )}
            <Button
              variant="contained"
              disabled={busy || routingPreview?.status !== "ready"}
              onClick={() => void acquire()}
            >
              Получить новое поколение
            </Button>
            {acquisitionRun?.status === "running" && (
              <Button onClick={() => void cancelAcquisition()}>
                Отменить получение
              </Button>
            )}
          </Stack>
            {routingPreview && (
              <RoutingPreviewSummary
                preview={routingPreview}
                onReviewExtension={(uuid) => {
                  const row = document.getElementById(`extension-${uuid}`);
                  row?.scrollIntoView({ block: "center" });
                  row?.focus();
                }}
              />
            )}
          {acquisitionRun?.status === "running" && (
            <Alert severity="info">
              <Stack spacing={1}>
                <Typography>
                  Получение поколения: {acquisitionRun.progress.phase}
                  {acquisitionRun.progress.subject
                    ? ` · ${acquisitionRun.progress.subject}`
                    : ""}
                </Typography>
                <LinearProgress
                  variant={
                    acquisitionRun.progress.total > 0
                      ? "determinate"
                      : "indeterminate"
                  }
                  value={
                    acquisitionRun.progress.total > 0
                      ? (100 * acquisitionRun.progress.completed) /
                        acquisitionRun.progress.total
                      : undefined
                  }
                />
                {acquisitionRun.progress.total > 0 && (
                  <Typography variant="caption">
                    Выполнено: {acquisitionRun.progress.completed} из{" "}
                    {acquisitionRun.progress.total}
                  </Typography>
                )}
              </Stack>
            </Alert>
          )}
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            <Chip
              title={String(setup.active_source.generation_id || "")}
              label={`Активное поколение: ${
                String(setup.active_source.generation_id || "нет").slice(
                  0,
                  12,
                ) || "нет"
              }`}
            />
            <Chip
              label={`Физические строки: ${
                setup.active_diff.row_counts?.["diff-inventory.csv"] ?? 0
              }`}
            />
            <Chip
              label={`Покрытие: ${
                setup.active_diff.row_counts?.["target-coverage.csv"] ?? 0
              }`}
            />
          </Stack>
          {acquisitionResult && (
            <Alert severity="success">
              <Box component="pre" sx={{ whiteSpace: "pre-wrap" }}>
                {JSON.stringify(acquisitionResult, null, 2)}
              </Box>
            </Alert>
          )}
        </Stack>
      </SourceSection>
    </Stack>
  );
}

export function Indexes({
  project,
  snapshot,
}: {
  project: Project;
  snapshot: Snapshot;
}) {
  const [items, setItems] = useState<SourceIndex[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingMode, setPendingMode] = useState<"ensure" | "rebuild" | "validate">();
  const [configurationText, setConfigurationText] = useState("");
  const [configurationFingerprint, setConfigurationFingerprint] = useState("");
  const [configurationPreview, setConfigurationPreview] = useState<Record<string, unknown>>();
  const [routeHealth, setRouteHealth] = useState<{ blockers: Record<string, unknown>[]; degraded: Record<string, unknown>[] }>({ blockers: [], degraded: [] });
  const refresh = useCallback(
    () =>
      api<{ items: SourceIndex[]; configuration: Record<string, unknown>; configuration_fingerprint: string; route_health: { blockers: Record<string, unknown>[]; degraded: Record<string, unknown>[] } }>(`/projects/${project.id}/indexes`)
        .then((value) => {
          setItems(value.items);
          setRouteHealth(value.route_health ?? { blockers: [], degraded: [] });
          setConfigurationText(JSON.stringify(value.configuration, null, 2));
          setConfigurationFingerprint(value.configuration_fingerprint);
          setConfigurationPreview(undefined);
        })
        .catch((error) => setError(error.message)),
    [project.id],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const run = async (mode: "ensure" | "rebuild" | "validate") => {
    if (
      mode === "rebuild" &&
      !window.confirm(
        `Перестроить ${selected.length || items.length} одноразовых индексов?`,
      )
    )
      return;
    setBusy(true);
    setPendingMode(mode);
    setError("");
    try {
      await api(`/projects/${project.id}/actions`, {
        method: "POST",
        headers: mutationHeaders(),
        body: JSON.stringify({
          operation: "indexes.build",
          payload: {
            mode,
            component_ids: selected.length ? selected : undefined,
            confirmed: mode === "rebuild",
          },
          expected_fingerprint: snapshot.workflow_fingerprint,
        }),
      });
      await refresh();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setPendingMode(undefined);
      setBusy(false);
    }
  };
  const previewConfiguration = async () => {
    setBusy(true);
    setError("");
    try {
      const value = await api<Record<string, unknown>>(`/projects/${project.id}/indexes/configuration-preview`, {
        method: "POST",
        body: JSON.stringify({
          configuration: JSON.parse(configurationText),
          expected_file_fingerprint: configurationFingerprint,
        }),
      });
      setConfigurationPreview(value);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const applyConfiguration = async () => {
    if (!configurationPreview) return;
    setBusy(true);
    setError("");
    try {
      await api(`/projects/${project.id}/actions`, {
        method: "POST",
        headers: mutationHeaders(),
        body: JSON.stringify({
          operation: "indexes.configure",
          payload: {
            configuration: JSON.parse(configurationText),
            expected_file_fingerprint: configurationFingerprint,
            expected_plan_fingerprint: configurationPreview.plan_fingerprint,
          },
          expected_fingerprint: snapshot.workflow_fingerprint,
        }),
      });
      await refresh();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      <Alert severity="info">
        Индексы — одноразовое ускорение в пользовательском каталоге;
        канонические доказательства остаются в репозитории.
      </Alert>
      {routeHealth.blockers.length > 0 && <Alert severity="error">
        Недоступные маршруты: {JSON.stringify(routeHealth.blockers)}
      </Alert>}
      {routeHealth.degraded.length > 0 && <Alert severity="warning">
        Деградированные маршруты: {JSON.stringify(routeHealth.degraded)}
      </Alert>}
      <Accordion>
        <AccordionSummary expandIcon={<Typography aria-hidden="true">⌄</Typography>}>
          <Typography fontWeight={700}>Настройка адаптеров и маршрутов</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={1}>
            <TextField
              label="Типизированная конфигурация"
              multiline
              minRows={10}
              value={configurationText}
              onChange={(event) => {
                setConfigurationText(event.target.value);
                setConfigurationPreview(undefined);
              }}
            />
            <Stack direction="row" spacing={1}>
              <Button disabled={busy} onClick={() => void previewConfiguration()}>Проверить изменения</Button>
              <Button variant="contained" disabled={busy || !configurationPreview} onClick={() => void applyConfiguration()}>Применить проверенный план</Button>
            </Stack>
            {configurationPreview && <Alert severity="warning">
              <Box component="pre" sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                {JSON.stringify(configurationPreview, null, 2)}
              </Box>
            </Alert>}
          </Stack>
        </AccordionDetails>
      </Accordion>
      <Stack direction="row" spacing={1}>
        <Button
          disabled={busy}
          variant="contained"
          onClick={() => void run("ensure")}
        >
          Обеспечить индексы
        </Button>
        <Button
          disabled={busy}
          onClick={() => void run("validate")}
        >
          Проверить готовность
        </Button>
        <Button
          disabled={busy}
          color="warning"
          onClick={() => void run("rebuild")}
        >
          Перестроить с подтверждением
        </Button>
      </Stack>
      {pendingMode && (
        <Stack direction="row" spacing={1} alignItems="center" role="status" aria-live="polite">
          <CircularProgress size={18} />
          <Typography variant="body2">
            {pendingMode === "ensure"
              ? "Проверяются и создаются индексы…"
              : pendingMode === "validate"
                ? "Проверяется готовность индексов…"
                : "Перестраиваются индексы…"}
          </Typography>
        </Stack>
      )}
      {items.map((item) => (
        <Card key={`${item.adapter_id}:${item.component_id}`} variant="outlined">
          <CardContent>
            <FormControlLabel
              control={
                <Checkbox
                  checked={selected.includes(item.component_id)}
                  onChange={(event) =>
                    setSelected(
                      event.target.checked
                        ? [...selected, item.component_id]
                        : selected.filter((id) => id !== item.component_id),
                    )
                  }
                />
              }
              label={item.component_id}
            />
            <Stack direction="row" spacing={1}>
              <Chip
                size="small"
                label={item.status}
                color={item.status === "ready" ? "success" : "default"}
              />
              <Chip size="small" label={`${item.bsl_file_count} BSL`} />
            </Stack>
            <Typography display="block" variant="caption">
              {item.adapter_id} {item.engine_version} · адаптер {item.adapter_version}
              {item.contract_version ? ` · контракт ${item.contract_version}` : ""} · поколение{" "}
              {item.source_generation_id}
            </Typography>
            <Typography display="block" variant="caption">
              Возможности: {item.capabilities.join(", ") || "не объявлены"} · приоритеты:{" "}
              {JSON.stringify(item.route_priorities)}
            </Typography>
            <Typography display="block" variant="caption">
              Индекс {item.index_fingerprint || "не создан"} · проверка{" "}
              {item.last_validation || "ещё не выполнялась"} · исходник {item.fingerprint}
            </Typography>
            {item.failure_code && <Alert severity="error" sx={{ mt: 1 }}>
              {item.failure_code}{item.failure_summary ? `: ${item.failure_summary}` : ""}
            </Alert>}
            {item.readiness_reason && <Typography display="block" variant="caption">
              Причина: {item.readiness_reason} · восстановление: {item.recovery_action}
            </Typography>}
          </CardContent>
        </Card>
      ))}
    </Stack>
  );
}

export function groupEvents(events: Event[]) {
  const runs: Record<
    string,
    Record<string, Record<string, Record<number, Event[]>>>
  > = {};
  for (const event of events) {
    const job = event.job_id || "run";
    const step = event.step_id || "run";
    const attempt = event.attempt || 0;
    const run = (runs[event.run_id] ||= {});
    const jobEvents = (run[job] ||= {});
    const stepEvents = (jobEvents[step] ||= {});
    (stepEvents[attempt] ||= []).push(event);
  }
  return runs;
}

export function Events({ project, target }: { project: Project; target?: JournalTarget }) {
  const [events, setEvents] = useState<Event[]>([]);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [logs, setLogs] = useState<Record<string, string[]>>({});
  const cursor = useRef(0);
  const refresh = useCallback(
    () =>
      api<{
        events: Event[];
        next_cursor: number;
        resync_required: boolean;
        earliest_sequence: number;
        snapshot?: { events: Event[] }[];
      }>(
        `/projects/${project.id}/events?cursor=${cursor.current}&limit=500&tail=${cursor.current === 0}`,
      ).then((value) => {
        const incoming = value.resync_required
          ? (value.snapshot || []).flatMap((item) => item.events || [])
          : value.events;
        setEvents((current) =>
          [
            ...new Map(
              [...current, ...incoming].map((item) => [item.sequence, item]),
            ).values(),
          ]
            .sort((a, b) => a.sequence - b.sequence)
            .slice(-500),
        );
        cursor.current = value.next_cursor;
      }),
    [project.id],
  );
  useEffect(() => {
    cursor.current = 0;
    setEvents([]);
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    if (!target) return;
    setFilter(target.invocationId || target.runId || "");
    if (target.runId) setExpanded(new Set([`run:${target.runId}`]));
  }, [target?.invocationId, target?.runId]);
  const term = filter.toLowerCase();
  const visible = events.filter(
    (event) =>
      JSON.stringify(event).toLowerCase().includes(term) ||
      (logs[`${event.run_id}:${event.attempt || 0}`] || [])
        .join("")
        .toLowerCase()
        .includes(term),
  );
  const grouped = groupEvents(visible);
  const terminalRuns = new Set(
    events
      .filter((event) => event.type === "run.finished")
      .map((event) => event.run_id),
  );
  const toggle = (key: string) =>
    setExpanded((value) => {
      const next = new Set(value);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  const loadLog = async (runId: string, attempt: number) => {
    const key = `${runId}:${attempt}`;
    const value = await api<{ lines: string[] }>(
      `/projects/${project.id}/runs/${encodeURIComponent(runId)}/attempts/${attempt}/log?offset=0&limit=500`,
    );
    setLogs((current) => ({ ...current, [key]: value.lines }));
  };
  const cancel = async (runId: string) => {
    await api(
      `/projects/${project.id}/runs/${encodeURIComponent(runId)}/cancel`,
      {
        method: "POST",
        headers: mutationHeaders(),
        body: JSON.stringify({ actor: "local-user" }),
      },
    );
  };
  return (
    <Stack spacing={2}>
      <TextField
        label="Поиск в событиях и журналах"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />
      <Typography variant="caption">
        Показано не более 500 последних событий и 500 строк журнала за запрос;
        фильтр и раскрытые узлы сохраняются при обновлении. Показываются
        наблюдаемые действия агентов; приватные рассуждения модели недоступны.
      </Typography>
      {Object.entries(grouped).map(([runId, run]) => {
        const runKey = `run:${runId}`;
        const running = !terminalRuns.has(runId);
        const failure = Object.values(run)
          .flatMap((job) => Object.values(job))
          .flatMap((step) => Object.values(step))
          .flat()
          .reverse()
          .map((event) => {
            const exit = event.payload.exit as Record<string, unknown> | undefined;
            return (exit?.blocker || event.payload.blocker) as Blocker | undefined;
          })
          .find((blocker) => blocker?.message);
        return (
          <Card variant="outlined" key={runId}>
            <CardContent>
              <Stack direction="row" justifyContent="space-between">
                <Box
                  component="button"
                  onClick={() => toggle(runKey)}
                  aria-expanded={expanded.has(runKey)}
                  sx={{
                    border: 0,
                    background: "none",
                    p: 0,
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  <Typography variant="h6">
                    {expanded.has(runKey) ? "▼" : "▶"} Запуск {runId}
                  </Typography>
                </Box>
                {running && (
                  <Button color="warning" onClick={() => void cancel(runId)}>
                    Отменить запуск
                  </Button>
                )}
              </Stack>
              {failure && <Alert severity="error" sx={{ mt: 1 }}>{failure.code}: {failure.message}</Alert>}
              {expanded.has(runKey) && (
                <Stack spacing={1} mt={1}>
                  {Object.entries(run).map(([jobId, job]) => {
                    const jobKey = `${runKey}:${jobId}`;
                    return (
                      <Box key={jobId} ml={2}>
                        <Button
                          size="small"
                          onClick={() => toggle(jobKey)}
                          aria-expanded={expanded.has(jobKey)}
                        >
                          {expanded.has(jobKey) ? "▼" : "▶"} Задание {jobId}
                        </Button>
                        {expanded.has(jobKey) &&
                          Object.entries(job).map(([stepId, step]) => {
                            const stepKey = `${jobKey}:${stepId}`;
                            return (
                              <Box key={stepId} ml={2}>
                                <Button
                                  size="small"
                                  onClick={() => toggle(stepKey)}
                                  aria-expanded={expanded.has(stepKey)}
                                >
                                  {expanded.has(stepKey) ? "▼" : "▶"} Шаг{" "}
                                  {stepId}
                                </Button>
                                {expanded.has(stepKey) &&
                                  Object.entries(step).map(
                                    ([attemptText, attemptEvents]) => {
                                      const attempt = Number(attemptText);
                                      const logKey = `${runId}:${attempt}`;
                                      const last =
                                        attemptEvents[attemptEvents.length - 1];
                                      const diffGeneration = String(
                                        last.payload.diff_generation_id || "",
                                      );
                                      return (
                                        <Card
                                          key={attemptText}
                                          variant="outlined"
                                          sx={{ ml: 2, mb: 1 }}
                                        >
                                          <CardContent>
                                            <Stack
                                              direction="row"
                                              justifyContent="space-between"
                                            >
                                              <Typography variant="subtitle2">
                                                Попытка {attempt || "—"} ·{" "}
                                                {String(
                                                  last.payload.status ||
                                                    "running",
                                                )}
                                              </Typography>
                                              <Typography variant="caption">
                                                {last.timestamp}
                                              </Typography>
                                            </Stack>
                                            <Typography variant="caption">
                                              Исполнитель:{" "}
                                              {String(
                                                last.payload.actor ||
                                                  last.payload.agent_profile ||
                                                  "—",
                                              )}{" "}
                                              · единица:{" "}
                                              {String(
                                                last.payload.work_unit_id ||
                                                  "—",
                                              )}{" "}
                                              · длительность:{" "}
                                              {String(
                                                last.payload.duration_seconds ??
                                                  "—",
                                              )}
                                            </Typography>
                                            <Stack direction="row" spacing={1}>
                                              {attempt > 0 && (
                                                <Button
                                                  size="small"
                                                  onClick={() =>
                                                    void loadLog(runId, attempt)
                                                  }
                                                >
                                                  Загрузить журнал
                                                </Button>
                                              )}
                                              {/^[0-9a-f]{64}$/.test(
                                                diffGeneration,
                                              ) && (
                                                <Button
                                                  size="small"
                                                  component="a"
                                                  href={`/api/v1/projects/${project.id}/artifacts/analysis/indexes/generations/${diffGeneration}/diff-inventory.csv`}
                                                >
                                                  Различия репозитория
                                                </Button>
                                              )}
                                            </Stack>
                                            {logs[logKey] && (
                                              <Box
                                                component="pre"
                                                sx={{
                                                  maxHeight: 320,
                                                  overflow: "auto",
                                                  whiteSpace: "pre-wrap",
                                                }}
                                              >
                                                {logs[logKey].join("")}
                                              </Box>
                                            )}
                                            {attemptEvents.map((event) => (
                                              <Box key={event.sequence} mt={1}>
                                                <Typography variant="caption">
                                                  #{event.sequence} ·{" "}
                                                  {event.type}
                                                </Typography>
                                                <Box
                                                  component="pre"
                                                  sx={{
                                                    whiteSpace: "pre-wrap",
                                                    overflowWrap: "anywhere",
                                                  }}
                                                >
                                                  {JSON.stringify(
                                                    event.payload,
                                                    null,
                                                    2,
                                                  )}
                                                </Box>
                                              </Box>
                                            ))}
                                          </CardContent>
                                        </Card>
                                      );
                                    },
                                  )}
                              </Box>
                            );
                          })}
                      </Box>
                    );
                  })}
                </Stack>
              )}
            </CardContent>
          </Card>
        );
      })}
    </Stack>
  );
}

export function Registry({ project, target }: { project: Project; target?: RegistryTarget }) {
  const [name, setName] = useState(target?.registry || "diff-inventory");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{
    diff_generation_id?: string;
    items: Record<string, unknown>[];
    has_more: boolean;
  }>({ items: [], has_more: false });
  const generation = useRef("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!target) return;
    setName(target.registry);
    setOffset(0);
    generation.current = "";
  }, [target?.registry, target?.itemId]);
  useEffect(() => {
    const expected =
      offset > 0 && generation.current
        ? `&expected_generation=${generation.current}`
        : "";
    const item = target?.itemId ? `&item_id=${encodeURIComponent(target.itemId)}` : "";
    api<typeof page>(
      `/projects/${project.id}/registries/${name}?offset=${offset}&limit=100${expected}${item}`,
    )
      .then((value) => {
        generation.current = value.diff_generation_id || "";
        setPage(value);
        setError("");
      })
      .catch((error) => {
        setPage({ items: [], has_more: false });
        setError(error.message);
      });
  }, [project.id, name, offset, target?.itemId]);
  return (
    <Stack spacing={2}>
      {error && <Alert severity="warning">{error}</Alert>}
      <FormControl>
        <InputLabel id="registry-label">Реестр</InputLabel>
        <Select
          labelId="registry-label"
          label="Реестр"
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            setOffset(0);
            generation.current = "";
          }}
        >
          {[
            "diff-inventory",
            "target-coverage",
            "extension-diff",
            "extension-dependencies",
            "extension-path-coverage",
            "extension-physical-diff",
            "mrq",
          ].map((item) => (
            <MenuItem key={item} value={item}>
              {item}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      <Typography variant="caption">
        Строки {offset + 1}–{offset + page.items.length}; размер страницы 100.
        {page.diff_generation_id &&
          ` Поколение различий: ${page.diff_generation_id}.`}
      </Typography>
      {page.items.length === 0 && (
        <Alert severity="info">В выбранном реестре нет записей.</Alert>
      )}
      {page.items.map((item, index) => (
        <Card
          variant="outlined"
          key={String(item.stable_diff_id || item.mrq_id || index)}
        >
          <CardContent>
            <Box
              component="pre"
              aria-label={`Запись реестра ${String(item.stable_diff_id || item.mrq_id || index + 1)}`}
              sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
            >
              {JSON.stringify(item, null, 2)}
            </Box>
          </CardContent>
        </Card>
      ))}
      <Stack direction="row" spacing={1}>
        <Button
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 100))}
        >
          Назад
        </Button>
        <Button
          disabled={!page.has_more}
          onClick={() => setOffset(offset + 100)}
        >
          Далее
        </Button>
      </Stack>
    </Stack>
  );
}

function Workspace({
  project,
  close,
}: {
  project: Project;
  close: () => void;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [view, setView] = useState<
    "dispatcher" | "react-flow-example" | "enriched-reference" | "sources" | "indexes" | "settings" | "journal" | "registries"
  >("dispatcher");
  const [settingsTab, setSettingsTab] = useState<"stages" | "profiles">("stages");
  const [settingsStep, setSettingsStep] = useState<string>();
  const [journalTarget, setJournalTarget] = useState<JournalTarget>();
  const [registryTarget, setRegistryTarget] = useState<RegistryTarget>();
  const [sourceExtensionTarget, setSourceExtensionTarget] = useState<string>();
  const [error, setError] = useState("");
  const [retryWorkingView, setRetryWorkingView] = useState(false);
  const fetchSnapshot = useCallback(
    () => api<Snapshot>(`/projects/${project.id}/workflow`),
    [project.id],
  );
  const refresh = useCallback(
    () =>
      fetchSnapshot()
        .then(setSnapshot)
        .catch((error) => setError(error.message)),
    [fetchSnapshot],
  );
  useEffect(() => {
    refresh();
  }, [refresh]);
  const working = view === "dispatcher";
  const openWorking = async () => {
    if (working) {
      return;
    }
    try {
      const next = await fetchSnapshot();
      setSnapshot(next);
      setError("");
      setRetryWorkingView(false);
      setView("dispatcher");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось обновить рабочий снимок");
      setRetryWorkingView(true);
    }
  };
  return (
    <>
      <AppBar position="static" color="inherit" elevation={1}>
        <Toolbar variant="dense" sx={{ minHeight: 56 }}>
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="h6" fontWeight={750}>
              Диспетчер исследования
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {project.name}
            </Typography>
          </Box>
          <Button
            variant={view === "dispatcher" ? "contained" : "text"}
            onClick={() => void openWorking()}
          >
            Диспетчер
          </Button>
          <Button onClick={() => setView("sources")}>Источники</Button>
          <Button onClick={() => setView("journal")}>Журнал</Button>
          <Button onClick={() => setView("registries")}>Реестры</Button>
          <Button onClick={() => { setSettingsStep(undefined); setSettingsTab("stages"); setView("settings"); }}>
            Профили и параметры
          </Button>
          <Button color="inherit" onClick={close}>
            Другой проект
          </Button>
        </Toolbar>
      </AppBar>
      <Box sx={{ px: working ? 1.5 : 3, py: 1.5 }}>
        {error && <Alert severity="error" action={retryWorkingView
          ? <Button color="inherit" onClick={() => void openWorking()}>Повторить снимок</Button>
          : undefined}>{error}</Alert>}
        {view === "react-flow-example" ? (
          <OfficialSubFlowReference />
        ) : view === "enriched-reference" ? (
          <EnrichedSubFlowReference />
        ) : !snapshot ? (
          <CircularProgress />
        ) : (
          <>
            {working && (
              <PipelineDispatcher
                projectId={project.id}
                initialProjection={snapshot.dispatcher}
                initialFingerprint={snapshot.workflow_fingerprint}
                onOpenSources={() => {
                  setSourceExtensionTarget(snapshot.extension_scope_blockers?.[0]?.uuid);
                  setView("sources");
                }}
                onOpenIndexes={() => setView("indexes")}
                onOpenSettings={(stepId) => { setSettingsStep(stepId); setSettingsTab("stages"); setView("settings"); }}
                onOpenJournal={(target) => { setJournalTarget(target); setView("journal"); }}
                onOpenRegistry={(target) => { setRegistryTarget(target); setView("registries"); }}
              />
            )}
            {view === "sources" && (
              <Sources
                project={project}
                snapshot={snapshot}
                refreshWorkflow={refresh}
                initialExtensionUuid={sourceExtensionTarget}
              />
            )}
            {view === "indexes" && (
              <Indexes project={project} snapshot={snapshot} />
            )}
            {view === "settings" && (
              <Stack spacing={2}>
                <Box>
                  <Typography variant="h5" fontWeight={750}>Профили и параметры</Typography>
                  <Typography color="text.secondary">Настройка этапов процесса и профилей агентов</Typography>
                </Box>
                <Tabs value={settingsTab} onChange={(_event, value) => setSettingsTab(value)} aria-label="Разделы настроек">
                  <Tab value="stages" label="Этапы" />
                  <Tab value="profiles" label="Профили" />
                </Tabs>
                {settingsTab === "stages" ? (
                  <WorkflowEditor project={project} snapshot={snapshot} refresh={refresh} initialStepId={settingsStep} />
                ) : (
                  <AgentProfiles project={project} />
                )}
              </Stack>
            )}
            {view === "journal" && <Events project={project} target={journalTarget} />}
            {view === "registries" && <Registry project={project} target={registryTarget} />}
          </>
        )}
      </Box>
    </>
  );
}

export function App() {
  const [project, setProject] = useState<Project>();
  return (
    <>
      <CssBaseline />
      {project ? (
        <Workspace project={project} close={() => setProject(undefined)} />
      ) : (
        <ProjectPicker onSelect={setProject} />
      )}
    </>
  );
}
