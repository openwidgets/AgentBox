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
}
