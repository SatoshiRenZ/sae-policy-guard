# Threat Model Reference

Full threat taxonomy for AI-assisted crypto trading systems, derived from the
SAE whitepaper's analysis of OpenClaw and Moltbook vulnerability patterns.

## Threat Class 1: Supply-Chain / Plugin Risk

When trading execution systems become extensible (plugins, skills, bots),
attackers masquerade as legitimate trading utilities to enter the pipeline.

### 1.1 Malicious Plugin Injection
- **Attack:** Publish a plugin disguised as a trading utility (e.g., "BTC alerts",
  "DCA bot") that contains hidden execution of unauthorized trades or credential theft
- **Detection:** Obfuscated code patterns (base64, eval/exec), dynamic imports,
  unexpected network calls
- **Severity:** Critical
- **Mitigation:** Scan all plugins with `threat_audit.py` before installation.
  Allowlist approved plugins. Sandbox plugin execution.

### 1.2 Dependency Confusion
- **Attack:** Publish a package with a similar name to a legitimate trading library
  on a package registry, containing malicious code
- **Detection:** Check package source against known registries, verify publisher identity
- **Severity:** High
- **Mitigation:** Pin exact dependency versions. Use lockfiles. Verify package hashes.

### 1.3 Execution Escalation
- **Attack:** A plugin requests elevated permissions (file system access, network,
  API key access) beyond what its stated function requires
- **Detection:** Permission analysis during installation, runtime permission monitoring
- **Severity:** High
- **Mitigation:** Enforce principle of least privilege. Plugins get read-only access
  to market data by default. Execution authority requires explicit policy grant.

### 1.4 Update Poisoning
- **Attack:** A previously safe plugin pushes a malicious update that adds credential
  exfiltration or unauthorized trading
- **Detection:** Diff analysis on plugin updates, behavioral change detection
- **Severity:** High
- **Mitigation:** Review plugin updates before applying. Pin versions in production.
  Alert on unexpected behavioral changes.

## Threat Class 2: Prompt Injection / Narrative Manipulation

Agent interconnection and content propagation amplify manipulation.
Trading systems must assume narrative sources can be adversarially controlled.

### 2.1 Direct Prompt Injection
- **Attack:** Inject instructions into data feeds consumed by the trading agent
  (e.g., hidden text in market data, manipulated API responses)
- **Detection:** Input sanitization, anomaly detection on data feed content
- **Severity:** Critical
- **Mitigation:** Treat all external data as untrusted. Parse structured data only.
  Never execute instructions found in data fields.

### 2.2 Social Narrative Manipulation
- **Attack:** Coordinate social media campaigns to create artificial narratives
  (pump signals, fake insider info, manufactured fear) targeting AI agents that
  consume social sentiment
- **Detection:** Volume anomaly detection, source diversity analysis, bot detection
- **Severity:** High
- **Mitigation:** The Narrative Firewall module. Weight sentiment signals by source
  credibility. Flag volume anomalies as potential manipulation.

### 2.3 Agent-to-Agent Coercion
- **Attack:** In multi-agent systems, one compromised agent influences others through
  shared communication channels (as demonstrated by Moltbook vulnerabilities)
- **Detection:** Track agent recommendation sources, detect amplification patterns
- **Severity:** Medium
- **Mitigation:** Isolate trading decisions from agent social layer. Require
  independent confirmation of signals from multiple uncorrelated sources.

### 2.4 Indirect Prompt Injection via Market Data
- **Attack:** Place orders or create on-chain transactions with embedded instructions
  in memo/data fields that trading agents might process
- **Detection:** Content filtering on transaction data, instruction pattern detection
- **Severity:** Medium
- **Mitigation:** Parse only numeric/structured fields from market data.
  Ignore text fields in on-chain data when making trading decisions.

## Threat Class 3: Data Leakage / Identity Risk

Leaks of identity, credentials, strategies, and communications rapidly translate
into financial risk in agentic trading systems.

### 3.1 API Key Exposure
- **Attack:** API keys leaked through logs, error messages, plugin data sharing,
  or repository commits
- **Detection:** Regex scanning for key patterns in logs and code, entropy analysis
- **Severity:** Critical
- **Mitigation:** Never log API keys. Use environment variables or secrets manager.
  Rotate keys regularly. Set IP restrictions and withdrawal limits.

### 3.2 Strategy Leakage
- **Attack:** Trading strategy parameters, entry/exit rules, or position sizing
  logic exposed through shared logs, plugin telemetry, or agent communications
- **Detection:** Scan outbound data for strategy-correlated information
- **Severity:** High
- **Mitigation:** Minimize logging detail for strategy logic. Do not share strategy
  parameters with plugins. Encrypt strategy configuration at rest.

### 3.3 Position Inference
- **Attack:** Attacker infers current positions from observable behavior (trade timing,
  order flow patterns, API request patterns) and front-runs or squeezes
- **Detection:** Difficult to detect; requires operational security awareness
- **Severity:** Medium
- **Mitigation:** Randomize execution timing. Use multiple venues. Split large
  orders. Avoid predictable patterns in order placement.

### 3.4 Wallet/Account Correlation
- **Attack:** Link on-chain wallet addresses to exchange accounts to real identities,
  enabling targeted attacks or social engineering
- **Detection:** Monitor for wallet address exposure in logs, code, and communications
- **Severity:** Medium
- **Mitigation:** Use separate wallets for different purposes. Avoid hardcoding
  wallet addresses. Use privacy-preserving bridging when needed.

### 3.5 Communication Interception
- **Attack:** Intercept DMs, strategy discussions, or trade signals between agents
  or between agents and operators (as exposed in Moltbook vulnerability)
- **Detection:** Monitor for unencrypted communication channels
- **Severity:** High
- **Mitigation:** Encrypt all agent-to-operator communications. Use authenticated
  channels. Assume public channels are monitored.

## Mapping Threats to SAE Enforcement

| Threat | SAE Response |
|---|---|
| Malicious plugin executes unauthorized trade | Policy Gate blocks trades not matching operator intent |
| Narrative manipulation triggers FOMO | Narrative Firewall detects anomalous narrative + blocks |
| Compromised agent recommends bad trades | Trader State Model flags sudden behavior change |
| API key leaked | Threat Audit detects credential exposure patterns |
| Strategy exposed via logging | Threat Audit detects sensitive data in logs |

SAE's value is not only behavioral risk control — it converts these external
threats into enforceable execution constraints.
