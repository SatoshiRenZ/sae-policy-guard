#!/usr/bin/env python3
"""
SAE Threat Audit — Static analysis scanner for trading plugins/extensions.

Scans for supply-chain, prompt injection, and data leakage risks
in trading system plugins, bots, and extensions.

Usage:
    python threat_audit.py --target <plugin-directory-or-file> [--config config.json] [--mode full|quick]
"""

import argparse
import ast
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any


# --- Threat patterns ---

SUPPLY_CHAIN_PATTERNS = {
    "eval_exec": {
        "pattern": r"\b(eval|exec|compile)\s*\(",
        "severity": "high",
        "description": "Dynamic code execution (eval/exec/compile)",
    },
    "dynamic_import": {
        "pattern": r"__import__\s*\(|importlib\.import_module",
        "severity": "medium",
        "description": "Dynamic module import",
    },
    "base64_decode": {
        "pattern": r"base64\.(b64decode|decodebytes|urlsafe_b64decode)\s*\(",
        "severity": "medium",
        "description": "Base64 decoding (potential obfuscation)",
    },
    "subprocess": {
        "pattern": r"\b(subprocess|os\.system|os\.popen|commands\.getoutput)\s*[\.(]",
        "severity": "high",
        "description": "System command execution",
    },
    "pickle_load": {
        "pattern": r"pickle\.(load|loads)\s*\(",
        "severity": "high",
        "description": "Pickle deserialization (arbitrary code execution risk)",
    },
    "obfuscated_strings": {
        "pattern": r"(\\x[0-9a-fA-F]{2}){8,}|chr\(\d+\)\s*\+\s*chr\(\d+\)",
        "severity": "medium",
        "description": "Obfuscated string construction",
    },
}

INJECTION_PATTERNS = {
    "template_injection": {
        "pattern": r"(\.format\s*\(.*(?:request|input|user|data|body|param)|\.format_map\s*\(|Template\s*\(\s*(?:request|input|user|data|body|param))",
        "severity": "medium",
        "description": "String template with user-controlled input",
    },
    "sql_injection": {
        "pattern": r"(execute|executemany)\s*\(\s*[f'\"].*(%s|\{)",
        "severity": "high",
        "description": "Potential SQL injection via string formatting",
    },
    "unvalidated_url": {
        "pattern": r"requests\.(get|post|put|delete|patch)\s*\(\s*[a-zA-Z_]",
        "severity": "medium",
        "description": "HTTP request with variable URL (potential SSRF)",
    },
    "json_loads_unvalidated": {
        "pattern": r"json\.loads\s*\(\s*(response|data|body|content|text|payload)",
        "severity": "low",
        "description": "JSON parsing of external data without schema validation",
    },
}

LEAKAGE_PATTERNS = {
    "credential_in_code": {
        "pattern": r"(api_key|api_secret|private_key|password|secret_key|access_token)\s*=\s*['\"][^'\"]{8,}['\"]",
        "severity": "critical",
        "description": "Hardcoded credential in source code",
    },
    "credential_logging": {
        "pattern": r"(print|log|logger|logging)\s*[\.(].*\b(api_key|secret|password|private_key|token)\b",
        "severity": "high",
        "description": "Credential potentially logged to output",
    },
    "env_credential_access": {
        "pattern": r"os\.(environ|getenv)\s*[\[(].*\b(KEY|SECRET|PASSWORD|TOKEN|PRIVATE)\b",
        "severity": "low",
        "description": "Environment variable access for credentials (review handling)",
    },
    "file_write_sensitive": {
        "pattern": r"(open|write|dump)\s*\(.*\b(key|secret|credential|password|strategy)\b",
        "severity": "medium",
        "description": "Potential sensitive data written to file",
    },
    "wallet_address": {
        "pattern": r"['\"]0x[a-fA-F0-9]{40}['\"]",
        "severity": "medium",
        "description": "Hardcoded wallet address in source",
    },
    "unencrypted_storage": {
        "pattern": r"json\.dump\s*\(.*\b(key|secret|credential|private)\b",
        "severity": "medium",
        "description": "Sensitive data stored as unencrypted JSON",
    },
}

NETWORK_PATTERNS = {
    "outbound_connection": {
        "pattern": r"(requests|urllib|httpx|aiohttp|socket)\.(get|post|put|open|connect|request)",
        "severity": "low",
        "description": "Outbound network connection",
    },
    "hardcoded_ip": {
        "pattern": r"['\"](\d{1,3}\.){3}\d{1,3}['\"]",
        "severity": "medium",
        "description": "Hardcoded IP address",
    },
    "hardcoded_url": {
        "pattern": r"['\"]https?://(?!localhost|127\.0\.0\.1)[^\s'\"]+['\"]",
        "severity": "low",
        "description": "Hardcoded external URL",
    },
    "websocket": {
        "pattern": r"(websocket|ws://|wss://|WebSocket)",
        "severity": "low",
        "description": "WebSocket connection",
    },
}


def scan_file_regex(
    filepath: str, content: str, patterns: dict, category: str
) -> list[dict]:
    """Scan file content against regex patterns."""
    findings = []
    lines = content.split("\n")

    for pattern_name, pattern_info in patterns.items():
        for line_num, line in enumerate(lines, 1):
            if re.search(pattern_info["pattern"], line, re.IGNORECASE):
                findings.append(
                    {
                        "category": category,
                        "pattern": pattern_name,
                        "severity": pattern_info["severity"],
                        "file": filepath,
                        "line": line_num,
                        "content": line.strip()[:120],
                        "description": pattern_info["description"],
                    }
                )

    return findings


def scan_python_ast(filepath: str, content: str) -> list[dict]:
    """Use Python AST to detect structural risks in Python files."""
    findings = []

    try:
        tree = ast.parse(content, filename=filepath)
    except SyntaxError:
        return findings

    for node in ast.walk(tree):
        # Detect exec/eval calls
        if isinstance(node, ast.Call):
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name in ("exec", "eval", "compile"):
                findings.append(
                    {
                        "category": "supply_chain",
                        "pattern": "ast_dynamic_execution",
                        "severity": "high",
                        "file": filepath,
                        "line": node.lineno,
                        "content": f"{func_name}() call detected via AST",
                        "description": f"Dynamic code execution: {func_name}()",
                    }
                )

        # Detect star imports (can mask malicious code)
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    findings.append(
                        {
                            "category": "supply_chain",
                            "pattern": "star_import",
                            "severity": "low",
                            "file": filepath,
                            "line": node.lineno,
                            "content": f"from {node.module} import *",
                            "description": "Star import can mask malicious names",
                        }
                    )

        # Detect __getattr__ / __getattribute__ overrides (proxy objects)
        if isinstance(node, ast.FunctionDef) and node.name in (
            "__getattr__",
            "__getattribute__",
        ):
            findings.append(
                {
                    "category": "supply_chain",
                    "pattern": "attribute_interception",
                    "severity": "medium",
                    "file": filepath,
                    "line": node.lineno,
                    "content": f"def {node.name}(...)",
                    "description": "Attribute interception can proxy/redirect access",
                }
            )

    return findings


def scan_file(filepath: str) -> list[dict]:
    """Scan a single file for all threat categories."""
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except (OSError, IOError):
        return []

    findings = []

    # Regex scans for all file types
    findings.extend(scan_file_regex(filepath, content, SUPPLY_CHAIN_PATTERNS, "supply_chain"))
    findings.extend(scan_file_regex(filepath, content, INJECTION_PATTERNS, "injection"))
    findings.extend(scan_file_regex(filepath, content, LEAKAGE_PATTERNS, "leakage"))
    findings.extend(scan_file_regex(filepath, content, NETWORK_PATTERNS, "network"))

    # AST scan for Python files
    if filepath.endswith(".py"):
        findings.extend(scan_python_ast(filepath, content))

    return findings


def collect_files(target: str) -> list[str]:
    """Collect scannable files from a target path."""
    scannable_extensions = {
        ".py", ".js", ".ts", ".mjs", ".cjs",
        ".sh", ".bash", ".zsh",
        ".json", ".yaml", ".yml", ".toml",
        ".env", ".cfg", ".ini", ".conf",
    }

    if os.path.isfile(target):
        return [target]

    files = []
    for root, dirs, filenames in os.walk(target):
        # Skip hidden directories and common non-source dirs
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv", ".venv")
        ]
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext in scannable_extensions or filename in (".env", "Dockerfile"):
                files.append(os.path.join(root, filename))

    return files


def compute_category_scores(findings: list[dict]) -> dict[str, float]:
    """Compute normalized risk scores per category."""
    severity_weights = {"critical": 1.0, "high": 0.7, "medium": 0.4, "low": 0.15}

    category_scores: dict[str, float] = {}
    category_counts: dict[str, int] = {}

    for f in findings:
        cat = f["category"]
        weight = severity_weights.get(f["severity"], 0.1)
        category_scores[cat] = category_scores.get(cat, 0) + weight
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # Normalize: cap at 1.0, scale by expected finding density
    normalized = {}
    for cat in ("supply_chain", "injection", "leakage", "network"):
        raw = category_scores.get(cat, 0)
        # Sigmoid-like normalization: score approaches 1.0 as findings increase
        normalized[cat] = round(min(1.0, raw / (raw + 3.0)), 4) if raw > 0 else 0.0

    return normalized


def classify_overall_risk(scores: dict[str, float]) -> str:
    """Classify overall risk from category scores."""
    max_score = max(scores.values()) if scores else 0.0
    avg_score = sum(scores.values()) / max(len(scores), 1)

    if max_score >= 0.7 or avg_score >= 0.5:
        return "high"
    elif max_score >= 0.4 or avg_score >= 0.25:
        return "medium"
    elif max_score > 0:
        return "low"
    else:
        return "clean"


def run_audit(target: str, config: dict | None = None, mode: str = "full") -> dict[str, Any]:
    """Run full threat audit on target."""
    files = collect_files(target)
    all_findings = []

    for filepath in files:
        file_findings = scan_file(filepath)
        all_findings.extend(file_findings)

    # In quick mode, only report high/critical severity
    if mode == "quick":
        all_findings = [
            f for f in all_findings if f["severity"] in ("critical", "high")
        ]

    scores = compute_category_scores(all_findings)
    overall_risk = classify_overall_risk(scores)

    # Deduplicate findings by (file, line, pattern)
    seen = set()
    unique_findings = []
    for f in all_findings:
        key = (f["file"], f["line"], f["pattern"])
        if key not in seen:
            seen.add(key)
            unique_findings.append(f)

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    unique_findings.sort(key=lambda f: severity_order.get(f["severity"], 4))

    # Generate recommendations
    recommendations = generate_recommendations(unique_findings, scores)

    return {
        "target": target,
        "overall_risk": overall_risk,
        "supply_chain_score": scores.get("supply_chain", 0.0),
        "injection_score": scores.get("injection", 0.0),
        "leakage_score": scores.get("leakage", 0.0),
        "network_score": scores.get("network", 0.0),
        "findings": unique_findings,
        "recommendations": recommendations,
        "scan_summary": {
            "files_scanned": len(files),
            "patterns_checked": (
                len(SUPPLY_CHAIN_PATTERNS)
                + len(INJECTION_PATTERNS)
                + len(LEAKAGE_PATTERNS)
                + len(NETWORK_PATTERNS)
            ),
            "findings_count": len(unique_findings),
            "critical_count": sum(1 for f in unique_findings if f["severity"] == "critical"),
            "high_count": sum(1 for f in unique_findings if f["severity"] == "high"),
            "medium_count": sum(1 for f in unique_findings if f["severity"] == "medium"),
            "low_count": sum(1 for f in unique_findings if f["severity"] == "low"),
        },
        "scan_time": datetime.now(timezone.utc).isoformat(),
    }


def generate_recommendations(findings: list[dict], scores: dict[str, float]) -> list[str]:
    """Generate actionable recommendations from findings."""
    recs = []

    if scores.get("supply_chain", 0) >= 0.4:
        recs.append(
            "HIGH PRIORITY: Review all dynamic code execution (eval/exec) and "
            "subprocess calls. Consider sandboxing or removing them."
        )

    if scores.get("leakage", 0) >= 0.4:
        recs.append(
            "HIGH PRIORITY: Audit all credential handling. Remove hardcoded secrets, "
            "ensure credentials are not logged, and use encrypted storage."
        )

    if scores.get("injection", 0) >= 0.4:
        recs.append(
            "Review all external data inputs. Add schema validation for JSON parsing "
            "and parameterize SQL queries."
        )

    if scores.get("network", 0) >= 0.3:
        recs.append(
            "Document all outbound network connections. Verify each external URL "
            "and consider allowlisting network destinations."
        )

    has_critical = any(f["severity"] == "critical" for f in findings)
    if has_critical:
        recs.insert(
            0,
            "CRITICAL: Hardcoded credentials detected. Rotate all affected "
            "keys/passwords immediately and move to environment variables or a secrets manager.",
        )

    if not recs:
        recs.append("No significant risks detected. Continue routine monitoring.")

    return recs


def main():
    parser = argparse.ArgumentParser(
        description="SAE Threat Audit — Plugin/extension security scanner"
    )
    parser.add_argument(
        "--target", required=True, help="Path to plugin directory or file to scan"
    )
    parser.add_argument("--config", default=None, help="Path to config JSON")
    parser.add_argument(
        "--mode",
        choices=["full", "quick"],
        default="full",
        help="full: all severities; quick: critical/high only",
    )
    args = parser.parse_args()

    config = None
    if args.config:
        with open(args.config) as f:
            config = json.load(f)

    result = run_audit(args.target, config, args.mode)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
