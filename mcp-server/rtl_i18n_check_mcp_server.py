#!/usr/bin/env python3
"""MCP server for rtl-i18n-check — expose the RTL locale-file checker
as a tool.

Zero-dependency (Python standard library only). Speaks JSON-RPC 2.0
over stdio, newline-delimited, following the MCP lifecycle:

    initialize -> notifications/initialized -> tools/list -> tools/call

Tool:
    check_rtl_locales
        files   locale files to check (array of paths, required)
        lang    force the locale language: fa | ar | ur | he
        source  source-locale file (e.g. en) to compare placeholder
                sets against
        digits  expected digit script: fa | ar | latn | auto
        strict  also fail on warnings (bool)
        fix     write <name>.fixed.<ext> for JSON/ARB inputs (bool)

The tool runs skills/rtl-i18n-check/scripts/rtl_i18n_check.py as a
subprocess with --json and returns its summary. Exit-code semantics
are preserved in the returned text (0 = no errors, 1 = errors found,
2 = usage error or an unreadable file).
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE.parent / "skills" / "rtl-i18n-check" / "scripts" \
    / "rtl_i18n_check.py"

SERVER_INFO = {"name": "rtl-i18n-check", "version": "0.1.0"}
PROTOCOL_VERSION = "2024-11-05"


def run_checker(args):
    files = args.get("files")
    if not files:
        raise ValueError("check_rtl_locales requires 'files'")
    if isinstance(files, str):
        files = [files]
    cmd = [sys.executable, str(CHECKER)] + [str(f) for f in files] \
        + ["--json"]
    if args.get("lang"):
        cmd += ["--lang", str(args["lang"])]
    if args.get("source"):
        cmd += ["--source", str(args["source"])]
    cmd += ["--digits", str(args.get("digits", "auto"))]
    if args.get("strict"):
        cmd.append("--strict")
    if args.get("fix"):
        cmd.append("--fix")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    lines = [f"check_rtl_locales exit code: {proc.returncode}"]
    try:
        summary = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        summary = {}
    if summary:
        counts = summary.get("counts", {})
        lines.append(
            f"{counts.get('error', '?')} errors, "
            f"{counts.get('warning', '?')} warnings.")
        for finding in summary.get("findings", []):
            key = f" :: {finding['key']}" if finding.get("key") else ""
            lines.append(
                f"[{finding['severity'].upper()}] {finding['code']} "
                f"{finding['file']}{key}: {finding['message']}")
            if finding.get("detail"):
                lines.append(f"    {finding['detail']}")
    if proc.stderr.strip():
        lines.append("stderr:")
        lines.append(proc.stderr.strip())
    return "\n".join(lines)


TOOLS = [
    {
        "name": "check_rtl_locales",
        "description": (
            "Check fa/ar/ur/he locale files for the RTL bugs generic "
            "i18n checkers cannot see: unisolated LTR islands (names, "
            "URLs, placeholders, currency) inside RTL text, ZWNJ loss, "
            "Arabic/Persian letter-variant mixing, mixed digit scripts, "
            "missing CLDR plural forms (Arabic needs six), bidi "
            "override/unpaired isolate characters, and translated or "
            "dropped placeholders. Returns the failure count and the "
            "findings; with fix=true it writes <name>.fixed.<ext> for "
            "JSON/ARB inputs with the safely mechanical fixes applied."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "locale files to check, e.g. "
                                   "['locales/fa.json']",
                },
                "lang": {
                    "type": "string",
                    "enum": ["fa", "ar", "ur", "he"],
                    "description": "force the locale language for all files",
                },
                "source": {
                    "type": "string",
                    "description": "source-locale file (e.g. en) to "
                                   "compare placeholder sets against",
                },
                "digits": {
                    "type": "string",
                    "enum": ["fa", "ar", "latn", "auto"],
                    "description": "expected digit script (default auto: "
                                   "the file's majority script)",
                },
                "strict": {
                    "type": "boolean",
                    "description": "also fail on warnings",
                },
                "fix": {
                    "type": "boolean",
                    "description": "write <name>.fixed.<ext> for JSON/ARB "
                                   "inputs with mechanical fixes applied",
                },
            },
            "required": ["files"],
        },
    }
]


def handle(request):
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"tools": TOOLS},
        }
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name != "check_rtl_locales":
            return {
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32602,
                          "message": f"unknown tool: {name}"},
            }
        try:
            text = run_checker(arguments)
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": text}]},
            }
        except Exception as exc:  # noqa: BLE001 - surface as tool error
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text",
                                        "text": f"error: {exc}"}],
                           "isError": True},
            }
    return {
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
