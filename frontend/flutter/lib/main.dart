import 'dart:convert';
import 'dart:math';

import 'package:flutter/material.dart';

import 'agentbox_api.dart';
import 'oidc_auth.dart';

const _apiBaseUrl = String.fromEnvironment('AGENTBOX_API_BASE_URL');

void main() {
  runApp(const AgentBoxApp());
}

class AgentBoxApp extends StatelessWidget {
  const AgentBoxApp({super.key});

  @override
  Widget build(BuildContext context) {
    const seed = Color(0xFF78A9F5);
    return MaterialApp(
      title: 'AgentBox',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: seed,
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
        scaffoldBackgroundColor: const Color(0xFF0A1020),
        cardTheme: const CardThemeData(
          color: Color(0xFF17243A),
          margin: EdgeInsets.zero,
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: const Color(0xFF101B2D),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: const BorderSide(color: Color(0xFF2A3A55)),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: const BorderSide(color: Color(0xFF2A3A55)),
          ),
        ),
      ),
      home: const AgentBoxHomePage(),
    );
  }
}

class AgentBoxHomePage extends StatefulWidget {
  const AgentBoxHomePage({super.key});

  @override
  State<AgentBoxHomePage> createState() => _AgentBoxHomePageState();
}

class _AgentBoxHomePageState extends State<AgentBoxHomePage> {
  static const _oidcConfig = AgentBoxOidcConfig(
    issuer: String.fromEnvironment('AGENTBOX_OIDC_ISSUER'),
    clientId: String.fromEnvironment('AGENTBOX_OIDC_MOBILE_CLIENT_ID'),
    redirectUrl: String.fromEnvironment('AGENTBOX_OIDC_MOBILE_REDIRECT_URI'),
  );

  late final AgentBoxOidcAuth _oidcAuth = AgentBoxOidcAuth(config: _oidcConfig);
  AgentBoxApiClient? _api;
  bool _busy = false;
  bool _signedIn = false;
  int _selectedIndex = 0;
  String? _notice;
  bool _noticeIsError = false;

  final _manifestController = TextEditingController();
  final _agentIdController = TextEditingController();
  final _executionIdController = TextEditingController();
  final _jobVersionController = TextEditingController(text: '1.0.0');
  final _jobInputController = TextEditingController(text: '{"message":"Hello from AgentBox"}');
  final _jobIdController = TextEditingController();
  final _profileIdController = TextEditingController(text: 'support-quality');
  final _profileVersionController = TextEditingController(text: '1.0.0');
  final _profileController = TextEditingController();
  final _evaluationResultsController = TextEditingController(
    text: '[{"case_id":"greeting","output":"Hello! How can I help?","latency_ms":420,"cost_usd":0.001}]',
  );
  final _cognitiveGoalController = TextEditingController(text: 'goal://support/triage');
  final _cognitiveAgentRefController = TextEditingController();
  final _cognitiveRunIdController = TextEditingController();
  final _contextSearchController = TextEditingController();
  Map<String, dynamic>? _cognitiveRun;
  List<dynamic> _cognitiveEvents = [];
  Map<String, dynamic>? _contextSnapshot;
  final _experienceAgentController = TextEditingController();
  final _experienceExecutionController = TextEditingController();
  final _experienceEvaluationController = TextEditingController();
  final _experienceWorkloadRefController = TextEditingController(text: 'benchmark://support-quality/v1');
  final _experienceWorkloadHashController = TextEditingController();
  final _experienceIdController = TextEditingController();
  final _lessonTypeController = TextEditingController(text: 'response.quality');
  final _lessonStatementController = TextEditingController();
  final _fitnessProfileController = TextEditingController(text: 'support-quality');
  final _fitnessVersionController = TextEditingController(text: '1.0.0');
  final _fitnessSamplesController = TextEditingController(text: '3');
  List<dynamic> _experienceItems = [];
  List<dynamic> _lessonItems = [];
  List<dynamic> _fitnessItems = [];
  final _experimentAgentController = TextEditingController();
  final _experimentParentVersionController = TextEditingController(text: '1.0.0');
  final _experimentCandidateVersionController = TextEditingController(text: '1.1.0');
  final _experimentParentRunController = TextEditingController();
  final _experimentCandidateRunController = TextEditingController();
  final _experimentWorkloadRefController = TextEditingController(text: 'benchmark://support-quality/v1');
  final _experimentWorkloadHashController = TextEditingController();
  final _experimentComparisonIdController = TextEditingController();
  Map<String, dynamic>? _experimentComparison;
  Map<String, dynamic>? _createdAgent;
  List<dynamic> _versions = [];
  Map<String, dynamic>? _selectedVersion;
  Map<String, dynamic>? _execution;
  Map<String, dynamic>? _job;
  List<dynamic> _jobEvents = [];
  List<dynamic> _profileVersions = [];
  List<dynamic> _evaluationRuns = [];
  Map<String, dynamic>? _evaluationRun;

  @override
  void initState() {
    super.initState();
    _manifestController.text = _starterManifest();
    _profileController.text = _starterEvaluationProfile();
    _restoreSession();
  }

  @override
  void dispose() {
    _api?.close();
    _manifestController.dispose();
    _agentIdController.dispose();
    _executionIdController.dispose();
    _jobVersionController.dispose();
    _jobInputController.dispose();
    _jobIdController.dispose();
    _profileIdController.dispose();
    _profileVersionController.dispose();
    _profileController.dispose();
    _evaluationResultsController.dispose();
    _cognitiveGoalController.dispose();
    _cognitiveAgentRefController.dispose();
    _cognitiveRunIdController.dispose();
    _contextSearchController.dispose();
    _experienceAgentController.dispose();
    _experienceExecutionController.dispose();
    _experienceEvaluationController.dispose();
    _experienceWorkloadRefController.dispose();
    _experienceWorkloadHashController.dispose();
    _experienceIdController.dispose();
    _lessonTypeController.dispose();
    _lessonStatementController.dispose();
    _fitnessProfileController.dispose();
    _fitnessVersionController.dispose();
    _fitnessSamplesController.dispose();
    _experimentAgentController.dispose();
    _experimentParentVersionController.dispose();
    _experimentCandidateVersionController.dispose();
    _experimentParentRunController.dispose();
    _experimentCandidateRunController.dispose();
    _experimentWorkloadRefController.dispose();
    _experimentWorkloadHashController.dispose();
    _experimentComparisonIdController.dispose();
    super.dispose();
  }

  String _starterManifest() => JsonEncoder.withIndent('  ').convert({
        'schema_version': '1.0.0',
        'agent_id': 'sample-support-agent',
        'version': '1.0.0',
        'name': 'Support assistant',
        'description': 'A governed support assistant agent.',
        'purpose': 'Answer support questions using approved knowledge and tools.',
        'behavior': {
          'objective': 'Provide accurate, concise support answers and ask for clarification when needed.',
          'instructions_ref': 'agentbox://instructions/support-assistant',
          'max_steps': 8,
          'timeout_seconds': 120,
        },
        'model_binding': {
          'provider_id': 'openai-compatible',
          'model_id': 'gpt-4o-mini',
        },
        'tools': <dynamic>[],
        'permissions': {
          'tool_scopes': <String>[],
          'data_scopes': <String>[],
        },
        'provenance': {
          'origin': 'authored',
          'created_at': DateTime.now().toUtc().toIso8601String(),
        },
        'runtime_targets': ['server'],
        'tags': ['starter'],
      });

  String _starterEvaluationProfile() => JsonEncoder.withIndent('  ').convert({
        'schema_version': '1.0.0',
        'profile_id': 'support-quality',
        'version': '1.0.0',
        'name': 'Support response quality',
        'description': 'Deterministic checks for support agent outputs.',
        'scoring': {
          'correctness_weight': 0.7,
          'latency_weight': 0.2,
          'cost_weight': 0.1,
        },
        'gates': {
          'min_case_pass_rate': 0.9,
          'max_avg_latency_ms': 3000,
          'max_total_cost_usd': 0.05,
        },
        'cases': [
          {
            'case_id': 'greeting',
            'description': 'Response contains a greeting',
            'input': {'message': 'Hello'},
            'assertions': [
              {'type': 'contains', 'expected_value': 'Hello'},
            ],
          },
        ],
      });

  Future<void> _restoreSession() async {
    if (!_oidcConfig.isConfigured || _apiBaseUrl.isEmpty) return;
    final token = await _oidcAuth.accessToken();
    if (!mounted) return;
    if (token != null && _apiBaseUrl.isNotEmpty) {
      setState(() {
        _signedIn = true;
        _api = _newApiClient();
      });
    }
  }

  AgentBoxApiClient _newApiClient() => AgentBoxApiClient(
        baseUrl: _apiBaseUrl,
        accessTokenProvider: _oidcAuth.accessToken,
      );

  void _setNotice(String message, {bool error = false}) {
    if (!mounted) return;
    setState(() {
      _notice = message;
      _noticeIsError = error;
    });
  }

  Future<void> _signIn() async {
    if (!_oidcConfig.isConfigured) {
      _setNotice('Configure the OIDC issuer, mobile client ID, and redirect URI.', error: true);
      return;
    }
    if (_apiBaseUrl.isEmpty) {
      _setNotice('Configure AGENTBOX_API_BASE_URL before signing in.', error: true);
      return;
    }
    setState(() => _busy = true);
    try {
      await _oidcAuth.signIn();
      final token = await _oidcAuth.accessToken();
      if (token == null) throw StateError('Identity provider did not establish a usable session.');
      if (!mounted) return;
      setState(() {
        _api = _newApiClient();
        _signedIn = true;
      });
      _setNotice('Signed in. AgentBox API is ready.');
    } catch (error) {
      _setNotice('Sign-in failed: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _signOut() async {
    await _oidcAuth.signOut();
    _api?.close();
    if (!mounted) return;
    setState(() {
      _api = null;
      _signedIn = false;
      _versions = [];
      _selectedVersion = null;
      _execution = null;
      _job = null;
      _jobEvents = [];
      _profileVersions = [];
      _evaluationRuns = [];
      _evaluationRun = null;
      _cognitiveRun = null;
      _cognitiveEvents = [];
      _contextSnapshot = null;
      _experienceItems = [];
      _lessonItems = [];
      _fitnessItems = [];
      _experimentComparison = null;
      _jobIdController.clear();
      _cognitiveRunIdController.clear();
    });
    _setNotice('Signed out. Secure tokens were cleared.');
  }

  Future<void> _createAgent() async {
    final api = _api;
    if (!_signedIn || api == null) return;
    setState(() => _busy = true);
    try {
      final decoded = jsonDecode(_manifestController.text);
      if (decoded is! Map<String, dynamic>) {
        throw const FormatException('Manifest must be a JSON object.');
      }
      final result = await api.createAgent(decoded);
      if (!mounted) return;
      setState(() {
        _createdAgent = result;
        _agentIdController.text = result['agent_id']?.toString() ?? '';
      });
      _setNotice('Agent version registered.');
    } catch (error) {
      _setNotice('Could not register agent: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadVersions() async {
    final api = _api;
    final agentId = _agentIdController.text.trim();
    if (!_signedIn || api == null || agentId.isEmpty) return;
    setState(() => _busy = true);
    try {
      final result = await api.listAgentVersions(agentId);
      final items = result['items'];
      if (!mounted) return;
      setState(() {
        _versions = items is List ? items : [];
        _selectedVersion = null;
      });
      _setNotice('Loaded ${_versions.length} version(s).');
    } catch (error) {
      _setNotice('Could not load versions: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadVersion(String version) async {
    final api = _api;
    if (!_signedIn || api == null) return;
    setState(() => _busy = true);
    try {
      final result = await api.getAgentVersion(_agentIdController.text.trim(), version);
      if (!mounted) return;
      setState(() => _selectedVersion = result);
    } catch (error) {
      _setNotice('Could not load version: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _newIdempotencyKey() {
    final random = Random.secure();
    return List.generate(
      16,
      (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'),
    ).join();
  }

  Future<void> _submitJob() async {
    final api = _api;
    final agentId = _agentIdController.text.trim();
    final version = _jobVersionController.text.trim();
    if (!_signedIn || api == null || agentId.isEmpty || version.isEmpty) return;
    setState(() => _busy = true);
    try {
      final input = jsonDecode(_jobInputController.text);
      final result = await api.submitExecutionJob(
        agentId: agentId,
        version: version,
        input: input,
        maxOutputTokens: 1024,
        idempotencyKey: _newIdempotencyKey(),
      );
      if (!mounted) return;
      setState(() {
        _job = result;
        _jobIdController.text = result['job_id']?.toString() ?? '';
        _jobEvents = [];
      });
      _setNotice('Execution job accepted. Worker completion is asynchronous.');
    } catch (error) {
      _setNotice('Could not submit execution job: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _inspectJob() async {
    final api = _api;
    final jobId = _jobIdController.text.trim();
    if (!_signedIn || api == null || jobId.isEmpty) return;
    setState(() => _busy = true);
    try {
      final results = await Future.wait([
        api.getExecutionJob(jobId),
        api.listExecutionJobEvents(jobId),
      ]);
      if (!mounted) return;
      final events = results[1]['items'];
      setState(() {
        _job = results[0];
        _jobEvents = events is List ? events : [];
      });
      _setNotice('Execution job state refreshed.');
    } catch (error) {
      _setNotice('Could not inspect execution job: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _cancelJob() async {
    final api = _api;
    final jobId = _jobIdController.text.trim();
    if (!_signedIn || api == null || jobId.isEmpty) return;
    setState(() => _busy = true);
    try {
      final result = await api.cancelExecutionJob(jobId);
      if (!mounted) return;
      setState(() => _job = result);
      _setNotice('Cancellation requested. Refresh to observe the confirmed terminal state.');
    } catch (error) {
      _setNotice('Could not request cancellation: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _saveEvaluationProfile() async {
    final api = _api;
    if (!_signedIn || api == null) return;
    setState(() => _busy = true);
    try {
      final decoded = jsonDecode(_profileController.text);
      if (decoded is! Map<String, dynamic>) {
        throw const FormatException('Evaluation profile must be a JSON object.');
      }
      final result = await api.createEvaluationProfile(decoded);
      if (!mounted) return;
      setState(() {
        _profileIdController.text = result['profile_id']?.toString() ?? '';
        _profileVersionController.text = result['version']?.toString() ?? '';
      });
      _setNotice('Immutable evaluation profile version registered.');
    } catch (error) {
      _setNotice('Could not register evaluation profile: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadEvaluationProfiles() async {
    final api = _api;
    final profileId = _profileIdController.text.trim();
    if (!_signedIn || api == null || profileId.isEmpty) return;
    setState(() => _busy = true);
    try {
      final result = await api.listEvaluationProfileVersions(profileId);
      if (!mounted) return;
      final items = result['items'];
      setState(() => _profileVersions = items is List ? items : []);
      _setNotice('Loaded ${_profileVersions.length} profile version(s).');
    } catch (error) {
      _setNotice('Could not load evaluation profiles: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadEvaluationProfile(String version) async {
    final api = _api;
    if (!_signedIn || api == null) return;
    setState(() => _busy = true);
    try {
      final result = await api.getEvaluationProfile(
        _profileIdController.text.trim(),
        version,
      );
      if (!mounted) return;
      setState(() {
        _profileVersionController.text = version;
        _profileController.text = JsonEncoder.withIndent('  ').convert(result['profile']);
      });
    } catch (error) {
      _setNotice('Could not load profile version: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _runEvaluation() async {
    final api = _api;
    final agentId = _agentIdController.text.trim();
    final agentVersion = _jobVersionController.text.trim();
    if (!_signedIn || api == null || agentId.isEmpty || agentVersion.isEmpty) return;
    setState(() => _busy = true);
    try {
      final decoded = jsonDecode(_evaluationResultsController.text);
      if (decoded is! List) {
        throw const FormatException('Evaluation results must be a JSON array.');
      }
      final result = await api.submitEvaluation(
        agentId: agentId,
        version: agentVersion,
        profileId: _profileIdController.text.trim(),
        profileVersion: _profileVersionController.text.trim(),
        results: decoded,
      );
      if (!mounted) return;
      setState(() => _evaluationRun = result);
      _setNotice('Scored submitted outputs. This API does not execute the agent.');
    } catch (error) {
      _setNotice('Could not score evaluation: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadEvaluationHistory() async {
    final api = _api;
    final agentId = _agentIdController.text.trim();
    final version = _jobVersionController.text.trim();
    if (!_signedIn || api == null || agentId.isEmpty || version.isEmpty) return;
    setState(() => _busy = true);
    try {
      final result = await api.listEvaluations(agentId, version);
      if (!mounted) return;
      final items = result['items'];
      setState(() => _evaluationRuns = items is List ? items : []);
      _setNotice('Loaded ${_evaluationRuns.length} evaluation run(s).');
    } catch (error) {
      _setNotice('Could not load evaluation history: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadExecution() async {
    final api = _api;
    final executionId = _executionIdController.text.trim();
    if (!_signedIn || api == null || executionId.isEmpty) return;
    setState(() => _busy = true);
    try {
      final result = await api.getExecution(executionId);
      if (!mounted) return;
      setState(() => _execution = result);
      _setNotice('Execution record loaded.');
    } catch (error) {
      _setNotice('Could not load execution: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            Container(
              width: 30,
              height: 30,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.primary,
                borderRadius: BorderRadius.circular(9),
              ),
              child: const Text('A', style: TextStyle(color: Color(0xFF0A1020), fontWeight: FontWeight.w900)),
            ),
            const SizedBox(width: 10),
            const Text('AgentBox'),
          ],
        ),
        actions: [
          if (_signedIn)
            IconButton(
              tooltip: 'Sign out',
              onPressed: _busy ? null : _signOut,
              icon: const Icon(Icons.logout),
            )
          else
            const Padding(
              padding: EdgeInsets.only(right: 16),
              child: Icon(Icons.account_circle_outlined),
            ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            if (_notice != null)
              Container(
                width: double.infinity,
                margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: _noticeIsError ? const Color(0xFF351D27) : const Color(0xFF102B27),
                  border: Border.all(color: _noticeIsError ? const Color(0xFF76414D) : const Color(0xFF2A6959)),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Text(_notice!, style: TextStyle(color: _noticeIsError ? const Color(0xFFFFB2B2) : const Color(0xFF8CE0C0), fontSize: 12)),
              ),
            Expanded(
              child: IndexedStack(
                index: _selectedIndex,
                children: [
                  _buildOverview(),
                  _buildAgentStudio(),
                  _buildExecution(),
                  _buildEvaluation(),
                  _buildCognitiveRuntime(),
                  _buildLearningExplorer(),
                  _buildExperimentExplorer(),
                ],
              ),
            ),
          ],
        ),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selectedIndex,
        onDestinationSelected: (index) => setState(() => _selectedIndex = index),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.dashboard_outlined), selectedIcon: Icon(Icons.dashboard), label: 'Overview'),
          NavigationDestination(icon: Icon(Icons.hub_outlined), selectedIcon: Icon(Icons.hub), label: 'Agent Studio'),
          NavigationDestination(icon: Icon(Icons.play_circle_outline), selectedIcon: Icon(Icons.play_circle), label: 'Execution'),
          NavigationDestination(icon: Icon(Icons.fact_check_outlined), selectedIcon: Icon(Icons.fact_check), label: 'Evaluation'),
          NavigationDestination(icon: Icon(Icons.account_tree_outlined), selectedIcon: Icon(Icons.account_tree), label: 'Cognition'),
          NavigationDestination(icon: Icon(Icons.school_outlined), selectedIcon: Icon(Icons.school), label: 'Learning'),
          NavigationDestination(icon: Icon(Icons.compare_arrows), selectedIcon: Icon(Icons.compare_arrows), label: 'Experiments'),
        ],
      ),
    );
  }

  Widget _buildOverview() {
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        const Text('AGENT OPERATING ENVIRONMENT', style: TextStyle(color: Color(0xFF8BB7FF), fontSize: 10, letterSpacing: 2, fontWeight: FontWeight.w800)),
        const SizedBox(height: 12),
        Text('Build. Run. Learn. Govern.', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Text('One AgentBox platform across your devices. The backend remains the authority for identity, permissions, and execution.', style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant, height: 1.5)),
        const SizedBox(height: 24),
        _pillarCard('01', 'AI Intelligence', 'Intent, context, cognition, verification, and memory.', Icons.psychology_outlined),
        _pillarCard('02', 'Agent Factory', 'Create immutable agent manifests and inspect versions.', Icons.hub_outlined),
        _pillarCard('03', 'Application Factory', 'Compose governed capabilities and workflows.', Icons.widgets_outlined),
        _pillarCard('04', 'Execution Engine', 'Inspect execution records and operational outcomes.', Icons.play_circle_outline),
        _pillarCard('05', 'Platform Foundation', 'Identity, tenancy, authorization, audit, and persistence.', Icons.security_outlined),
        const SizedBox(height: 18),
        if (!_signedIn) ...[
          FilledButton.icon(
            onPressed: _busy ? null : _signIn,
            icon: _busy ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)) : const Icon(Icons.login),
            label: Text(_oidcConfig.isConfigured ? 'Sign in with your organization' : 'Configure OIDC to sign in'),
          ),
          const SizedBox(height: 8),
          Text('API: ${_apiBaseUrl.isEmpty ? "not configured" : _apiBaseUrl}', style: const TextStyle(color: Color(0xFF91A3BE), fontSize: 11)),
        ] else
          const Chip(avatar: Icon(Icons.verified_user_outlined, size: 16), label: Text('Authenticated session')),
      ],
    );
  }

  Widget _pillarCard(String number, String title, String description, IconData icon) {
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 15, vertical: 7),
        leading: CircleAvatar(backgroundColor: const Color(0xFF243A59), child: Icon(icon, size: 20)),
        title: Text(title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13)),
        subtitle: Padding(padding: const EdgeInsets.only(top: 5), child: Text(description, style: const TextStyle(fontSize: 11, height: 1.4))),
        trailing: Text(number, style: const TextStyle(color: Color(0xFF8BB7FF), fontWeight: FontWeight.w800, fontSize: 11)),
      ),
    );
  }

  Widget _buildAgentStudio() {
    return ListView(
      padding: const EdgeInsets.all(18),
      children: [
        const Text('AGENT FACTORY', style: TextStyle(color: Color(0xFF8BB7FF), fontSize: 10, letterSpacing: 2, fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Text('Agent Studio', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        const Text('Register a versioned agent or inspect an existing agent registry entry.', style: TextStyle(color: Color(0xFF9AAAC2), height: 1.5)),
        const SizedBox(height: 18),
        _sectionCard(
          title: 'Register agent version',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(controller: _manifestController, minLines: 12, maxLines: 22, style: const TextStyle(fontFamily: 'monospace', fontSize: 11), decoration: const InputDecoration(labelText: 'Agent manifest · JSON', alignLabelWithHint: true)),
              const SizedBox(height: 12),
              FilledButton(onPressed: !_signedIn || _busy ? null : _createAgent, child: Text(_busy ? 'Working…' : 'Register immutable version')),
              if (_createdAgent != null) ...[
                const SizedBox(height: 12),
                _jsonResult(_createdAgent!),
              ],
            ],
          ),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Inspect versions',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(controller: _agentIdController, decoration: const InputDecoration(labelText: 'Agent ID', hintText: 'sample-support-agent')),
              const SizedBox(height: 10),
              OutlinedButton(onPressed: !_signedIn || _busy ? null : _loadVersions, child: const Text('Load versions')),
              if (_versions.isNotEmpty) ...[
                const SizedBox(height: 8),
                for (final item in _versions)
                  if (item is Map<String, dynamic>)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text('Version ${item['version'] ?? 'unknown'}'),
                      subtitle: Text(item['artifact_id']?.toString() ?? ''),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: _busy ? null : () => _loadVersion(item['version'].toString()),
                    ),
              ],
              if (_selectedVersion != null) ...[
                const SizedBox(height: 10),
                _jsonResult(_selectedVersion!),
              ],
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildExecution() {
    return ListView(
      padding: const EdgeInsets.all(18),
      children: [
        const Text('EXECUTION ENGINE', style: TextStyle(color: Color(0xFF8BB7FF), fontSize: 10, letterSpacing: 2, fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Text('Execution inspector', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        const Text('Retrieve a real execution record by ID. No simulated run data is shown.', style: TextStyle(color: Color(0xFF9AAAC2), height: 1.5)),
        const SizedBox(height: 18),
        _sectionCard(
          title: 'Submit durable agent job',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(controller: _agentIdController, decoration: const InputDecoration(labelText: 'Agent ID')),
              const SizedBox(height: 10),
              TextField(controller: _jobVersionController, decoration: const InputDecoration(labelText: 'Immutable version')),
              const SizedBox(height: 10),
              TextField(controller: _jobInputController, minLines: 3, maxLines: 6, style: const TextStyle(fontFamily: 'monospace', fontSize: 11), decoration: const InputDecoration(labelText: 'Input · JSON', alignLabelWithHint: true)),
              const SizedBox(height: 10),
              const Text('Requires a trusted worker and matching server-side provider allowlist.', style: TextStyle(color: Color(0xFF9AAAC2), fontSize: 11, height: 1.5)),
              const SizedBox(height: 10),
              FilledButton(onPressed: !_signedIn || _busy ? null : _submitJob, child: const Text('Submit execution job')),
              if (_job != null) ...[
                const SizedBox(height: 12),
                _jsonResult(_job!),
              ],
            ],
          ),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Job lifecycle',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(controller: _jobIdController, decoration: const InputDecoration(labelText: 'Execution job ID')),
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(child: OutlinedButton(onPressed: !_signedIn || _busy ? null : _inspectJob, child: const Text('Refresh status'))),
                  const SizedBox(width: 8),
                  Expanded(child: OutlinedButton(onPressed: !_signedIn || _busy ? null : _cancelJob, child: const Text('Request cancel'))),
                ],
              ),
              if (_jobEvents.isNotEmpty) ...[
                const SizedBox(height: 12),
                const Text('Append-only lifecycle events', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 12)),
                const SizedBox(height: 8),
                _jsonResult({'items': _jobEvents}),
              ],
            ],
          ),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Find synchronous execution',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(controller: _executionIdController, decoration: const InputDecoration(labelText: 'Execution ID')),
              const SizedBox(height: 10),
              FilledButton(onPressed: !_signedIn || _busy ? null : _loadExecution, child: const Text('Inspect execution')),
              if (_execution != null) ...[
                const SizedBox(height: 12),
                _jsonResult(_execution!),
              ],
            ],
          ),
        ),
      ],
    );
  }

  Future<void> _createCognitiveRun() async {
    final api = _api;
    if (!_signedIn || api == null) return;
    setState(() => _busy = true);
    try {
      final result = await api.createCognitiveRun(
        goalRef: _cognitiveGoalController.text.trim(),
        agentRef: _cognitiveAgentRefController.text.trim(),
        idempotencyKey: 'cognition-${DateTime.now().microsecondsSinceEpoch}',
      );
      final run = result['run'] as Map<String, dynamic>;
      final events = await api.listCognitiveRunEvents(run['run_id'].toString());
      if (!mounted) return;
      setState(() {
        _cognitiveRun = run;
        _cognitiveRunIdController.text = run['run_id'].toString();
        _cognitiveEvents = events['items'] as List<dynamic>? ?? [];
        _contextSnapshot = null;
      });
      _setNotice('Cognitive run created. Lifecycle records do not execute agents or grant authority.');
    } catch (error) {
      _setNotice('Could not create cognitive run: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _inspectCognitiveRun() async {
    final api = _api;
    final runId = _cognitiveRunIdController.text.trim();
    if (!_signedIn || api == null || runId.isEmpty) return;
    setState(() => _busy = true);
    try {
      final results = await Future.wait([
        api.getCognitiveRun(runId),
        api.listCognitiveRunEvents(runId),
      ]);
      if (!mounted) return;
      setState(() {
        _cognitiveRun = results[0];
        _cognitiveEvents = results[1]['items'] as List<dynamic>? ?? [];
        _contextSnapshot = null;
      });
    } catch (error) {
      _setNotice('Could not inspect cognitive run: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _assembleCognitiveContext() async {
    final api = _api;
    final runId = _cognitiveRunIdController.text.trim();
    if (!_signedIn || api == null || runId.isEmpty) return;
    setState(() => _busy = true);
    try {
      final search = _contextSearchController.text.trim();
      final result = await api.assembleCognitiveContext(
        runId: runId,
        request: {
          if (search.isNotEmpty) 'search': search,
          'include_inferences': false,
          'limit': 8,
        },
        idempotencyKey: 'context-${DateTime.now().microsecondsSinceEpoch}',
      );
      final events = await api.listCognitiveRunEvents(runId);
      if (!mounted) return;
      setState(() {
        _contextSnapshot = result;
        _cognitiveEvents = events['items'] as List<dynamic>? ?? [];
      });
      _setNotice('Context snapshot assembled from policy-eligible tenant evidence.');
    } catch (error) {
      _setNotice('Could not assemble context: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _createExperience() async {
    final api = _api;
    if (!_signedIn || api == null) return;
    setState(() => _busy = true);
    try {
      final result = await api.createExperience(
        executionId: _experienceExecutionController.text.trim(),
        evaluationRunId: _experienceEvaluationController.text.trim(),
        workloadRef: _experienceWorkloadRefController.text.trim(),
        workloadHash: _experienceWorkloadHashController.text.trim(),
      );
      if (!mounted) return;
      setState(() => _experienceIdController.text = result['experience_id'].toString());
      _setNotice('Experience saved. Caller-asserted linkage remains unverified.');
    } catch (error) {
      _setNotice('Could not record experience: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadExperiences() async {
    final api = _api;
    if (!_signedIn || api == null || _experienceAgentController.text.trim().isEmpty) return;
    setState(() => _busy = true);
    try {
      final result = await api.listExperiences(_experienceAgentController.text.trim());
      if (!mounted) return;
      setState(() => _experienceItems = result['items'] as List<dynamic>? ?? []);
    } catch (error) {
      _setNotice('Could not load experiences: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _createLesson() async {
    final api = _api;
    final experienceId = _experienceIdController.text.trim();
    if (!_signedIn || api == null || experienceId.isEmpty) return;
    setState(() => _busy = true);
    try {
      final result = await api.createLearningLesson(
        experienceId: experienceId,
        lesson: {
          'lesson_type': _lessonTypeController.text.trim(),
          'statement': _lessonStatementController.text.trim(),
          'evidence_refs': [experienceId],
          'source_kind': 'human_review',
          'extractor_version': 'agentbox-learning-ui/1.0.0',
          'confidence': 1.0,
        },
      );
      if (!mounted) return;
      setState(() => _lessonItems = [result, ..._lessonItems]);
      _setNotice('Proposed lesson saved with its source experience reference.');
    } catch (error) {
      _setNotice('Could not save lesson: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadLessons() async {
    final api = _api;
    final experienceId = _experienceIdController.text.trim();
    if (!_signedIn || api == null || experienceId.isEmpty) return;
    setState(() => _busy = true);
    try {
      final result = await api.listLearningLessons(experienceId);
      if (!mounted) return;
      setState(() => _lessonItems = result['items'] as List<dynamic>? ?? []);
    } catch (error) {
      _setNotice('Could not load lessons: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadFitness() async {
    final api = _api;
    if (!_signedIn || api == null) return;
    final samples = int.tryParse(_fitnessSamplesController.text.trim());
    if (samples == null || samples < 1 || samples > 1000) {
      _setNotice('Minimum samples must be an integer from 1 to 1000.', error: true);
      return;
    }
    setState(() => _busy = true);
    try {
      final result = await api.selectFitness(
        profileId: _fitnessProfileController.text.trim(),
        profileVersion: _fitnessVersionController.text.trim(),
        minSamples: samples,
      );
      if (!mounted) return;
      setState(() => _fitnessItems = result['items'] as List<dynamic>? ?? []);
    } catch (error) {
      _setNotice('Could not select fitness candidates: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _buildLearningExplorer() {
    return ListView(
      padding: const EdgeInsets.all(18),
      children: [
        const Text('AI INTELLIGENCE · AGENT EVOLUTION', style: TextStyle(color: Color(0xFF8BB7FF), fontSize: 10, letterSpacing: 2, fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Text('Experience, Learning & Fitness', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        const Text('Retain minimized execution/evaluation links, derive traceable lessons, and rank agents using repeated evidence.', style: TextStyle(color: Color(0xFF9AAAC2), height: 1.5)),
        const SizedBox(height: 18),
        _sectionCard(
          title: 'Record execution experience',
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextField(controller: _experienceExecutionController, decoration: const InputDecoration(labelText: 'Execution ID')),
            const SizedBox(height: 8),
            TextField(controller: _experienceEvaluationController, decoration: const InputDecoration(labelText: 'Evaluation run ID')),
            const SizedBox(height: 8),
            TextField(controller: _experienceWorkloadRefController, decoration: const InputDecoration(labelText: 'Workload reference')),
            const SizedBox(height: 8),
            TextField(controller: _experienceWorkloadHashController, maxLength: 64, decoration: const InputDecoration(labelText: 'Workload SHA-256')),
            const SizedBox(height: 8),
            FilledButton(onPressed: !_signedIn || _busy ? null : _createExperience, child: const Text('Record experience')),
            const Divider(height: 24),
            TextField(controller: _experienceAgentController, decoration: const InputDecoration(labelText: 'Agent ID for experience history')),
            OutlinedButton(onPressed: !_signedIn || _busy ? null : _loadExperiences, child: const Text('Load experiences')),
            if (_experienceItems.isNotEmpty) _jsonResult({'items': _experienceItems}),
            const Text('Experience links are caller-asserted and remain unverified; they are not trusted execution proof.', style: TextStyle(color: Color(0xFFFFD28A), fontSize: 11, height: 1.5)),
          ]),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Traceable learning lessons',
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextField(controller: _experienceIdController, decoration: const InputDecoration(labelText: 'Source experience ID')),
            const SizedBox(height: 8),
            TextField(controller: _lessonTypeController, decoration: const InputDecoration(labelText: 'Normalized lesson type')),
            const SizedBox(height: 8),
            TextField(controller: _lessonStatementController, maxLines: 3, decoration: const InputDecoration(labelText: 'Lesson statement')),
            const SizedBox(height: 8),
            FilledButton(onPressed: !_signedIn || _busy ? null : _createLesson, child: const Text('Save proposed lesson')),
            OutlinedButton(onPressed: !_signedIn || _busy ? null : _loadLessons, child: const Text('Load lessons')),
            if (_lessonItems.isNotEmpty) _jsonResult({'items': _lessonItems}),
            const Text('Lessons remain proposed and source-linked. They do not rewrite agent configuration or grant authority.', style: TextStyle(color: Color(0xFF9AAAC2), fontSize: 11, height: 1.5)),
          ]),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Evidence-based fitness selection',
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextField(controller: _fitnessProfileController, decoration: const InputDecoration(labelText: 'Evaluation profile ID')),
            const SizedBox(height: 8),
            TextField(controller: _fitnessVersionController, decoration: const InputDecoration(labelText: 'Profile version')),
            const SizedBox(height: 8),
            TextField(controller: _fitnessSamplesController, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'Minimum sample count')),
            const SizedBox(height: 8),
            FilledButton(onPressed: !_signedIn || _busy ? null : _loadFitness, child: const Text('Select fitness candidates')),
            if (_fitnessItems.isNotEmpty) _jsonResult({'items': _fitnessItems}),
            const Text('Historical aggregates over unverified caller-asserted records are prioritization signals only—not benchmark proof, gate results, or promotion approval.', style: TextStyle(color: Color(0xFFFFD28A), fontSize: 11, height: 1.5)),
          ]),
        ),
      ],
    );
  }

  Future<void> _compareExperiment() async {
    final api = _api;
    if (!_signedIn || api == null) return;
    final hash = _experimentWorkloadHashController.text.trim();
    if (!RegExp(r'^[a-fA-F0-9]{64}$').hasMatch(hash)) {
      _setNotice('Workload SHA-256 must contain exactly 64 hexadecimal characters.', error: true);
      return;
    }
    setState(() => _busy = true);
    try {
      final result = await api.compareAgentExperiment(
        agentId: _experimentAgentController.text.trim(),
        parentVersion: _experimentParentVersionController.text.trim(),
        candidateVersion: _experimentCandidateVersionController.text.trim(),
        parentEvaluationRunId: _experimentParentRunController.text.trim(),
        candidateEvaluationRunId: _experimentCandidateRunController.text.trim(),
        workloadRef: _experimentWorkloadRefController.text.trim(),
        workloadHash: hash,
      );
      if (!mounted) return;
      setState(() {
        _experimentComparison = result;
        _experimentComparisonIdController.text = result['comparison_id'].toString();
      });
      _setNotice('Comparison saved. Workload identity remains caller-asserted; result is advisory only.');
    } catch (error) {
      _setNotice('Could not compare parent and candidate: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _buildCognitiveRuntime() {
    const stages = [
      'intent', 'context', 'cognition', 'capability', 'responsibility',
      'execution', 'observation', 'verification', 'memory',
    ];
    final seen = _cognitiveEvents
        .whereType<Map<String, dynamic>>()
        .map((event) => event['stage']?.toString())
        .toSet();
    return ListView(
      padding: const EdgeInsets.all(18),
      children: [
        const Text('AI INTELLIGENCE', style: TextStyle(color: Color(0xFF8BB7FF), fontSize: 10, letterSpacing: 2, fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Text('Cognitive Runtime Explorer', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        const Text('Inspect tenant-scoped run state, append-only lifecycle events, and minimized context snapshots. This screen is not an execution or authorization surface.', style: TextStyle(color: Color(0xFF9AAAC2), height: 1.5)),
        const SizedBox(height: 18),
        _sectionCard(
          title: 'Create cognitive run',
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextField(controller: _cognitiveGoalController, decoration: const InputDecoration(labelText: 'Goal reference')),
            const SizedBox(height: 8),
            TextField(controller: _cognitiveAgentRefController, decoration: const InputDecoration(labelText: 'Immutable agent reference · id@version')),
            const SizedBox(height: 10),
            FilledButton(onPressed: !_signedIn || _busy ? null : _createCognitiveRun, child: const Text('Create run')),
          ]),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Run inspector',
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextField(controller: _cognitiveRunIdController, decoration: const InputDecoration(labelText: 'Cognitive run ID')),
            const SizedBox(height: 8),
            OutlinedButton(onPressed: !_signedIn || _busy ? null : _inspectCognitiveRun, child: const Text('Inspect run and events')),
            if (_cognitiveRun != null) _jsonResult(_cognitiveRun!),
          ]),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Nine-stage lifecycle',
          child: Column(children: [
            for (var i = 0; i < stages.length; i++)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: CircleAvatar(radius: 13, child: Text('${i + 1}', style: const TextStyle(fontSize: 11))),
                title: Text(stages[i]),
                trailing: Icon(seen.contains(stages[i]) ? Icons.check_circle : Icons.radio_button_unchecked, color: seen.contains(stages[i]) ? Colors.greenAccent : Colors.white38),
                subtitle: Text(seen.contains(stages[i]) ? 'Event recorded' : 'Not yet recorded'),
              ),
            if (_cognitiveEvents.isNotEmpty) _jsonResult({'events': _cognitiveEvents}),
          ]),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Context snapshot',
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextField(controller: _contextSearchController, decoration: const InputDecoration(labelText: 'Optional entity search')),
            const SizedBox(height: 8),
            const Text('Only currently valid observed facts and human-validated entities are included by default. Inferences are disabled.', style: TextStyle(color: Color(0xFF9AAAC2), fontSize: 11, height: 1.5)),
            const SizedBox(height: 8),
            FilledButton(onPressed: !_signedIn || _busy || _cognitiveRunIdController.text.trim().isEmpty ? null : _assembleCognitiveContext, child: const Text('Assemble context')),
            if (_contextSnapshot != null) _jsonResult(_contextSnapshot!),
          ]),
        ),
      ],
    );
  }

  Widget _buildEvaluation() {
    return ListView(
      padding: const EdgeInsets.all(18),
      children: [
        const Text('QUALITY & VERIFICATION', style: TextStyle(color: Color(0xFF8BB7FF), fontSize: 10, letterSpacing: 2, fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Text('Evaluation Explorer', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        const Text('Create immutable deterministic profiles, score supplied outputs, and inspect quality gates. Scoring does not execute an agent.', style: TextStyle(color: Color(0xFF9AAAC2), height: 1.5)),
        const SizedBox(height: 18),
        _sectionCard(
          title: 'Evaluation profile · JSON',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(controller: _profileController, minLines: 12, maxLines: 20, style: const TextStyle(fontFamily: 'monospace', fontSize: 11), decoration: const InputDecoration(alignLabelWithHint: true)),
              const SizedBox(height: 10),
              const Text('Profiles contain deterministic assertions, scoring weights, and gates. They cannot contain executable evaluator code.', style: TextStyle(color: Color(0xFF9AAAC2), fontSize: 11, height: 1.5)),
              const SizedBox(height: 10),
              FilledButton(onPressed: !_signedIn || _busy ? null : _saveEvaluationProfile, child: const Text('Register immutable profile')),
            ],
          ),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Profile versions',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(controller: _profileIdController, decoration: const InputDecoration(labelText: 'Profile ID')),
              const SizedBox(height: 8),
              OutlinedButton(onPressed: !_signedIn || _busy ? null : _loadEvaluationProfiles, child: const Text('Load profile versions')),
              for (final item in _profileVersions)
                if (item is Map<String, dynamic>)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text('Version ${item['version'] ?? 'unknown'}'),
                    subtitle: Text(item['created_at']?.toString() ?? ''),
                    onTap: _busy ? null : () => _loadEvaluationProfile(item['version'].toString()),
                  ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Score submitted outputs',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(controller: _agentIdController, decoration: const InputDecoration(labelText: 'Agent ID')),
              const SizedBox(height: 8),
              TextField(controller: _jobVersionController, decoration: const InputDecoration(labelText: 'Agent version')),
              const SizedBox(height: 8),
              TextField(controller: _profileVersionController, decoration: const InputDecoration(labelText: 'Profile version')),
              const SizedBox(height: 8),
              TextField(controller: _evaluationResultsController, minLines: 5, maxLines: 10, style: const TextStyle(fontFamily: 'monospace', fontSize: 11), decoration: const InputDecoration(labelText: 'Measured case results · JSON array', alignLabelWithHint: true)),
              const SizedBox(height: 8),
              const Text('Each result supplies case_id, output, latency_ms, and cost_usd. Missing case outputs fail their case.', style: TextStyle(color: Color(0xFF9AAAC2), fontSize: 11, height: 1.5)),
              const SizedBox(height: 10),
              FilledButton(onPressed: !_signedIn || _busy ? null : _runEvaluation, child: const Text('Score evaluation')),
              if (_evaluationRun != null) ...[
                const SizedBox(height: 12),
                _jsonResult(_evaluationRun!),
              ],
            ],
          ),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Evaluation history',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              OutlinedButton(onPressed: !_signedIn || _busy ? null : _loadEvaluationHistory, child: const Text('Load runs for agent version')),
              for (final item in _evaluationRuns)
                if (item is Map<String, dynamic>)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text('${item['passed'] == true ? 'PASS' : 'FAIL'} · score ${item['score'] ?? '—'}'),
                    subtitle: Text(item['run_id']?.toString() ?? ''),
                    onTap: () async {
                      final api = _api;
                      if (api == null) return;
                      setState(() => _busy = true);
                      try {
                        final run = await api.getEvaluation(item['run_id'].toString());
                        if (!mounted) return;
                        setState(() => _evaluationRun = run);
                      } catch (error) {
                        _setNotice('Could not load evaluation run: $error', error: true);
                      } finally {
                        if (mounted) setState(() => _busy = false);
                      }
                    },
                  ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _sectionCard({required String title, required Widget child}) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(15),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15)),
            const SizedBox(height: 14),
            child,
          ],
        ),
      ),
    );
  }

  Widget _jsonResult(Map<String, dynamic> value) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: const Color(0xFF0A1220), borderRadius: BorderRadius.circular(10), border: Border.all(color: const Color(0xFF2A3A55))),
      child: SelectableText(JsonEncoder.withIndent('  ').convert(value), style: const TextStyle(fontFamily: 'monospace', fontSize: 10, height: 1.5)),
    );
  }
  Future<void> _loadExperimentComparison() async {
    final api = _api;
    final id = _experimentComparisonIdController.text.trim();
    if (!_signedIn || api == null || id.isEmpty) return;
    setState(() => _busy = true);
    try {
      final result = await api.getAgentExperimentComparison(id);
      if (!mounted) return;
      setState(() => _experimentComparison = result);
    } catch (error) {
      _setNotice('Could not load experiment comparison: $error', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _buildExperimentExplorer() {
    return ListView(
      padding: const EdgeInsets.all(18),
      children: [
        const Text('AGENT FACTORY · CONTROLLED COMPARISON', style: TextStyle(color: Color(0xFF8BB7FF), fontSize: 10, letterSpacing: 2, fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Text('Parent–Candidate Experiment', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        const Text('Compare immutable parent and candidate versions using two evaluation runs pinned to one profile version and an asserted workload identity.', style: TextStyle(color: Color(0xFF9AAAC2), height: 1.5)),
        const SizedBox(height: 18),
        _sectionCard(
          title: 'Create comparison',
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextField(controller: _experimentAgentController, decoration: const InputDecoration(labelText: 'Agent ID')),
            const SizedBox(height: 8),
            TextField(controller: _experimentParentVersionController, decoration: const InputDecoration(labelText: 'Parent version')),
            const SizedBox(height: 8),
            TextField(controller: _experimentCandidateVersionController, decoration: const InputDecoration(labelText: 'Candidate version')),
            const SizedBox(height: 8),
            TextField(controller: _experimentParentRunController, decoration: const InputDecoration(labelText: 'Parent evaluation run ID')),
            const SizedBox(height: 8),
            TextField(controller: _experimentCandidateRunController, decoration: const InputDecoration(labelText: 'Candidate evaluation run ID')),
            const SizedBox(height: 8),
            TextField(controller: _experimentWorkloadRefController, decoration: const InputDecoration(labelText: 'Workload reference')),
            const SizedBox(height: 8),
            TextField(controller: _experimentWorkloadHashController, maxLength: 64, decoration: const InputDecoration(labelText: 'Workload SHA-256')),
            const SizedBox(height: 8),
            FilledButton(onPressed: !_signedIn || _busy ? null : _compareExperiment, child: const Text('Compare parent and candidate')),
            const Divider(height: 24),
            TextField(controller: _experimentComparisonIdController, decoration: const InputDecoration(labelText: 'Comparison ID')),
            OutlinedButton(onPressed: !_signedIn || _busy ? null : _loadExperimentComparison, child: const Text('Load comparison')),
            if (_experimentComparison != null) _jsonResult(_experimentComparison!),
          ]),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Evidence and authority boundary',
          child: const Text('The backend verifies tenant ownership, immutable candidate-to-parent lineage, exact agent versions, completed evaluation runs and profile-version identity. The supplied workload reference/hash is still caller-asserted. Results are unverified and advisory; no trusted-runner attestation, hard-gate authority or promotion action is available.', style: TextStyle(color: Color(0xFFFFD28A), fontSize: 12, height: 1.6)),
        ),
      ],
    );
  }

  Widget _buildCognitiveRuntime() {
    const stages = [
      'intent', 'context', 'cognition', 'capability', 'responsibility',
      'execution', 'observation', 'verification', 'memory',
    ];
    final seen = _cognitiveEvents
        .whereType<Map<String, dynamic>>()
        .map((event) => event['stage']?.toString())
        .toSet();
    return ListView(
      padding: const EdgeInsets.all(18),
      children: [
        const Text('AI INTELLIGENCE', style: TextStyle(color: Color(0xFF8BB7FF), fontSize: 10, letterSpacing: 2, fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Text('Cognitive Runtime Explorer', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        const Text('Inspect tenant-scoped run state, append-only lifecycle events, and minimized context snapshots. This screen is not an execution or authorization surface.', style: TextStyle(color: Color(0xFF9AAAC2), height: 1.5)),
        const SizedBox(height: 18),
        _sectionCard(
          title: 'Create cognitive run',
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextField(controller: _cognitiveGoalController, decoration: const InputDecoration(labelText: 'Goal reference')),
            const SizedBox(height: 8),
            TextField(controller: _cognitiveAgentRefController, decoration: const InputDecoration(labelText: 'Immutable agent reference · id@version')),
            const SizedBox(height: 10),
            FilledButton(onPressed: !_signedIn || _busy ? null : _createCognitiveRun, child: const Text('Create run')),
          ]),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Run inspector',
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextField(controller: _cognitiveRunIdController, decoration: const InputDecoration(labelText: 'Cognitive run ID')),
            const SizedBox(height: 8),
            OutlinedButton(onPressed: !_signedIn || _busy ? null : _inspectCognitiveRun, child: const Text('Inspect run and events')),
            if (_cognitiveRun != null) _jsonResult(_cognitiveRun!),
          ]),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Nine-stage lifecycle',
          child: Column(children: [
            for (var i = 0; i < stages.length; i++)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: CircleAvatar(radius: 13, child: Text('${i + 1}', style: const TextStyle(fontSize: 11))),
                title: Text(stages[i]),
                trailing: Icon(seen.contains(stages[i]) ? Icons.check_circle : Icons.radio_button_unchecked, color: seen.contains(stages[i]) ? Colors.greenAccent : Colors.white38),
                subtitle: Text(seen.contains(stages[i]) ? 'Event recorded' : 'Not yet recorded'),
              ),
            if (_cognitiveEvents.isNotEmpty) _jsonResult({'events': _cognitiveEvents}),
          ]),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Context snapshot',
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextField(controller: _contextSearchController, decoration: const InputDecoration(labelText: 'Optional entity search')),
            const SizedBox(height: 8),
            const Text('Only currently valid observed facts and human-validated entities are included by default. Inferences are disabled.', style: TextStyle(color: Color(0xFF9AAAC2), fontSize: 11, height: 1.5)),
            const SizedBox(height: 8),
            FilledButton(onPressed: !_signedIn || _busy || _cognitiveRunIdController.text.trim().isEmpty ? null : _assembleCognitiveContext, child: const Text('Assemble context')),
            if (_contextSnapshot != null) _jsonResult(_contextSnapshot!),
          ]),
        ),
      ],
    );
  }

  Widget _buildEvaluation() {
    return ListView(
      padding: const EdgeInsets.all(18),
      children: [
        const Text('QUALITY & VERIFICATION', style: TextStyle(color: Color(0xFF8BB7FF), fontSize: 10, letterSpacing: 2, fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Text('Evaluation Explorer', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        const Text('Create immutable deterministic profiles, score supplied outputs, and inspect quality gates. Scoring does not execute an agent.', style: TextStyle(color: Color(0xFF9AAAC2), height: 1.5)),
        const SizedBox(height: 18),
        _sectionCard(
          title: 'Evaluation profile · JSON',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(controller: _profileController, minLines: 12, maxLines: 20, style: const TextStyle(fontFamily: 'monospace', fontSize: 11), decoration: const InputDecoration(alignLabelWithHint: true)),
              const SizedBox(height: 10),
              const Text('Profiles contain deterministic assertions, scoring weights, and gates. They cannot contain executable evaluator code.', style: TextStyle(color: Color(0xFF9AAAC2), fontSize: 11, height: 1.5)),
              const SizedBox(height: 10),
              FilledButton(onPressed: !_signedIn || _busy ? null : _saveEvaluationProfile, child: const Text('Register immutable profile')),
            ],
          ),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Profile versions',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(controller: _profileIdController, decoration: const InputDecoration(labelText: 'Profile ID')),
              const SizedBox(height: 8),
              OutlinedButton(onPressed: !_signedIn || _busy ? null : _loadEvaluationProfiles, child: const Text('Load profile versions')),
              for (final item in _profileVersions)
                if (item is Map<String, dynamic>)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text('Version ${item['version'] ?? 'unknown'}'),
                    subtitle: Text(item['created_at']?.toString() ?? ''),
                    onTap: _busy ? null : () => _loadEvaluationProfile(item['version'].toString()),
                  ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Score submitted outputs',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(controller: _agentIdController, decoration: const InputDecoration(labelText: 'Agent ID')),
              const SizedBox(height: 8),
              TextField(controller: _jobVersionController, decoration: const InputDecoration(labelText: 'Agent version')),
              const SizedBox(height: 8),
              TextField(controller: _profileVersionController, decoration: const InputDecoration(labelText: 'Profile version')),
              const SizedBox(height: 8),
              TextField(controller: _evaluationResultsController, minLines: 5, maxLines: 10, style: const TextStyle(fontFamily: 'monospace', fontSize: 11), decoration: const InputDecoration(labelText: 'Measured case results · JSON array', alignLabelWithHint: true)),
              const SizedBox(height: 8),
              const Text('Each result supplies case_id, output, latency_ms, and cost_usd. Missing case outputs fail their case.', style: TextStyle(color: Color(0xFF9AAAC2), fontSize: 11, height: 1.5)),
              const SizedBox(height: 10),
              FilledButton(onPressed: !_signedIn || _busy ? null : _runEvaluation, child: const Text('Score evaluation')),
              if (_evaluationRun != null) ...[
                const SizedBox(height: 12),
                _jsonResult(_evaluationRun!),
              ],
            ],
          ),
        ),
        const SizedBox(height: 14),
        _sectionCard(
          title: 'Evaluation history',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              OutlinedButton(onPressed: !_signedIn || _busy ? null : _loadEvaluationHistory, child: const Text('Load runs for agent version')),
              for (final item in _evaluationRuns)
                if (item is Map<String, dynamic>)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text('${item['passed'] == true ? 'PASS' : 'FAIL'} · score ${item['score'] ?? '—'}'),
                    subtitle: Text(item['run_id']?.toString() ?? ''),
                    onTap: () async {
                      final api = _api;
                      if (api == null) return;
                      setState(() => _busy = true);
                      try {
                        final run = await api.getEvaluation(item['run_id'].toString());
                        if (!mounted) return;
                        setState(() => _evaluationRun = run);
                      } catch (error) {
                        _setNotice('Could not load evaluation run: $error', error: true);
                      } finally {
                        if (mounted) setState(() => _busy = false);
                      }
                    },
                  ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _sectionCard({required String title, required Widget child}) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(15),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15)),
            const SizedBox(height: 14),
            child,
          ],
        ),
      ),
    );
  }

  Widget _jsonResult(Map<String, dynamic> value) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: const Color(0xFF0A1220), borderRadius: BorderRadius.circular(10), border: Border.all(color: const Color(0xFF2A3A55))),
      child: SelectableText(JsonEncoder.withIndent('  ').convert(value), style: const TextStyle(fontFamily: 'monospace', fontSize: 10, height: 1.5)),
    );
  }
}
