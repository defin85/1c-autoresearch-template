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
  MenuItem,
  Select,
  Stack,
  TextField,
  Toolbar,
  Typography,
} from "@mui/material";
import { api, mutationHeaders } from "./api";
import { PipelineDispatcher } from "./dispatcher/PipelineDispatcher";
import { ExternalFolderImport } from "./ExternalFolderImport";
import type { DispatcherProjection } from "./dispatcher/projection";

type Blocker = { code: string; message: string; action: string };
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
  reasoning_effort: "low" | "medium" | "high" | "xhigh";
  instructions_version: number;
};
type RunResult = {
  run_id: string;
  result: string;
  proposal?: { payload: Record<string, unknown> };
  blocker?: Blocker;
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
  external_artifacts: { artifacts: ExternalArtifact[] };
  upload_draft_fingerprint: string;
  connection_profiles: Record<
    string,
    {
      available: boolean;
      tested: boolean;
      profile_id: string;
      platform_path: string;
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
  index_key: string;
  engine: string;
  engine_version: string;
  bsl_file_count: number;
  status: string;
  last_validation?: string;
};
type StepConfiguration = {
  job_id: string;
  step: {
    id: string;
    operation: string;
    timeout_seconds: number;
    max_retries?: number;
    agent_profile?: string;
    instruction_supplement?: string;
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
  status: "pending" | "running" | "ready" | "failed" | "cancelled";
  routing_plan_fingerprint: string;
  routing_manifest?: { groups: RoutingGroup[] };
  required_tools?: string[];
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
}: {
  preview: RoutingPreview;
}) {
  return (
    <>
      <Alert severity={preview.status === "ready" ? "success" : "info"}>
        Состояние: {preview.status}. Требуемые инструменты:{" "}
        {preview.required_tools?.join(", ") || "не определены"}.
      </Alert>
      {preview.status === "failed" && (
        <Alert severity="error">
          {preview.error || "Не удалось построить маршрут."}
        </Alert>
      )}
      {preview.routing_manifest?.groups.map((group) => (
        <Card key={group.routing_group_id} variant="outlined">
          <CardContent>
            <Typography variant="subtitle2">
              {group.routing_group_id}
            </Typography>
            <Typography>
              Управляемые: {group.form_counts.managed}; обычные:{" "}
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
        routingPreview?.routing_manifest?.groups.some(
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
  const refresh = () => {
    void api<Project[]>("/projects")
      .then(setProjects)
      .catch((error) => setError(error.message));
  };
  useEffect(refresh, []);
  const save = async () => {
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
          <Button variant="contained" onClick={save}>
            Открыть
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

function ActionPanel({
  project,
  snapshot,
  refresh,
}: {
  project: Project;
  snapshot: Snapshot;
  refresh: () => void;
}) {
  const [next, setNext] = useState<{
    action: string;
    blocker?: { message: string };
  } | null>();
  const [result, setResult] = useState<RunResult>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(
    () =>
      api<typeof next>(`/projects/${project.id}/workflow/next`)
        .then(setNext)
        .catch((error) => setError(error.message)),
    [project.id],
  );
  useEffect(() => {
    void load();
  }, [load]);
  const run = async () => {
    const agentApproval = result?.blocker?.code === "approval.agent_proposal";
    const approvals = agentApproval
      ? [next?.action]
      : next?.action === "sources.acquire" &&
          window.confirm(
            "Получить и опубликовать полное новое поколение трёх баз?",
          )
        ? ["sources.acquire"]
        : [];
    setBusy(true);
    setError("");
    try {
      const value = await api<RunResult>(
        `/projects/${project.id}/workflow/run-next`,
        {
          method: "POST",
          headers: mutationHeaders(),
          body: JSON.stringify({
            expected_fingerprint: snapshot.workflow_fingerprint,
            approved_operations: approvals,
          }),
        },
      );
      setResult(value);
      refresh();
      await load();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="h6" mb={2}>
          Следующее типизированное действие
        </Typography>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Stack spacing={2}>
          {next ? (
            <>
              <Typography component="code">{next.action}</Typography>
              {next.blocker && (
                <Alert severity="info">{next.blocker.message}</Alert>
              )}
              {next.action === "sources.acquire" && (
                <Alert severity="info">
                  Откройте раздел «Источники», проверьте маршрут и подтвердите
                  получение там.
                </Alert>
              )}
              {result?.proposal && (
                <Alert severity="warning">
                  <Typography mb={1}>
                    Предложение агента сформировано и ещё не изменило
                    репозиторий.
                  </Typography>
                  <Box
                    component="pre"
                    sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {JSON.stringify(result.proposal.payload, null, 2)}
                  </Box>
                </Alert>
              )}
              <Button
                disabled={busy || next.action === "sources.acquire"}
                color={
                  result?.blocker?.code === "approval.agent_proposal"
                    ? "warning"
                    : "primary"
                }
                variant="contained"
                onClick={() => void run()}
              >
                {result?.blocker?.code === "approval.agent_proposal"
                  ? "Одобрить сохранённое предложение"
                  : "Выполнить следующий шаг"}
              </Button>
            </>
          ) : (
            <Alert severity="success">
              Готовых действий нет: процесс завершен.
            </Alert>
          )}
          <Typography variant="caption">
            Новый результат агента всегда требует отдельного подтверждения.
            Произвольные команды и замена канонического файла не принимаются.
          </Typography>
        </Stack>
      </CardContent>
    </Card>
  );
}

function AgentProfiles({ project }: { project: Project }) {
  const [items, setItems] = useState<Record<string, AgentProfile>>({});
  const [profileId, setProfileId] = useState("local");
  const [model, setModel] = useState("gpt-5.6-sol");
  const [reasoning, setReasoning] =
    useState<AgentProfile["reasoning_effort"]>("low");
  const [version, setVersion] = useState(1);
  const [error, setError] = useState("");
  const load = useCallback(
    () =>
      api<{ items: Record<string, AgentProfile> }>(
        `/projects/${project.id}/agent-profiles`,
      )
        .then((value) => setItems(value.items))
        .catch((error) => setError(error.message)),
    [project.id],
  );
  useEffect(() => {
    void load();
  }, [load]);
  const save = async () => {
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
          },
        }),
      });
      await load();
    } catch (error) {
      setError((error as Error).message);
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
        <Stack spacing={2}>
          <Typography variant="caption">
            Профили хранятся только в пользовательском каталоге и не являются
            исследовательскими решениями.
          </Typography>
          {Object.entries(items).map(([id, profile]) => (
            <Alert key={id} severity="success">
              {id}: {profile.model}, рассуждение {profile.reasoning_effort},
              версия инструкций {profile.instructions_version}
            </Alert>
          ))}
          <TextField
            label="Идентификатор профиля"
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
          />
          <TextField
            label="Модель"
            value={model}
            onChange={(event) => setModel(event.target.value)}
          />
          <FormControl>
            <InputLabel>Уровень рассуждения</InputLabel>
            <Select
              label="Уровень рассуждения"
              value={reasoning}
              onChange={(event) =>
                setReasoning(
                  event.target.value as AgentProfile["reasoning_effort"],
                )
              }
            >
              {["low", "medium", "high", "xhigh"].map((value) => (
                <MenuItem key={value} value={value}>
                  {value}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <TextField
            type="number"
            label="Версия инструкций"
            value={version}
            onChange={(event) => setVersion(Number(event.target.value))}
            inputProps={{ min: 1 }}
          />
          <Button variant="contained" onClick={() => void save()}>
            Проверить и сохранить профиль
          </Button>
        </Stack>
      </CardContent>
    </Card>
  );
}

function WorkflowEditor({
  project,
  snapshot,
  refresh,
}: {
  project: Project;
  snapshot: Snapshot;
  refresh: () => void;
}) {
  const [configuration, setConfiguration] = useState<WorkflowConfiguration>();
  const [selected, setSelected] = useState("");
  const [timeout, setTimeoutValue] = useState(1800);
  const [retries, setRetries] = useState(0);
  const [profile, setProfile] = useState("local");
  const [supplement, setSupplement] = useState("");
  const [preview, setPreview] = useState<Record<string, unknown>>();
  const [error, setError] = useState("");
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
          setSelected((previous) => previous || value.steps[0]?.step.id || "");
        })
        .catch((error) => setError(error.message)),
    [project.id],
  );
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    void api<{ items: Record<string, AgentProfile> }>(
      `/projects/${project.id}/agent-profiles`,
    ).then((value) => setAgentProfiles(value.items));
  }, [project.id]);
  const current = configuration?.steps.find(
    (item) => item.step.id === selected,
  );
  useEffect(() => {
    if (current) {
      setTimeoutValue(current.step.timeout_seconds);
      setRetries(current.step.max_retries || 0);
      setProfile(current.step.agent_profile || "local");
      setSupplement(current.step.instruction_supplement || "");
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
    ...(current?.step.operation.startsWith("mrq.")
      ? { agent_profile: profile, instruction_supplement: supplement }
      : {}),
  });
  const showPreview = async () => {
    try {
      setError("");
      setPreview(
        await api(`/projects/${project.id}/workflow/patch-preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            step_id: selected,
            parameters: parameters(),
            expected_manifest_fingerprint: configuration?.manifest_fingerprint,
          }),
        }),
      );
    } catch (error) {
      setError((error as Error).message);
    }
  };
  const apply = async () => {
    try {
      setError("");
      await api(`/projects/${project.id}/actions`, {
        method: "POST",
        headers: mutationHeaders(),
        body: JSON.stringify({
          operation: "workflow.patch-step",
          payload: {
            step_id: selected,
            parameters: parameters(),
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
    }
  };
  if (!configuration || !current) return <CircularProgress />;
  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="h6" mb={2}>
          Параметры фиксированного шага
        </Typography>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Stack spacing={2}>
          <FormControl>
            <InputLabel>Шаг</InputLabel>
            <Select
              label="Шаг"
              value={selected}
              onChange={(event) => setSelected(event.target.value)}
            >
              {configuration.steps.map((item) => (
                <MenuItem key={item.step.id} value={item.step.id}>
                  {item.job_id} · {item.step.id}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <Alert severity="info">
            Операция {current.step.operation}; исполнитель{" "}
            {current.catalog.executor}; воздействие {current.catalog.effect}
            {current.catalog.approval_required
              ? "; требуется подтверждение"
              : ""}
            .
          </Alert>
          <TextField
            type="number"
            label="Предельное время, секунд"
            value={timeout}
            onChange={(event) => setTimeoutValue(Number(event.target.value))}
            inputProps={{ min: 30, max: 86400 }}
          />
          {["diff.build", "projections.build"].includes(
            current.step.operation,
          ) && (
            <TextField
              type="number"
              label="Повторные попытки"
              value={retries}
              onChange={(event) => setRetries(Number(event.target.value))}
              inputProps={{ min: 0, max: 1 }}
            />
          )}
          {current.step.operation.startsWith("mrq.") && (
            <>
              <FormControl error={!agentProfiles[profile]}>
                <InputLabel>Профиль агента</InputLabel>
                <Select
                  label="Профиль агента"
                  value={profile}
                  onChange={(event) => setProfile(event.target.value)}
                >
                  {Object.keys(agentProfiles).map((value) => (
                    <MenuItem key={value} value={value}>
                      {value}
                    </MenuItem>
                  ))}
                  {!agentProfiles[profile] && (
                    <MenuItem value={profile}>{profile} — отсутствует</MenuItem>
                  )}
                </Select>
              </FormControl>
              {!agentProfiles[profile] && (
                <Alert severity="warning">
                  Создайте профиль агента перед запуском шага.
                </Alert>
              )}
              <TextField
                multiline
                minRows={3}
                label="Дополнительная инструкция"
                value={supplement}
                onChange={(event) => setSupplement(event.target.value)}
                inputProps={{ maxLength: 4000 }}
              />
            </>
          )}
          <Stack direction="row" spacing={1}>
            <Button onClick={() => void showPreview()}>
              Предварительный просмотр
            </Button>
            <Button
              disabled={!preview}
              variant="contained"
              onClick={() => void apply()}
            >
              Применить просмотренное изменение
            </Button>
          </Stack>
          {preview && (
            <Box
              component="pre"
              sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
            >
              {JSON.stringify(preview, null, 2)}
            </Box>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}

function Sources({
  project,
  snapshot,
  refreshWorkflow,
}: {
  project: Project;
  snapshot: Snapshot;
  refreshWorkflow: () => void;
}) {
  const [setup, setSetup] = useState<SourceSetup>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [profiles, setProfiles] = useState<
    Record<string, Record<string, string>>
  >({});
  const [artifactHashes, setArtifactHashes] = useState<Record<string, string>>(
    {},
  );
  const [sourceProfile, setSourceProfile] = useState("");
  const [roleProfiles, setRoleProfiles] = useState<Record<string, string>>({});
  const [sourcePreview, setSourcePreview] = useState<Record<string, unknown>>();
  const [routingPreview, setRoutingPreview] = useState<RoutingPreview>();
  const [acquisitionResult, setAcquisitionResult] =
    useState<Record<string, unknown>>();
  const refresh = useCallback(
    () =>
      api<SourceSetup>(`/projects/${project.id}/source-setup`)
        .then((value) => {
          setSetup(value);
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
        .catch((error) => setError(error.message)),
    [project.id],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);
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
    } catch (error) {
      setError((error as Error).message);
    } finally {
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
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const current = await api<RoutingPreview>(
          `/projects/${project.id}/source-routing-previews/${created.preview_id}`,
        );
        setRoutingPreview(current);
        if (!["pending", "running"].includes(current.status)) return;
        await new Promise((resolve) => window.setTimeout(resolve, 250));
      }
      setError("Предварительный просмотр не завершился в установленное время.");
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
      const result = await api<Record<string, unknown>>(
        `/projects/${project.id}/workflow/run-next`,
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
      setAcquisitionResult(result);
      await refresh();
      refreshWorkflow();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
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
                    {current?.tested ? (
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
                          value={field(id, "infobase_password")}
                          onChange={(event) =>
                            setField(
                              id,
                              "infobase_password",
                              event.target.value,
                            )
                          }
                        />
                        <Button disabled={busy} onClick={() => void save(id)}>
                          Проверить и сохранить профиль
                        </Button>
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
        title="Внешние артефакты"
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
              В отслеживаемом контракте внешние артефакты не объявлены.
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
        step={4}
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
          </Stack>
          {routingPreview && <RoutingPreviewSummary preview={routingPreview} />}
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

function Indexes({
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
  const refresh = useCallback(
    () =>
      api<{ items: SourceIndex[] }>(`/projects/${project.id}/indexes`)
        .then((value) => setItems(value.items))
        .catch((error) => setError(error.message)),
    [project.id],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const run = async (mode: "ensure" | "rebuild") => {
    if (
      mode === "rebuild" &&
      !window.confirm(
        `Перестроить ${selected.length || items.length} одноразовых индексов?`,
      )
    )
      return;
    setBusy(true);
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
          color="warning"
          onClick={() => void run("rebuild")}
        >
          Перестроить с подтверждением
        </Button>
      </Stack>
      {items.map((item) => (
        <Card key={item.component_id} variant="outlined">
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
              {item.engine} {item.engine_version} · поколение{" "}
              {item.source_generation_id}
            </Typography>
            <Typography display="block" variant="caption">
              Ключ {item.index_key} · проверка{" "}
              {item.last_validation || "ещё не выполнялась"} · исходник{" "}
              {item.fingerprint}
            </Typography>
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

function Events({ project }: { project: Project }) {
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
        `/projects/${project.id}/events?cursor=${cursor.current}&limit=500`,
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

function Registry({ project }: { project: Project }) {
  const [name, setName] = useState("diff-inventory");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{
    items: Record<string, unknown>[];
    has_more: boolean;
  }>({ items: [], has_more: false });
  const [error, setError] = useState("");
  useEffect(() => {
    api<typeof page>(
      `/projects/${project.id}/registries/${name}?offset=${offset}&limit=100`,
    )
      .then(setPage)
      .catch((error) => {
        setPage({ items: [], has_more: false });
        setError(error.message);
      });
  }, [project.id, name, offset]);
  return (
    <Stack spacing={2}>
      {error && <Alert severity="warning">{error}</Alert>}
      <FormControl>
        <InputLabel>Реестр</InputLabel>
        <Select
          label="Реестр"
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            setOffset(0);
          }}
        >
          {["diff-inventory", "target-coverage", "mrq"].map((item) => (
            <MenuItem key={item} value={item}>
              {item}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      <Typography variant="caption">
        Строки {offset + 1}–{offset + page.items.length}; размер страницы 100.
      </Typography>
      {page.items.map((item, index) => (
        <Card
          variant="outlined"
          key={String(item.stable_diff_id || item.mrq_id || index)}
        >
          <CardContent>
            <Box
              component="pre"
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
    "dispatcher" | "sources" | "indexes" | "settings" | "journal" | "registries"
  >("dispatcher");
  const [error, setError] = useState("");
  const refresh = useCallback(
    () =>
      api<Snapshot>(`/projects/${project.id}/workflow`)
        .then(setSnapshot)
        .catch((error) => setError(error.message)),
    [project.id],
  );
  useEffect(() => {
    refresh();
  }, [refresh]);
  const dispatcher = () => setView("dispatcher");
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
          {view !== "dispatcher" && (
            <Button onClick={dispatcher}>Диспетчер</Button>
          )}
          <Button onClick={() => setView("sources")}>Источники</Button>
          <Button onClick={() => setView("journal")}>Журнал</Button>
          <Button onClick={() => setView("registries")}>Реестры</Button>
          <Button color="inherit" onClick={close}>
            Другой проект
          </Button>
        </Toolbar>
      </AppBar>
      <Box sx={{ px: view === "dispatcher" ? 1.5 : 3, py: 1.5 }}>
        {error && <Alert severity="error">{error}</Alert>}
        {!snapshot ? (
          <CircularProgress />
        ) : (
          <>
            {view === "dispatcher" && (
              <PipelineDispatcher
                projectId={project.id}
                initialProjection={snapshot.dispatcher}
                initialFingerprint={snapshot.workflow_fingerprint}
                onOpenSources={() => setView("sources")}
                onOpenIndexes={() => setView("indexes")}
                onOpenSettings={() => setView("settings")}
                onOpenJournal={() => setView("journal")}
                onOpenRegistries={() => setView("registries")}
              />
            )}
            {view === "sources" && (
              <Sources
                project={project}
                snapshot={snapshot}
                refreshWorkflow={refresh}
              />
            )}
            {view === "indexes" && (
              <Indexes project={project} snapshot={snapshot} />
            )}
            {view === "settings" && (
              <Stack spacing={2}>
                <ActionPanel
                  project={project}
                  snapshot={snapshot}
                  refresh={refresh}
                />
                <AgentProfiles project={project} />
                <WorkflowEditor
                  project={project}
                  snapshot={snapshot}
                  refresh={refresh}
                />
              </Stack>
            )}
            {view === "journal" && <Events project={project} />}
            {view === "registries" && <Registry project={project} />}
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
