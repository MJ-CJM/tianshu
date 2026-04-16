"""Skills Guard — security scanning with trust-aware install policy.

Ported from hermes-agent's skills_guard.py. 50+ regex patterns across
13 threat categories, invisible unicode detection, and trust-level policy matrix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class TrustLevel(str, Enum):
    BUILTIN = "builtin"
    TRUSTED = "trusted"
    COMMUNITY = "community"
    AGENT_CREATED = "agent-created"


class GuardCategory(str, Enum):
    EXFILTRATION = "exfiltration"
    PROMPT_INJECTION = "injection"
    INVISIBLE_UNICODE = "invisible_unicode"
    DESTRUCTIVE = "destructive"
    PERSISTENCE = "persistence"
    REVERSE_SHELL = "network"
    OBFUSCATION = "obfuscation"
    EXECUTION = "execution"
    SUPPLY_CHAIN = "supply_chain"
    SECRETS = "credential_exposure"
    PATH_TRAVERSAL = "traversal"
    CRYPTO_MINING = "mining"
    PRIVILEGE_ESCALATION = "privilege_escalation"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class GuardPattern:
    id: str
    category: GuardCategory
    regex: re.Pattern[str]
    severity: Severity
    description: str


@dataclass(frozen=True)
class GuardFinding:
    category: GuardCategory
    severity: Severity
    message: str
    line_number: int | None = None
    snippet: str = ""


@dataclass(frozen=True)
class GuardResult:
    verdict: str  # "safe" | "caution" | "dangerous"
    findings: tuple[GuardFinding, ...]


# --------------- Invisible Unicode Characters ---------------

INVISIBLE_CHARS: frozenset[str] = frozenset({
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\u2060",  # word joiner
    "\u2062",  # invisible times
    "\u2063",  # invisible separator
    "\u2064",  # invisible plus
    "\ufeff",  # BOM / zero-width no-break space
    "\u202a",  # LTR embedding
    "\u202b",  # RTL embedding
    "\u202c",  # pop directional formatting
    "\u202d",  # LTR override
    "\u202e",  # RTL override
    "\u2066",  # LTR isolate
    "\u2067",  # RTL isolate
    "\u2068",  # first strong isolate
    "\u2069",  # pop directional isolate
})

# --------------- Threat Patterns ---------------

_I = re.IGNORECASE

def _p(id_: str, cat: GuardCategory, pattern: str, sev: Severity, desc: str, flags: int = 0) -> GuardPattern:
    return GuardPattern(id_, cat, re.compile(pattern, flags), sev, desc)

# fmt: off
THREAT_PATTERNS: tuple[GuardPattern, ...] = (
    # === EXFILTRATION ===
    _p("env_exfil_curl", GuardCategory.EXFILTRATION,
       r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', Severity.CRITICAL,
       "curl command leaking env secrets", _I),
    _p("env_exfil_wget", GuardCategory.EXFILTRATION,
       r'wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', Severity.CRITICAL,
       "wget command leaking env secrets", _I),
    _p("env_exfil_requests", GuardCategory.EXFILTRATION,
       r'requests\.(get|post|put|patch)\s*\([^\n]*(KEY|TOKEN|SECRET|PASSWORD)', Severity.CRITICAL,
       "Python requests leaking secrets", _I),
    _p("ssh_dir_access", GuardCategory.EXFILTRATION,
       r'\$HOME/\.ssh|\~/\.ssh', Severity.HIGH,
       "Access to SSH directory"),
    _p("aws_dir_access", GuardCategory.EXFILTRATION,
       r'\$HOME/\.aws|\~/\.aws', Severity.HIGH,
       "Access to AWS credentials directory"),
    _p("read_secrets_file", GuardCategory.EXFILTRATION,
       r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', Severity.CRITICAL,
       "Reading credential files"),
    _p("dump_all_env", GuardCategory.EXFILTRATION,
       r'printenv|env\s*\|', Severity.HIGH,
       "Dumping all environment variables"),
    _p("python_os_environ", GuardCategory.EXFILTRATION,
       r"os\.environ\b(?!\s*\.get\s*\(\s*[\"']PATH)", Severity.HIGH,
       "Broad os.environ access"),
    _p("dns_exfil", GuardCategory.EXFILTRATION,
       r'\b(dig|nslookup|host)\s+[^\n]*\$', Severity.CRITICAL,
       "DNS exfiltration of env variables"),
    _p("tmp_staging", GuardCategory.EXFILTRATION,
       r'>\s*/tmp/[^\s]*\s*&&\s*(curl|wget|nc|python)', Severity.CRITICAL,
       "Staging data in /tmp then exfiltrating"),

    # === PROMPT INJECTION ===
    _p("ignore_instructions", GuardCategory.PROMPT_INJECTION,
       r'ignore\s+(?:\w+\s+)*(previous|all|above|prior)\s+instructions', Severity.CRITICAL,
       "Attempt to override previous instructions", _I),
    _p("role_hijack", GuardCategory.PROMPT_INJECTION,
       r'you\s+are\s+(?:\w+\s+)*now\s+', Severity.HIGH,
       "Role hijacking attempt", _I),
    _p("deception_hide", GuardCategory.PROMPT_INJECTION,
       r'do\s+not\s+(?:\w+\s+)*tell\s+(?:\w+\s+)*the\s+user', Severity.CRITICAL,
       "Hiding information from user", _I),
    _p("sys_prompt_override", GuardCategory.PROMPT_INJECTION,
       r'system\s+prompt\s+override', Severity.CRITICAL,
       "System prompt override attempt", _I),
    _p("disregard_rules", GuardCategory.PROMPT_INJECTION,
       r"disregard\s+(?:\w+\s+)*(your|all|any)\s+(?:\w+\s+)*(instructions|rules|guidelines)", Severity.CRITICAL,
       "Attempt to disregard rules", _I),
    _p("hidden_div", GuardCategory.PROMPT_INJECTION,
       r'<\s*div\s+style\s*=\s*["\'][\s\S]*?display\s*:\s*none', Severity.HIGH,
       "Hidden HTML div (invisible content injection)"),
    _p("jailbreak_dan", GuardCategory.PROMPT_INJECTION,
       r'\bDAN\s+mode\b|Do\s+Anything\s+Now', Severity.CRITICAL,
       "DAN jailbreak pattern", _I),
    _p("remove_filters", GuardCategory.PROMPT_INJECTION,
       r'(respond|answer|reply)\s+without\s+(?:\w+\s+)*(restrictions|limitations|filters|safety)', Severity.CRITICAL,
       "Attempt to remove safety filters", _I),
    _p("fake_update", GuardCategory.PROMPT_INJECTION,
       r'you\s+have\s+been\s+(?:\w+\s+)*(updated|upgraded|patched)\s+to', Severity.HIGH,
       "Fake model update claim", _I),

    # === DESTRUCTIVE ===
    _p("root_rm", GuardCategory.DESTRUCTIVE,
       r'rm\s+-rf\s+/', Severity.CRITICAL,
       "Recursive deletion of root filesystem"),
    _p("home_rm", GuardCategory.DESTRUCTIVE,
       r'rm\s+(-[^\s]*)?r.*\$HOME|\brmdir\s+.*\$HOME', Severity.CRITICAL,
       "Recursive deletion of home directory"),
    _p("system_overwrite", GuardCategory.DESTRUCTIVE,
       r'>\s*/etc/', Severity.CRITICAL,
       "Overwriting system configuration"),
    _p("disk_overwrite", GuardCategory.DESTRUCTIVE,
       r'\bdd\s+.*if=.*of=/dev/', Severity.CRITICAL,
       "Disk overwrite via dd"),
    _p("python_rmtree", GuardCategory.DESTRUCTIVE,
       r"shutil\.rmtree\s*\(\s*[\"'/]", Severity.HIGH,
       "Python shutil.rmtree on absolute path"),

    # === PERSISTENCE ===
    _p("shell_rc_mod", GuardCategory.PERSISTENCE,
       r'\.(bashrc|zshrc|profile|bash_profile|bash_login|zprofile|zlogin)\b', Severity.MEDIUM,
       "Shell RC file modification"),
    _p("ssh_backdoor", GuardCategory.PERSISTENCE,
       r'authorized_keys', Severity.CRITICAL,
       "SSH authorized_keys manipulation"),
    _p("systemd_service", GuardCategory.PERSISTENCE,
       r'systemd.*\.service|systemctl\s+(enable|start)', Severity.MEDIUM,
       "Systemd service persistence"),
    _p("macos_launchd", GuardCategory.PERSISTENCE,
       r'launchctl\s+load|LaunchAgents|LaunchDaemons', Severity.MEDIUM,
       "macOS launchd persistence"),
    _p("sudoers_mod", GuardCategory.PERSISTENCE,
       r'/etc/sudoers|visudo', Severity.CRITICAL,
       "Sudoers file modification"),
    _p("agent_config_mod", GuardCategory.PERSISTENCE,
       r'AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules', Severity.CRITICAL,
       "Agent configuration file modification"),

    # === NETWORK / REVERSE SHELL ===
    _p("reverse_shell_nc", GuardCategory.REVERSE_SHELL,
       r'\bnc\s+-[lp]|ncat\s+-[lp]|\bsocat\b', Severity.CRITICAL,
       "Reverse shell via netcat/socat"),
    _p("bash_reverse_shell", GuardCategory.REVERSE_SHELL,
       r'/bin/(ba)?sh\s+-i\s+.*>/dev/tcp/', Severity.CRITICAL,
       "Bash reverse shell"),
    _p("python_socket", GuardCategory.REVERSE_SHELL,
       r"python[23]?\s+-c\s+[\"']import\s+socket", Severity.CRITICAL,
       "Python one-liner socket connection"),
    _p("tunnel_service", GuardCategory.REVERSE_SHELL,
       r'\bngrok\b|\blocaltunnel\b|\bserveo\b|\bcloudflared\b', Severity.HIGH,
       "Tunneling service usage"),
    _p("exfil_service", GuardCategory.REVERSE_SHELL,
       r'webhook\.site|requestbin\.com|pipedream\.net|hookbin\.com', Severity.HIGH,
       "Known exfiltration service"),

    # === OBFUSCATION ===
    _p("base64_decode_pipe", GuardCategory.OBFUSCATION,
       r'base64\s+(-d|--decode)\s*\|', Severity.HIGH,
       "base64 decode piped to execution"),
    _p("eval_string", GuardCategory.OBFUSCATION,
       r"\beval\s*\(\s*[\"']", Severity.HIGH,
       "eval() with string argument"),
    _p("exec_string", GuardCategory.OBFUSCATION,
       r"\bexec\s*\(\s*[\"']", Severity.HIGH,
       "exec() with string argument"),
    _p("echo_pipe_exec", GuardCategory.OBFUSCATION,
       r'echo\s+[^\n]*\|\s*(bash|sh|python|perl|ruby|node)', Severity.CRITICAL,
       "Echo piped to interpreter"),
    _p("python_import_os", GuardCategory.OBFUSCATION,
       r"__import__\s*\(\s*[\"']os[\"']\s*\)", Severity.HIGH,
       "Dynamic os import"),
    _p("chr_building", GuardCategory.OBFUSCATION,
       r'chr\s*\(\s*\d+\s*\)\s*\+\s*chr\s*\(\s*\d+', Severity.HIGH,
       "String construction via chr() calls"),

    # === SUPPLY CHAIN ===
    _p("curl_pipe_shell", GuardCategory.SUPPLY_CHAIN,
       r'curl\s+[^\n]*\|\s*(ba)?sh', Severity.CRITICAL,
       "curl piped to shell"),
    _p("wget_pipe_shell", GuardCategory.SUPPLY_CHAIN,
       r'wget\s+[^\n]*-O\s*-\s*\|\s*(ba)?sh', Severity.CRITICAL,
       "wget piped to shell"),
    _p("curl_pipe_python", GuardCategory.SUPPLY_CHAIN,
       r'curl\s+[^\n]*\|\s*python', Severity.CRITICAL,
       "curl piped to python"),
    _p("unpinned_pip", GuardCategory.SUPPLY_CHAIN,
       r'pip\s+install\s+(?!-r\s)(?!.*==)', Severity.MEDIUM,
       "Unpinned pip install"),
    _p("unpinned_npm", GuardCategory.SUPPLY_CHAIN,
       r'npm\s+install\s+(?!.*@\d)', Severity.MEDIUM,
       "Unpinned npm install"),

    # === CREDENTIAL EXPOSURE ===
    _p("openai_key", GuardCategory.SECRETS,
       r'sk-[A-Za-z0-9]{20,}', Severity.CRITICAL,
       "OpenAI-style API key"),
    _p("github_token", GuardCategory.SECRETS,
       r'(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}', Severity.CRITICAL,
       "GitHub personal access token"),
    _p("aws_access_key", GuardCategory.SECRETS,
       r'AKIA[0-9A-Z]{16}', Severity.CRITICAL,
       "AWS access key ID"),
    _p("generic_secret", GuardCategory.SECRETS,
       r'(?:api[_-]?key|apikey)\s*[:=]\s*[\'"]?[A-Za-z0-9_\-]{20,}', Severity.CRITICAL,
       "Generic API key assignment", _I),
    _p("password_assign", GuardCategory.SECRETS,
       r'(?:secret|token|password|passwd|pwd)\s*[:=]\s*[\'"]?[A-Za-z0-9_\-]{8,}', Severity.CRITICAL,
       "Password/secret assignment", _I),

    # === PATH TRAVERSAL ===
    _p("path_traversal", GuardCategory.PATH_TRAVERSAL,
       r'\.\./\.\./\.\./', Severity.HIGH,
       "Deep path traversal (3+ levels)"),
    _p("etc_passwd", GuardCategory.PATH_TRAVERSAL,
       r'/etc/passwd|/etc/shadow', Severity.CRITICAL,
       "Access to system password files"),

    # === CRYPTO MINING ===
    _p("crypto_miner", GuardCategory.CRYPTO_MINING,
       r'\b(xmrig|cpuminer|cgminer|bfgminer|ethminer|minerd)\b', Severity.CRITICAL,
       "Cryptocurrency mining tool", _I),

    # === PRIVILEGE ESCALATION ===
    _p("sudo_nopasswd", GuardCategory.PRIVILEGE_ESCALATION,
       r'NOPASSWD', Severity.CRITICAL,
       "Passwordless sudo configuration"),
    _p("setuid", GuardCategory.PRIVILEGE_ESCALATION,
       r'chmod\s+[0-7]*[4-7][0-7]{2}\s|chmod\s+u\+s', Severity.HIGH,
       "Setting SUID bit"),
    _p("capability_set", GuardCategory.PRIVILEGE_ESCALATION,
       r'setcap\s+', Severity.HIGH,
       "Linux capability assignment"),
)
# fmt: on


# --------------- Install Policy Matrix ---------------

# (safe, caution, dangerous) → action
INSTALL_POLICY: dict[TrustLevel, tuple[str, str, str]] = {
    TrustLevel.BUILTIN:       ("allow", "allow", "allow"),
    TrustLevel.TRUSTED:       ("allow", "allow", "block"),
    TrustLevel.COMMUNITY:     ("allow", "block", "block"),
    TrustLevel.AGENT_CREATED: ("allow", "allow", "ask"),
}


# --------------- Main Scanner ---------------


class SkillsGuard:
    """Security scanner for skill content."""

    def scan_content(self, content: str, trust_level: TrustLevel = TrustLevel.COMMUNITY) -> GuardResult:
        """Scan skill content for security threats."""
        findings: list[GuardFinding] = []

        # Pattern matching
        for pat in THREAT_PATTERNS:
            for match in pat.regex.finditer(content):
                line_num = content[:match.start()].count("\n") + 1
                snippet = content[max(0, match.start() - 20):min(len(content), match.end() + 20)]
                findings.append(GuardFinding(
                    category=pat.category,
                    severity=pat.severity,
                    message=f"{pat.description} [{pat.id}]",
                    line_number=line_num,
                    snippet=snippet,
                ))

        # Invisible unicode scan
        findings.extend(self.scan_invisible_unicode(content))

        verdict = self._determine_verdict(findings)
        return GuardResult(verdict=verdict, findings=tuple(findings))

    @staticmethod
    def scan_invisible_unicode(content: str) -> list[GuardFinding]:
        """Detect invisible unicode characters that could hide malicious content."""
        findings: list[GuardFinding] = []
        for i, ch in enumerate(content):
            if ch in INVISIBLE_CHARS:
                line_num = content[:i].count("\n") + 1
                char_name = f"U+{ord(ch):04X}"
                findings.append(GuardFinding(
                    category=GuardCategory.INVISIBLE_UNICODE,
                    severity=Severity.HIGH,
                    message=f"Invisible unicode character {char_name} at position {i}",
                    line_number=line_num,
                    snippet=repr(content[max(0, i - 5):i + 5]),
                ))
        return findings

    @staticmethod
    def _determine_verdict(findings: list[GuardFinding]) -> str:
        if not findings:
            return "safe"
        severities = {f.severity for f in findings}
        if Severity.CRITICAL in severities:
            return "dangerous"
        return "caution"

    @staticmethod
    def should_allow(result: GuardResult, trust_level: TrustLevel) -> bool:
        """Check install policy matrix. Returns True if allowed."""
        policy = INSTALL_POLICY.get(trust_level, ("allow", "block", "block"))
        verdict_idx = {"safe": 0, "caution": 1, "dangerous": 2}.get(result.verdict, 2)
        action = policy[verdict_idx]
        return action == "allow"

    @staticmethod
    def resolve_trust_level(source: str) -> TrustLevel:
        """Map source string to trust level."""
        if source == "agent-created":
            return TrustLevel.AGENT_CREATED
        if source.startswith("official") or source == "builtin":
            return TrustLevel.BUILTIN
        if source in {"workspace", "user"}:
            return TrustLevel.TRUSTED
        return TrustLevel.COMMUNITY
