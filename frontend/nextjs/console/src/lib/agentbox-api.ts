export type AccessTokenProvider = () => Promise<string | null>;

export type AgentVersionRecord = {
  agent_id: string;
  version: string;
  content_hash: string;
  manifest: Record<string, unknown>;
  parent_artifact_id: string | null;
  created_at: string;
  artifact_id: string;
};

export type AgentVersionList = {
  agent_id: string;
  items: AgentVersionRecord[];
  limit: number;
  offset: number;
};

export class AgentBoxApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(message);
    this.name = "AgentBoxApiError";
  }
}

export class AgentBoxApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl: string,
    private readonly accessTokenProvider: AccessTokenProvider,
    private readonly fetcher: typeof fetch = fetch,
  ) {
    const parsed = new URL(baseUrl);
    if (parsed.protocol !== "https:" && parsed.hostname !== "localhost" && parsed.hostname !== "127.0.0.1") {
      throw new Error("AgentBox API must use HTTPS outside localhost development");
    }
    this.baseUrl = parsed.toString().replace(/\/$/, "");
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    authenticated = true,
  ): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body !== undefined) headers.set("Content-Type", "application/json");

    if (authenticated) {
      const token = await this.accessTokenProvider();
      if (!token) throw new AgentBoxApiError("An authenticated session is required", 401, null);
      headers.set("Authorization", `Bearer ${token}`);
    }

    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      ...init,
      headers,
      credentials: "omit",
    });
    if (!response.ok) {
      let detail: unknown = null;
      try { detail = await response.json(); } catch { detail = await response.text(); }
      throw new AgentBoxApiError(`AgentBox API request failed (${response.status})`, response.status, detail);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  health(): Promise<{ status: string; service: string }> {
    return this.request("/health", {}, false);
  }

  listAgentVersions(agentId: string, limit = 50, offset = 0): Promise<AgentVersionList> {
    return this.request(
      `/v1/agents/${encodeURIComponent(agentId)}/versions?limit=${limit}&offset=${offset}`,
    );
  }

  getAgentVersion(agentId: string, version: string): Promise<AgentVersionRecord> {
    return this.request(
      `/v1/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(version)}`,
    );
  }

  createAgent(manifest: Record<string, unknown>): Promise<AgentVersionRecord & { created: boolean }> {
    return this.request("/v1/agents", { method: "POST", body: JSON.stringify({ manifest }) });
  }

  getExecution(executionId: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/executions/${encodeURIComponent(executionId)}`);
  }

  submitExecutionJob(
    agentId: string,
    version: string,
    input: unknown,
    maxOutputTokens: number,
    idempotencyKey: string,
  ): Promise<Record<string, unknown>> {
    return this.request(
      `/v1/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(version)}/jobs`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({ input, max_output_tokens: maxOutputTokens }),
      },
    );
  }

  getExecutionJob(jobId: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/execution-jobs/${encodeURIComponent(jobId)}`);
  }

  listExecutionJobEvents(jobId: string): Promise<{ items: Record<string, unknown>[] }> {
    return this.request(`/v1/execution-jobs/${encodeURIComponent(jobId)}/events`);
  }

  cancelExecutionJob(jobId: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/execution-jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    });
  }
}

/**
 * Browser transport for the Next.js BFF. It relies on the HttpOnly Auth.js
 * session cookie and never reads or handles API bearer tokens in JavaScript.
 */
export class AgentBoxBffClient {
  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body !== undefined) headers.set("Content-Type", "application/json");

    const response = await fetch(`/api/agentbox${path}`, {
      ...init,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) {
      let detail: unknown = null;
      try {
        detail = await response.json();
      } catch {
        detail = await response.text();
      }
      throw new AgentBoxApiError(
        `AgentBox API request failed (${response.status})`,
        response.status,
        detail,
      );
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  listAgentVersions(agentId: string, limit = 50, offset = 0): Promise<AgentVersionList> {
    return this.request(
      `/v1/agents/${encodeURIComponent(agentId)}/versions?limit=${limit}&offset=${offset}`,
    );
  }

  getAgentVersion(agentId: string, version: string): Promise<AgentVersionRecord> {
    return this.request(
      `/v1/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(version)}`,
    );
  }

  createAgent(manifest: Record<string, unknown>): Promise<AgentVersionRecord & { created: boolean }> {
    return this.request("/v1/agents", {
      method: "POST",
      body: JSON.stringify({ manifest }),
    });
  }

  getExecution(executionId: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/executions/${encodeURIComponent(executionId)}`);
  }

 
  submitExecutionJob(
    agentId: string,
    version: string,
    input: unknown,
    maxOutputTokens: number,
    idempotencyKey: string,
  ): Promise<Record<string, unknown>> {
    return this.request(
      `/v1/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(version)}/jobs`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({ input, max_output_tokens: maxOutputTokens }),
      },
    );
  }

  getExecutionJob(jobId: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/execution-jobs/${encodeURIComponent(jobId)}`);
  }

  listExecutionJobEvents(jobId: string): Promise<{ items: Record<string, unknown>[] }> {
    return this.request(`/v1/execution-jobs/${encodeURIComponent(jobId)}/events`);
  }

  cancelExecutionJob(jobId: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/execution-jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    });
  }
  createEvaluationProfile(profile: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("/v1/evaluation-profiles", {
      method: "POST",
      body: JSON.stringify({ profile }),
    });
  }

  listEvaluationProfileVersions(profileId: string): Promise<{ items: Record<string, unknown>[] }> {
    return this.request(`/v1/evaluation-profiles/${encodeURIComponent(profileId)}/versions`);
  }

  getEvaluationProfile(profileId: string, version: string): Promise<Record<string, unknown>> {
    return this.request(
      `/v1/evaluation-profiles/${encodeURIComponent(profileId)}/versions/${encodeURIComponent(version)}`,
    );
  }

  submitEvaluation(
    agentId: string,
    version: string,
    profileId: string,
    profileVersion: string,
    results: unknown[],
  ): Promise<Record<string, unknown>> {
    return this.request(
      `/v1/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(version)}/evaluations`,
      {
        method: "POST",
        body: JSON.stringify({ profile_id: profileId, profile_version: profileVersion, results }),
      },
    );
  }

  listEvaluations(agentId: string, version: string): Promise<{ items: Record<string, unknown>[] }> {
    return this.request(
      `/v1/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(version)}/evaluations`,
    );
  }

  getEvaluation(runId: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/evaluations/${encodeURIComponent(runId)}`);
  }

  createCognitiveRun(goalRef: string, agentRef: string, idempotencyKey: string): Promise<Record<string, unknown>> {
    return this.request("/v1/cognitive-runs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ goal_ref: goalRef, agent_ref: agentRef }),
    });
  }

  getCognitiveRun(runId: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/cognitive-runs/${encodeURIComponent(runId)}`);
  }

  listCognitiveRunEvents(runId: string, afterSequence = 0, limit = 200): Promise<{ items: Record<string, unknown>[] }> {
    return this.request(`/v1/cognitive-runs/${encodeURIComponent(runId)}/events?after_sequence=${afterSequence}&limit=${limit}`);
  }

  assembleCognitiveContext(
    runId: string,
    request: Record<string, unknown>,
    idempotencyKey: string,
  ): Promise<Record<string, unknown>> {
    return this.request(`/v1/cognitive-runs/${encodeURIComponent(runId)}/context`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(request),
    });
  }

  createExperience(input: {
    execution_id: string; evaluation_run_id: string; workload_ref: string; workload_hash: string;
  }): Promise<Record<string, unknown>> {
    return this.request("/v1/agent-experiences", { method: "POST", body: JSON.stringify(input) });
  }

  listExperiences(agentId: string, profileId?: string, profileVersion?: string): Promise<{ items: Record<string, unknown>[] }> {
    const query = new URLSearchParams();
    if (profileId && profileVersion) {
      query.set("profile_id", profileId);
      query.set("profile_version", profileVersion);
    }
    const queryString = query.toString();
    return this.request(`/v1/agents/${encodeURIComponent(agentId)}/experiences${queryString ? `?${queryString}` : ""}`);
  }

  createLearningLesson(experienceId: string, input: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request(`/v1/agent-experiences/${encodeURIComponent(experienceId)}/lessons`, {
      method: "POST", body: JSON.stringify(input),
    });
  }

  listLearningLessons(experienceId: string): Promise<{ items: Record<string, unknown>[] }> {
    return this.request(`/v1/agent-experiences/${encodeURIComponent(experienceId)}/lessons`);
  }

  selectFitness(profileId: string, profileVersion: string, minSamples = 3): Promise<{ items: Record<string, unknown>[] }> {
    const query = new URLSearchParams({ profile_id: profileId, profile_version: profileVersion, min_samples: String(minSamples) });
    return this.request(`/v1/fitness/selection?${query}`);
  }

  compareAgentExperiment(input: {
    agent_id: string; parent_version: string; candidate_version: string;
    parent_evaluation_run_id: string; candidate_evaluation_run_id: string;
    workload_ref: string; workload_hash: string;
  }): Promise<Record<string, unknown>> {
    return this.request("/v1/agent-experiment-comparisons", {
      method: "POST", body: JSON.stringify(input),
    });
  }

  getAgentExperimentComparison(comparisonId: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/agent-experiment-comparisons/${encodeURIComponent(comparisonId)}`);
  }
}
