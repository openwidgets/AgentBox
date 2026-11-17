"use client";

import { useMemo, useState, type FormEvent } from "react";
import {
  AgentBoxApiError,
  AgentBoxBffClient,
  type AgentVersionRecord,
} from "@/lib/agentbox-api";

const api = new AgentBoxBffClient();

const starterManifest = {
  schema_version: "1.0.0",
  agent_id: "sample-support-agent",
  version: "1.0.0",
  name: "Support assistant",
  description: "A governed support assistant agent.",
  purpose: "Answer support questions using approved knowledge and tools.",
  behavior: {
    objective: "Provide accurate, concise support answers and ask for clarification when needed.",
    instructions_ref: "agentbox://instructions/support-assistant",
    max_steps: 8,
    timeout_seconds: 120,
  },
  model_binding: {
    provider_id: "provider.openai",
    model_id: "gpt-4o-mini",
  },
  tools: [],
  permissions: { tool_scopes: [], data_scopes: [] },
  provenance: {
    origin: "authored",
    created_at: "2026-11-16T00:00:00Z",
  },
  runtime_targets: ["server"],
  tags: ["starter"],
};

function errorMessage(error: unknown): string {
  if (error instanceof AgentBoxApiError) {
    const detail = error.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "detail" in detail) {
      return String((detail as { detail: unknown }).detail);
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "Unexpected error";
}

function JsonPanel({ value }: { value: unknown }) {
  return <pre className="json-panel">{JSON.stringify(value, null, 2)}</pre>;
}

export default function Workspace() {
  const [manifestText, setManifestText] = useState(
    JSON.stringify(starterManifest, null, 2),
  );
  const [agentId, setAgentId] = useState("");
  const [versions, setVersions] = useState<AgentVersionRecord[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<AgentVersionRecord | null>(null);
  const [executionId, setExecutionId] = useState("");
  const [execution, setExecution] = useState<Record<string, unknown> | null>(null);
  const [jobVersion, setJobVersion] = useState("1.0.0");
  const [jobInput, setJobInput] = useState('{"message":"Hello from AgentBox"}');
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState<Record<string, unknown> | null>(null);
  const [jobEvents, setJobEvents] = useState<Record<string, unknown>[]>([]);
  const [created, setCreated] = useState<AgentVersionRecord | null>(null);
  const [profileId, setProfileId] = useState("support-quality");
  const [profileVersion, setProfileVersion] = useState("1.0.0");
  const [profileText, setProfileText] = useState(JSON.stringify({
    schema_version: "1.0.0",
    profile_id: "support-quality",
    version: "1.0.0",
    name: "Support response quality",
    description: "Deterministic checks for support agent outputs.",
    scoring: { correctness_weight: 0.7, latency_weight: 0.2, cost_weight: 0.1 },
    gates: { min_case_pass_rate: 0.9, max_avg_latency_ms: 3000, max_total_cost_usd: 0.05 },
    cases: [{ case_id: "greeting", description: "Response contains a greeting", input: { message: "Hello" }, assertions: [{ type: "contains", expected_value: "Hello" }] }]
  }, null, 2));
  const [profileVersions, setProfileVersions] = useState<Record<string, unknown>[]>([]);
  const [evaluationResultsText, setEvaluationResultsText] = useState('[{"case_id":"greeting","output":"Hello! How can I help?","latency_ms":420,"cost_usd":0.001}]');
  const [evaluationRuns, setEvaluationRuns] = useState<Record<string, unknown>[]>([]);
  const [evaluationRun, setEvaluationRun] = useState<Record<string, unknown> | null>(null);
  const [evaluationRunId, setEvaluationRunId] = useState("");
  const [cognitiveGoalRef, setCognitiveGoalRef] = useState("goal://support/triage");
  const [cognitiveAgentRef, setCognitiveAgentRef] = useState("");
  const [cognitiveRunId, setCognitiveRunId] = useState("");
  const [cognitiveRun, setCognitiveRun] = useState<Record<string, unknown> | null>(null);
  const [cognitiveEvents, setCognitiveEvents] = useState<Record<string, unknown>[]>([]);
  const [contextSearch, setContextSearch] = useState("");
  const [contextSnapshot, setContextSnapshot] = useState<Record<string, unknown> | null>(null);
  const [experienceAgentId, setExperienceAgentId] = useState("");
  const [experienceExecutionId, setExperienceExecutionId] = useState("");
  const [experienceEvaluationId, setExperienceEvaluationId] = useState("");
  const [experienceWorkloadRef, setExperienceWorkloadRef] = useState("benchmark://support-quality/v1");
  const [experienceWorkloadHash, setExperienceWorkloadHash] = useState("");
  const [experienceItems, setExperienceItems] = useState<Record<string, unknown>[]>([]);
  const [experienceId, setExperienceId] = useState("");
  const [lessonType, setLessonType] = useState("response.quality");
  const [lessonStatement, setLessonStatement] = useState("");
  const [lessonItems, setLessonItems] = useState<Record<string, unknown>[]>([]);
  const [fitnessProfileId, setFitnessProfileId] = useState("support-quality");
  const [fitnessProfileVersion, setFitnessProfileVersion] = useState("1.0.0");
  const [fitnessMinSamples, setFitnessMinSamples] = useState("3");
  const [fitnessItems, setFitnessItems] = useState<Record<string, unknown>[]>([]);
  const [experimentAgentId, setExperimentAgentId] = useState("");
  const [experimentParentVersion, setExperimentParentVersion] = useState("1.0.0");
  const [experimentCandidateVersion, setExperimentCandidateVersion] = useState("1.1.0");
  const [experimentParentRunId, setExperimentParentRunId] = useState("");
  const [experimentCandidateRunId, setExperimentCandidateRunId] = useState("");
  const [experimentWorkloadRef, setExperimentWorkloadRef] = useState("benchmark://support-quality/v1");
  const [experimentWorkloadHash, setExperimentWorkloadHash] = useState("");
  const [experimentComparison, setExperimentComparison] = useState<Record<string, unknown> | null>(null);
  const [experimentComparisonId, setExperimentComparisonId] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  const versionCount = useMemo(() => versions.length, [versions]);

  async function createAgent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("create");
    setNotice(null);
    setCreated(null);
    try {
      const manifest = JSON.parse(manifestText) as Record<string, unknown>;
      const result = await api.createAgent(manifest);
      setCreated(result);
      setAgentId(result.agent_id);
      setNotice({ kind: "success", text: result.created ? "Agent version registered." : "This immutable agent version already exists." });
    } catch (error) {
      setNotice({ kind: "error", text: error instanceof SyntaxError ? "Manifest must be valid JSON." : errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function loadVersions(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("versions");
    setNotice(null);
    setSelectedVersion(null);
    try {
      const result = await api.listAgentVersions(agentId.trim());
      setVersions(result.items);
      setNotice({ kind: "success", text: `Loaded ${result.items.length} version(s) for ${result.agent_id}.` });
    } catch (error) {
      setVersions([]);
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function loadVersion(version: string) {
    setBusy("version");
    setNotice(null);
    try {
      const result = await api.getAgentVersion(agentId.trim(), version);
      setSelectedVersion(result);
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function loadExecution(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("execution");
    setNotice(null);
    setExecution(null);
    try {
      setExecution(await api.getExecution(executionId.trim()));
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function submitJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("job-submit");
    setNotice(null);
    setJob(null);
    setJobEvents([]);
    try {
      const input = JSON.parse(jobInput) as unknown;
      const result = await api.submitExecutionJob(
        agentId.trim(),
        jobVersion.trim(),
        input,
        1024,
        crypto.randomUUID(),
      );
      setJob(result);
      setJobId(String(result.job_id ?? ""));
      setNotice({ kind: "success", text: "Execution job accepted by the API. Worker completion is asynchronous." });
    } catch (error) {
      setNotice({ kind: "error", text: error instanceof SyntaxError ? "Execution input must be valid JSON." : errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function inspectJob() {
    if (!jobId.trim()) return;
    setBusy("job-inspect");
    setNotice(null);
    try {
      const [record, eventResult] = await Promise.all([
        api.getExecutionJob(jobId.trim()),
        api.listExecutionJobEvents(jobId.trim()),
      ]);
      setJob(record);
      setJobEvents(eventResult.items);
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function cancelJob() {
    if (!jobId.trim()) return;
    setBusy("job-cancel");
    setNotice(null);
    try {
      setJob(await api.cancelExecutionJob(jobId.trim()));
      setNotice({ kind: "success", text: "Cancellation request submitted. The worker confirms the final state." });
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function saveEvaluationProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("profile-save");
    setNotice(null);
    try {
      const profile = JSON.parse(profileText) as Record<string, unknown>;
      const saved = await api.createEvaluationProfile(profile);
      setProfileId(String(saved.profile_id ?? profileId));
      setProfileVersion(String(saved.version ?? profileVersion));
      setNotice({ kind: "success", text: "Immutable evaluation profile version registered." });
    } catch (error) {
      setNotice({ kind: "error", text: error instanceof SyntaxError ? "Evaluation profile must be valid JSON." : errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function loadEvaluationProfiles(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("profile-list");
    setNotice(null);
    try {
      const result = await api.listEvaluationProfileVersions(profileId.trim());
      setProfileVersions(result.items);
      setNotice({ kind: "success", text: `Loaded ${result.items.length} immutable profile version(s).` });
    } catch (error) {
      setProfileVersions([]);
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function runEvaluation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("evaluation-run");
    setNotice(null);
    setEvaluationRun(null);
    try {
      const results = JSON.parse(evaluationResultsText) as unknown;
      if (!Array.isArray(results)) throw new Error("Evaluation results must be a JSON array.");
      const result = await api.submitEvaluation(
        agentId.trim(), jobVersion.trim(), profileId.trim(), profileVersion.trim(), results,
      );
      setEvaluationRun(result);
      setEvaluationRunId(String(result.run_id ?? ""));
      setNotice({ kind: "success", text: "Evaluation scored the submitted outputs. This endpoint does not execute the agent." });
    } catch (error) {
      setNotice({ kind: "error", text: error instanceof SyntaxError ? "Submitted evaluation results must be valid JSON." : errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function loadEvaluationHistory() {
    if (!agentId.trim() || !jobVersion.trim()) return;
    setBusy("evaluation-history");
    setNotice(null);
    try {
      const result = await api.listEvaluations(agentId.trim(), jobVersion.trim());
      setEvaluationRuns(result.items);
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function inspectEvaluationRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("evaluation-inspect");
    setNotice(null);
    try {
      setEvaluationRun(await api.getEvaluation(evaluationRunId.trim()));
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function createCognitiveRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("cognition-create");
    setNotice(null);
    setCognitiveRun(null);
    setCognitiveEvents([]);
    setContextSnapshot(null);
    try {
      const result = await api.createCognitiveRun(
        cognitiveGoalRef.trim(), cognitiveAgentRef.trim(), crypto.randomUUID(),
      );
      const run = result.run as Record<string, unknown>;
      setCognitiveRun(run);
      setCognitiveRunId(String(run.run_id ?? ""));
      const eventResult = await api.listCognitiveRunEvents(String(run.run_id ?? ""));
      setCognitiveEvents(eventResult.items);
      setNotice({ kind: "success", text: "Cognitive run created. The runtime records lifecycle state; it does not execute an agent." });
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function inspectCognitiveRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("cognition-inspect");
    setNotice(null);
    setContextSnapshot(null);
    try {
      const [run, events] = await Promise.all([
        api.getCognitiveRun(cognitiveRunId.trim()),
        api.listCognitiveRunEvents(cognitiveRunId.trim()),
      ]);
      setCognitiveRun(run);
      setCognitiveEvents(events.items);
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function assembleCognitiveContext(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!cognitiveRunId.trim()) return;
    setBusy("cognition-context");
    setNotice(null);
    try {
      const result = await api.assembleCognitiveContext(
        cognitiveRunId.trim(),
        { ...(contextSearch.trim() ? { search: contextSearch.trim() } : {}), include_inferences: false, limit: 8 },
        crypto.randomUUID(),
      );
      setContextSnapshot(result);
      const events = await api.listCognitiveRunEvents(cognitiveRunId.trim());
      setCognitiveEvents(events.items);
      setNotice({ kind: "success", text: "Context snapshot assembled from currently valid, policy-eligible tenant evidence." });
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function createExperience(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("experience-create");
    setNotice(null);
    try {
      const result = await api.createExperience({
        execution_id: experienceExecutionId.trim(),
        evaluation_run_id: experienceEvaluationId.trim(),
        workload_ref: experienceWorkloadRef.trim(),
        workload_hash: experienceWorkloadHash.trim(),
      });
      setExperienceId(String(result.experience_id ?? ""));
      setNotice({ kind: "success", text: "Experience record saved. Its linkage remains caller-asserted and unverified." });
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally { setBusy(null); }
  }

  async function loadExperiences(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("experience-list");
    try {
      const result = await api.listExperiences(experienceAgentId.trim());
      setExperienceItems(result.items);
      setNotice({ kind: "success", text: `Loaded ${result.items.length} experience record(s).` });
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally { setBusy(null); }
  }

  async function createLesson(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("lesson-create");
    try {
      const result = await api.createLearningLesson(experienceId.trim(), {
        lesson_type: lessonType.trim(),
        statement: lessonStatement.trim(),
        evidence_refs: [experienceId.trim()],
        source_kind: "human_review",
        extractor_version: "agentbox-learning-ui/1.0.0",
        confidence: 1,
      });
      setLessonItems((items) => [result, ...items.filter((item) => item.lesson_id !== result.lesson_id)]);
      setNotice({ kind: "success", text: "Proposed lesson recorded with its source experience reference." });
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally { setBusy(null); }
  }

  async function loadLessons() {
    setBusy("lesson-list");
    try {
      const result = await api.listLearningLessons(experienceId.trim());
      setLessonItems(result.items);
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally { setBusy(null); }
  }

  async function loadFitness(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("fitness");
    try {
      const result = await api.selectFitness(fitnessProfileId.trim(), fitnessProfileVersion.trim(), Number(fitnessMinSamples));
      setFitnessItems(result.items);
      setNotice({ kind: "success", text: `Loaded ${result.items.length} evidence-based fitness candidate(s). This ranking is not promotion approval.` });
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally { setBusy(null); }
  }

  async function compareExperiment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("experiment");
    setNotice(null);
    try {
      const result = await api.compareAgentExperiment({
        agent_id: experimentAgentId.trim(),
        parent_version: experimentParentVersion.trim(),
        candidate_version: experimentCandidateVersion.trim(),
        parent_evaluation_run_id: experimentParentRunId.trim(),
        candidate_evaluation_run_id: experimentCandidateRunId.trim(),
        workload_ref: experimentWorkloadRef.trim(),
        workload_hash: experimentWorkloadHash.trim(),
      });
      setExperimentComparison(result);
      setExperimentComparisonId(String(result.comparison_id ?? ""));
      setNotice({ kind: "success", text: "Comparison recorded. Workload identity remains caller-asserted and the result is advisory only." });
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally { setBusy(null); }
  }

  async function loadExperimentComparison(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("experiment-read");
    try {
      setExperimentComparison(await api.getAgentExperimentComparison(experimentComparisonId.trim()));
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error) });
    } finally { setBusy(null); }
  }

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="AgentBox home">
          <span className="brand-mark">A</span><span>AgentBox</span>
        </a>
        <p className="side-label">PLATFORM</p>
        <nav className="side-nav" aria-label="Workspace sections">
          <a className="active" href="#overview">Overview</a>
          <a href="#agent-studio">Agent Studio</a>
          <a href="#execution">Execution</a>
          <a href="#evaluation">Evaluation</a>
          <a href="#cognition">Cognition</a>
          <a href="#learning">Learning & Fitness</a>
          <a href="#experiments">Experiments</a>
          <a href="#governance">Governance</a>
        </nav>
        <div className="sidebar-foot">
          <span className="status-pip" /> API-backed workspace
          <small>Backend remains the authority for access and policy.</small>
        </div>
      </aside>

      <main className="workspace-main">
        <header className="workspace-topbar">
          <div><span className="crumb">WORKSPACE</span><strong>Overview</strong></div>
          <span className="environment-pill">AgentBox platform</span>
        </header>

        <section className="welcome" id="overview">
          <div>
            <p className="eyebrow">AGENT OPERATING ENVIRONMENT</p>
            <h1>Build. Run. Learn. Govern.</h1>
            <p className="intro">One platform for agent intelligence, agent creation, application composition, execution, and platform governance.</p>
          </div>
          <div className="welcome-mark" aria-hidden="true">A<span>•</span></div>
        </section>

        {notice && (
          <div className={`notice ${notice.kind}`} role="status">{notice.text}</div>
        )}

        <section className="metric-grid" aria-label="Platform capabilities">
          <article className="metric-card"><span className="metric-icon">01</span><strong>AI Intelligence</strong><p>Context, cognition, verification, and memory.</p><span className="metric-state">Runtime foundation</span></article>
          <article className="metric-card"><span className="metric-icon">02</span><strong>Agent Factory</strong><p>Immutable manifests, versions, and lineage.</p><span className="metric-state">API connected</span></article>
          <article className="metric-card"><span className="metric-icon">03</span><strong>Application Factory</strong><p>Compose governed capabilities and workflows.</p><span className="metric-state planned">In progress</span></article>
          <article className="metric-card"><span className="metric-icon">04</span><strong>Execution Engine</strong><p>Inspect execution records and outcomes.</p><span className="metric-state">API connected</span></article>
          <article className="metric-card"><span className="metric-icon">05</span><strong>Platform Foundation</strong><p>Identity, tenancy, policy, audit, and data.</p><span className="metric-state">API connected</span></article>
        </section>

        <section className="content-section" id="agent-studio">
          <div className="section-heading">
            <div><p className="eyebrow">AGENT FACTORY</p><h2>Agent Studio</h2><p>Create a versioned agent or inspect an existing agent's immutable versions.</p></div>
            <span className="section-count">{versionCount} versions loaded</span>
          </div>
          <div className="two-column">
            <article className="surface-card">
              <div className="card-heading"><div><span className="card-kicker">NEW ARTIFACT</span><h3>Register agent version</h3></div><span className="card-symbol">＋</span></div>
              <form onSubmit={createAgent} className="stack-form">
                <label htmlFor="manifest">Agent manifest · JSON</label>
                <textarea id="manifest" value={manifestText} onChange={(event) => setManifestText(event.target.value)} rows={19} spellCheck={false} />
                <p className="helper">Uses the canonical AgentBox manifest contract. Secrets must be referenced, never embedded.</p>
                <button className="primary-button" type="submit" disabled={busy !== null}>{busy === "create" ? "Registering…" : "Register immutable version"}</button>
              </form>
              {created && <div className="result-card"><strong>{created.agent_id} · v{created.version}</strong><small>Artifact: {created.artifact_id}</small><small>SHA-256: {created.content_hash}</small></div>}
            </article>

            <article className="surface-card">
              <div className="card-heading"><div><span className="card-kicker">VERSION HISTORY</span><h3>Inspect an agent</h3></div><span className="card-symbol">↗</span></div>
              <form onSubmit={loadVersions} className="stack-form">
                <label htmlFor="agent-id">Agent ID</label>
                <input id="agent-id" value={agentId} onChange={(event) => setAgentId(event.target.value)} placeholder="e.g. sample-support-agent" required />
                <button className="secondary-button" type="submit" disabled={busy !== null || !agentId.trim()}>{busy === "versions" ? "Loading…" : "Load versions"}</button>
              </form>
              {versions.length > 0 ? (
                <div className="version-list">
                  {versions.map((item) => <button className={selectedVersion?.version === item.version ? "version-row selected" : "version-row"} key={item.artifact_id} onClick={() => void loadVersion(item.version)} type="button"><span><strong>v{item.version}</strong><small>{new Date(item.created_at).toLocaleString()}</small></span><span className="version-arrow">→</span></button>)}
                </div>
              ) : <p className="empty-state">No versions loaded. Enter an agent ID to query the tenant-scoped registry.</p>}
              {selectedVersion && <div className="detail-block"><div className="detail-title">Version manifest</div><JsonPanel value={selectedVersion.manifest} /></div>}
            </article>
          </div>
        </section>

        <section className="content-section" id="execution">
          <div className="section-heading">
            <div><p className="eyebrow">EXECUTION ENGINE</p><h2>Execution inspector</h2><p>Retrieve a real execution record using its identifier.</p></div>
          </div>
          <div className="two-column">
            <article className="surface-card execution-card">
              <div className="card-heading"><div><span className="card-kicker">DURABLE WORKER</span><h3>Submit agent job</h3></div><span className="card-symbol">▶</span></div>
              <form onSubmit={submitJob} className="stack-form">
                <label htmlFor="job-agent-id">Agent ID</label>
                <input id="job-agent-id" value={agentId} onChange={(event) => setAgentId(event.target.value)} placeholder="Agent ID from Agent Studio" required />
                <label htmlFor="job-version">Immutable version</label>
                <input id="job-version" value={jobVersion} onChange={(event) => setJobVersion(event.target.value)} placeholder="1.0.0" required />
                <label htmlFor="job-input">Input · JSON</label>
                <textarea id="job-input" value={jobInput} onChange={(event) => setJobInput(event.target.value)} rows={5} spellCheck={false} />
                <p className="helper">The API admits a tenant-scoped job with an idempotency key. A trusted worker and matching server allowlist must be configured for execution.</p>
                <button className="primary-button" type="submit" disabled={busy !== null || !agentId.trim() || !jobVersion.trim()}>{busy === "job-submit" ? "Submitting…" : "Submit execution job"}</button>
              </form>
              {job && <div className="result-card"><strong>Job {String(job.job_id ?? jobId)} · {String(job.state ?? "accepted")}</strong><small>Workload: {String(job.workload_ref ?? "")}</small></div>}
            </article>
            <article className="surface-card execution-card">
              <div className="card-heading"><div><span className="card-kicker">JOB LIFECYCLE</span><h3>Inspect and control</h3></div><span className="card-symbol">↻</span></div>
              <div className="stack-form">
                <label htmlFor="job-id">Execution job ID</label>
                <input id="job-id" value={jobId} onChange={(event) => setJobId(event.target.value)} placeholder="Paste a job ID" />
                <div className="action-row">
                  <button className="secondary-button" type="button" onClick={() => void inspectJob()} disabled={busy !== null || !jobId.trim()}>{busy === "job-inspect" ? "Loading…" : "Refresh status"}</button>
                  <button className="danger-button" type="button" onClick={() => void cancelJob()} disabled={busy !== null || !jobId.trim()}>Request cancel</button>
                </div>
              </div>
              {job && <JsonPanel value={job} />}
              {jobEvents.length > 0 && <div className="detail-block"><div className="detail-title">Append-only lifecycle events</div><JsonPanel value={jobEvents} /></div>}
            </article>
          </div>
          <article className="surface-card execution-card">
            <div className="card-heading"><div><span className="card-kicker">SYNCHRONOUS RECORD</span><h3>Execution inspector</h3></div></div>
            <form onSubmit={loadExecution} className="inline-form">
              <div className="field-grow"><label htmlFor="execution-id">Execution ID</label><input id="execution-id" value={executionId} onChange={(event) => setExecutionId(event.target.value)} placeholder="Paste an execution ID" required /></div>
              <button className="primary-button" type="submit" disabled={busy !== null || !executionId.trim()}>{busy === "execution" ? "Loading…" : "Inspect execution"}</button>
            </form>
            {execution ? <JsonPanel value={execution} /> : <p className="empty-state">Synchronous execution records appear here after a successful API lookup. No simulated data is shown.</p>}
          </article>
        </section>

        <section className="content-section" id="evaluation">
          <div className="section-heading">
            <div><p className="eyebrow">QUALITY & VERIFICATION</p><h2>Evaluation Explorer</h2><p>Version deterministic quality profiles, submit measured outputs, and inspect scoring and gates.</p></div>
          </div>
          <div className="two-column">
            <article className="surface-card">
              <div className="card-heading"><div><span className="card-kicker">IMMUTABLE CONTRACT</span><h3>Evaluation profile</h3></div><span className="card-symbol">✓</span></div>
              <form onSubmit={saveEvaluationProfile} className="stack-form">
                <label htmlFor="evaluation-profile">Profile · JSON</label>
                <textarea id="evaluation-profile" value={profileText} onChange={(event) => setProfileText(event.target.value)} rows={18} spellCheck={false} />
                <p className="helper">Profiles define cases, deterministic assertions, scoring weights, and promotion gates. No executable evaluator code is accepted.</p>
                <button className="primary-button" type="submit" disabled={busy !== null}>{busy === "profile-save" ? "Saving…" : "Register profile version"}</button>
              </form>
              <form onSubmit={loadEvaluationProfiles} className="stack-form detail-block">
                <label htmlFor="profile-id">Profile ID</label>
                <input id="profile-id" value={profileId} onChange={(event) => setProfileId(event.target.value)} required />
                <button className="secondary-button" type="submit" disabled={busy !== null || !profileId.trim()}>{busy === "profile-list" ? "Loading…" : "Load profile versions"}</button>
              </form>
              {profileVersions.length > 0 && <div className="version-list">{profileVersions.map((item) => <button className="version-row" key={String(item.version)} type="button" onClick={async () => { setBusy("profile-detail"); try { const result = await api.getEvaluationProfile(profileId.trim(), String(item.version)); setProfileVersion(String(item.version)); setProfileText(JSON.stringify(result.profile, null, 2)); setNotice({ kind: "success", text: `Loaded profile version ${String(item.version)}.` }); } catch (error) { setNotice({ kind: "error", text: errorMessage(error) }); } finally { setBusy(null); } }}><span><strong>v{String(item.version)}</strong><small>{String(item.created_at ?? "")}</small></span><span className="version-arrow">→</span></button>)}</div>}
            </article>
            <article className="surface-card execution-card">
              <div className="card-heading"><div><span className="card-kicker">DETERMINISTIC SCORING</span><h3>Score submitted outputs</h3></div><span className="card-symbol">◎</span></div>
              <form onSubmit={runEvaluation} className="stack-form">
                <label htmlFor="eval-agent-id">Agent ID</label>
                <input id="eval-agent-id" value={agentId} onChange={(event) => setAgentId(event.target.value)} required />
                <label htmlFor="eval-agent-version">Agent version</label>
                <input id="eval-agent-version" value={jobVersion} onChange={(event) => setJobVersion(event.target.value)} required />
                <label htmlFor="eval-profile-version">Profile version</label>
                <input id="eval-profile-version" value={profileVersion} onChange={(event) => setProfileVersion(event.target.value)} required />
                <label htmlFor="eval-results">Measured case outputs · JSON array</label>
                <textarea id="eval-results" value={evaluationResultsText} onChange={(event) => setEvaluationResultsText(event.target.value)} rows={9} spellCheck={false} />
                <p className="helper">Supply output, latency_ms, and cost_usd for each case. Agent execution and benchmark generation are not performed by this scoring API.</p>
                <button className="primary-button" type="submit" disabled={busy !== null || !agentId.trim()}>{busy === "evaluation-run" ? "Scoring…" : "Score submitted outputs"}</button>
              </form>
              {evaluationRun && <div className="result-card"><strong>{evaluationRun.passed ? "All evaluation gates passed" : "Evaluation gates not passed"} · score {String(evaluationRun.score ?? "—")}</strong><small>Run: {String(evaluationRun.run_id ?? "")} · case pass rate {String(evaluationRun.case_pass_rate ?? "—")}</small><JsonPanel value={evaluationRun.gate_results ?? {}} /></div>}
              <div className="detail-block">
                <button className="secondary-button" type="button" onClick={() => void loadEvaluationHistory()} disabled={busy !== null || !agentId.trim()}>Load evaluation history</button>
                {evaluationRuns.length > 0 && <div className="version-list">{evaluationRuns.map((item) => <button className="version-row" key={String(item.run_id)} type="button" onClick={() => { setEvaluationRun(item); setEvaluationRunId(String(item.run_id)); }}><span><strong>{String(item.passed ? "PASS" : "FAIL")} · {String(item.score)}</strong><small>{String(item.run_id)}</small></span><span className="version-arrow">→</span></button>)}</div>}
              </div>
              <form onSubmit={inspectEvaluationRun} className="stack-form detail-block">
                <label htmlFor="evaluation-run-id">Evaluation run ID</label>
                <input id="evaluation-run-id" value={evaluationRunId} onChange={(event) => setEvaluationRunId(event.target.value)} required />
                <button className="secondary-button" type="submit" disabled={busy !== null || !evaluationRunId.trim()}>{busy === "evaluation-inspect" ? "Loading…" : "Inspect evaluation run"}</button>
              </form>
            </article>
          </div>
        </section>

        <section className="content-section" id="cognition">
          <div className="section-heading">
            <div><p className="eyebrow">AI INTELLIGENCE</p><h2>Cognitive Runtime Explorer</h2><p>Inspect tenant-scoped run state, ordered lifecycle events, and minimized context snapshots.</p></div>
          </div>
          <div className="two-column">
            <article className="surface-card">
              <div className="card-heading"><div><span className="card-kicker">RUN INITIALIZATION</span><h3>Create cognitive run</h3></div></div>
              <form onSubmit={createCognitiveRun} className="stack-form">
                <label htmlFor="cognitive-goal">Goal reference</label>
                <input id="cognitive-goal" value={cognitiveGoalRef} onChange={(event) => setCognitiveGoalRef(event.target.value)} required />
                <label htmlFor="cognitive-agent">Immutable agent reference · agent-id@version</label>
                <input id="cognitive-agent" value={cognitiveAgentRef} onChange={(event) => setCognitiveAgentRef(event.target.value)} placeholder="support-agent@1.0.0" required />
                <button className="primary-button" type="submit" disabled={busy !== null}>{busy === "cognition-create" ? "Creating…" : "Create cognitive run"}</button>
              </form>
              <form onSubmit={inspectCognitiveRun} className="stack-form detail-block">
                <label htmlFor="cognitive-run-id">Run ID</label>
                <input id="cognitive-run-id" value={cognitiveRunId} onChange={(event) => setCognitiveRunId(event.target.value)} required />
                <button className="secondary-button" type="submit" disabled={busy !== null || !cognitiveRunId.trim()}>{busy === "cognition-inspect" ? "Loading…" : "Inspect run"}</button>
              </form>
              {cognitiveRun && <JsonPanel value={cognitiveRun} />}
            </article>
            <article className="surface-card">
              <div className="card-heading"><div><span className="card-kicker">CONTEXT ASSEMBLY</span><h3>Inspect context snapshot</h3></div></div>
              <form onSubmit={assembleCognitiveContext} className="stack-form">
                <label htmlFor="context-search">Optional entity search</label>
                <input id="context-search" value={contextSearch} onChange={(event) => setContextSearch(event.target.value)} placeholder="Search eligible tenant context" />
                <p className="helper">Only currently valid observed facts and human-validated entities are included by default. Inferences remain disabled.</p>
                <button className="primary-button" type="submit" disabled={busy !== null || !cognitiveRunId.trim()}>{busy === "cognition-context" ? "Assembling…" : "Assemble context snapshot"}</button>
              </form>
              {contextSnapshot ? <JsonPanel value={contextSnapshot} /> : <p className="empty-state">No context snapshot loaded. Assembly is an explicit, audited API operation.</p>}
            </article>
          </div>
          <article className="surface-card execution-card">
            <div className="card-heading"><div><span className="card-kicker">APPEND-ONLY JOURNAL · READ-ONLY VIEW</span><h3>Nine-stage lifecycle timeline</h3></div></div>
            <div className="governance-grid">
              {["intent","context","cognition","capability","responsibility","execution","observation","verification","memory"].map((stage, index) => {
                const seen = cognitiveEvents.some((item) => item.stage === stage);
                return <div className="governance-item" key={stage}><strong>{String(index + 1).padStart(2, "0")} · {stage}</strong><p>{seen ? "Event recorded" : "Not yet recorded"}</p></div>;
              })}
            </div>
            {cognitiveEvents.length > 0 ? <JsonPanel value={cognitiveEvents} /> : <p className="empty-state">Lifecycle events appear after a run is created or inspected. This view cannot append events, invoke capabilities, or grant authority.</p>}
          </article>
        </section>

        <section className="content-section" id="learning">
          <div className="section-heading">
            <div><p className="eyebrow">AI INTELLIGENCE · AGENT EVOLUTION</p><h2>Experience, Learning & Fitness</h2><p>Retain minimized execution/evaluation links, derive traceable lessons, and rank agents using repeated evidence.</p></div>
          </div>
          <div className="two-column">
            <article className="surface-card">
              <div className="card-heading"><div><span className="card-kicker">EXPERIENCE LEDGER</span><h3>Record execution experience</h3></div></div>
              <form onSubmit={createExperience} className="stack-form">
                <label htmlFor="experience-execution">Execution ID</label><input id="experience-execution" value={experienceExecutionId} onChange={(e) => setExperienceExecutionId(e.target.value)} required />
                <label htmlFor="experience-evaluation">Evaluation run ID</label><input id="experience-evaluation" value={experienceEvaluationId} onChange={(e) => setExperienceEvaluationId(e.target.value)} required />
                <label htmlFor="experience-workload">Workload reference</label><input id="experience-workload" value={experienceWorkloadRef} onChange={(e) => setExperienceWorkloadRef(e.target.value)} required />
                <label htmlFor="experience-hash">Workload SHA-256</label><input id="experience-hash" value={experienceWorkloadHash} onChange={(e) => setExperienceWorkloadHash(e.target.value)} minLength={64} maxLength={64} pattern="[a-fA-F0-9]{64}" required />
                <button className="primary-button" type="submit" disabled={busy !== null}>{busy === "experience-create" ? "Saving…" : "Record experience"}</button>
              </form>
              <form onSubmit={loadExperiences} className="stack-form detail-block">
                <label htmlFor="experience-agent">Agent ID</label><input id="experience-agent" value={experienceAgentId} onChange={(e) => setExperienceAgentId(e.target.value)} required />
                <button className="secondary-button" type="submit" disabled={busy !== null}>{busy === "experience-list" ? "Loading…" : "Load experiences"}</button>
              </form>
              {experienceItems.length > 0 && <div className="version-list">{experienceItems.map((item) => <button className="version-row" key={String(item.experience_id)} type="button" onClick={() => setExperienceId(String(item.experience_id))}><span><strong>{String(item.agent_id)}@{String(item.agent_version)}</strong><small>{String(item.experience_id)} · {String(item.evidence_status)}</small></span><span className="version-arrow">→</span></button>)}</div>}
              <p className="helper">Experience API currently records caller-asserted execution/evaluation linkage. Records are marked unverified and must not be treated as trusted execution proof.</p>
            </article>
            <article className="surface-card">
              <div className="card-heading"><div><span className="card-kicker">TRACEABLE LEARNING</span><h3>Propose a lesson</h3></div></div>
              <form onSubmit={createLesson} className="stack-form">
                <label htmlFor="lesson-experience">Source experience ID</label><input id="lesson-experience" value={experienceId} onChange={(e) => setExperienceId(e.target.value)} required />
                <label htmlFor="lesson-type">Normalized lesson type</label><input id="lesson-type" value={lessonType} onChange={(e) => setLessonType(e.target.value)} pattern="[a-z][a-z0-9._-]{1,63}" required />
                <label htmlFor="lesson-statement">Lesson statement</label><textarea id="lesson-statement" value={lessonStatement} onChange={(e) => setLessonStatement(e.target.value)} maxLength={2000} rows={4} required />
                <button className="primary-button" type="submit" disabled={busy !== null || !experienceId.trim()}>{busy === "lesson-create" ? "Saving…" : "Save proposed lesson"}</button>
                <button className="secondary-button" type="button" onClick={() => void loadLessons()} disabled={busy !== null || !experienceId.trim()}>{busy === "lesson-list" ? "Loading…" : "Load lessons"}</button>
              </form>
              {lessonItems.length > 0 && <JsonPanel value={lessonItems} />}
              <p className="helper">Lessons remain proposed, immutable and source-linked. They do not rewrite agent configuration or grant authority.</p>
            </article>
          </div>
          <article className="surface-card execution-card">
            <div className="card-heading"><div><span className="card-kicker">REPEATED SAMPLE SELECTION</span><h3>Fitness ranking</h3></div></div>
            <form onSubmit={loadFitness} className="stack-form">
              <div className="two-column">
                <div><label htmlFor="fitness-profile">Evaluation profile ID</label><input id="fitness-profile" value={fitnessProfileId} onChange={(e) => setFitnessProfileId(e.target.value)} required /></div>
                <div><label htmlFor="fitness-version">Profile version</label><input id="fitness-version" value={fitnessProfileVersion} onChange={(e) => setFitnessProfileVersion(e.target.value)} required /></div>
              </div>
              <label htmlFor="fitness-samples">Minimum sample count</label><input id="fitness-samples" type="number" min={1} max={1000} value={fitnessMinSamples} onChange={(e) => setFitnessMinSamples(e.target.value)} required />
              <button className="primary-button" type="submit" disabled={busy !== null}>{busy === "fitness" ? "Selecting…" : "Select fitness candidates"}</button>
            </form>
            {fitnessItems.length > 0 ? <JsonPanel value={fitnessItems} /> : <p className="empty-state">No fitness selection loaded. Selection requires repeated experience samples under the exact evaluation profile version.</p>}
            <p className="helper">Fitness scores are historical aggregates over unverified, caller-asserted records. They are prioritization signals only—not trusted benchmark comparisons, gate results, or promotion authority.</p>
          </article>
        </section>

        <section className="content-section" id="experiments">
          <div className="section-heading">
            <div><p className="eyebrow">AGENT FACTORY · CONTROLLED COMPARISON</p><h2>Parent–Candidate Experiment</h2><p>Compare immutable parent and candidate versions using two evaluation runs pinned to one profile version and an asserted workload identity.</p></div>
          </div>
          <article className="surface-card execution-card">
            <form onSubmit={compareExperiment} className="stack-form">
              <div className="two-column">
                <div><label htmlFor="experiment-agent">Agent ID</label><input id="experiment-agent" value={experimentAgentId} onChange={(e) => setExperimentAgentId(e.target.value)} required /></div>
                <div><label htmlFor="experiment-parent">Parent version</label><input id="experiment-parent" value={experimentParentVersion} onChange={(e) => setExperimentParentVersion(e.target.value)} required /></div>
                <div><label htmlFor="experiment-candidate">Candidate version</label><input id="experiment-candidate" value={experimentCandidateVersion} onChange={(e) => setExperimentCandidateVersion(e.target.value)} required /></div>
                <div><label htmlFor="experiment-parent-run">Parent evaluation run ID</label><input id="experiment-parent-run" value={experimentParentRunId} onChange={(e) => setExperimentParentRunId(e.target.value)} required /></div>
                <div><label htmlFor="experiment-candidate-run">Candidate evaluation run ID</label><input id="experiment-candidate-run" value={experimentCandidateRunId} onChange={(e) => setExperimentCandidateRunId(e.target.value)} required /></div>
                <div><label htmlFor="experiment-workload">Workload reference</label><input id="experiment-workload" value={experimentWorkloadRef} onChange={(e) => setExperimentWorkloadRef(e.target.value)} required /></div>
              </div>
              <label htmlFor="experiment-hash">Workload SHA-256</label><input id="experiment-hash" value={experimentWorkloadHash} onChange={(e) => setExperimentWorkloadHash(e.target.value)} minLength={64} maxLength={64} pattern="[a-fA-F0-9]{64}" required />
              <button className="primary-button" type="submit" disabled={busy !== null}>{busy === "experiment" ? "Comparing…" : "Create parent–candidate comparison"}</button>
            </form>
            <form onSubmit={loadExperimentComparison} className="stack-form detail-block">
              <label htmlFor="experiment-comparison-id">Comparison ID</label><input id="experiment-comparison-id" value={experimentComparisonId} onChange={(e) => setExperimentComparisonId(e.target.value)} required />
              <button className="secondary-button" type="submit" disabled={busy !== null}>{busy === "experiment-read" ? "Loading…" : "Load comparison"}</button>
            </form>
            {experimentComparison ? <JsonPanel value={experimentComparison} /> : <p className="empty-state">No experiment comparison loaded.</p>}
            <p className="helper">The backend verifies tenant ownership, immutable candidate-to-parent lineage, exact agent versions, completed evaluation runs and profile-version identity. The supplied workload reference/hash is still caller-asserted. Results are unverified and advisory; there are no trusted-runner attestations, hard-gate authority, or promotion action.</p>
          </article>
        </section>

        <section className="content-section" id="governance">
          <div className="section-heading"><div><p className="eyebrow">GOVERNANCE</p><h2>Trust boundaries</h2><p>These controls remain enforced by the backend, regardless of what the UI displays.</p></div></div>
          <div className="governance-grid">
            <div className="governance-item"><strong>Identity & tenancy</strong><p>OIDC identity and tenant context are verified server-side.</p></div>
            <div className="governance-item"><strong>Authorization</strong><p>Capabilities and resource ownership are decided by backend policy.</p></div>
            <div className="governance-item"><strong>Evidence & audit</strong><p>UI actions do not bypass evidence review or append-only audit.</p></div>
          </div>
        </section>
        <footer className="workspace-footer"><span>AgentBox · Web Console</span><span>Contract-driven · Tenant-scoped · Backend-governed</span></footer>
      </main>
    </div>
  );
}
