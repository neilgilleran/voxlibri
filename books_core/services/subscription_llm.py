"""
Subscription-backed completions: run the Codex or Claude Code CLI instead of the
paid OpenAI API, so book analysis rides a ChatGPT or Claude plan at no per-token
cost.

Selected with VOXLIBRI_LLM_PROVIDER=codex|claude (default: openai, i.e. unchanged).
Optional VOXLIBRI_CLI_MODEL picks the model the CLI uses; otherwise the CLI's own
default applies. OpenAI model names passed by callers (gpt-4o-mini etc.) are ignored
on this path because they mean nothing to either CLI.

Each call runs in an empty temporary directory with no tools: the model can only
answer, never read or run anything. Token counts are what the CLI reports when it
reports them, otherwise a chars/4 estimate, and are used for usage tracking only.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PROVIDERS = ('codex', 'claude')
DEFAULT_TIMEOUT = int(os.getenv('VOXLIBRI_CLI_TIMEOUT', '600'))


class SubscriptionLLMError(RuntimeError):
    """The CLI failed, timed out, or returned nothing."""


def provider() -> str:
    return os.getenv('VOXLIBRI_LLM_PROVIDER', 'openai').strip().lower()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _full_prompt(prompt: str, system_message: Optional[str]) -> str:
    if not system_message:
        return prompt
    return f"{system_message.strip()}\n\n---\n\n{prompt}"


def _binary(name: str) -> str:
    path = shutil.which(name) or os.path.expanduser(
        {'codex': '~/.bun/bin/codex', 'claude': '~/.local/bin/claude'}[name]
    )
    if not os.path.exists(path):
        raise SubscriptionLLMError(f"{name} CLI not found on PATH")
    return path


def _run_codex(text: str, model: Optional[str], timeout: int) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix='voxlibri-codex-') as work:
        out_file = os.path.join(work, 'answer.txt')
        cmd = [
            _binary('codex'), 'exec',
            '--skip-git-repo-check', '--ephemeral',
            '--sandbox', 'read-only',
            '-C', work,
            '-o', out_file,
        ]
        if model:
            cmd += ['-m', model]
        cmd.append('-')  # prompt from stdin
        proc = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=timeout)
        answer = ''
        if os.path.exists(out_file):
            with open(out_file, encoding='utf-8') as f:
                answer = f.read().strip()
        if proc.returncode != 0 or not answer:
            tail = (proc.stderr or proc.stdout or '')[-400:]
            raise SubscriptionLLMError(f"codex exec failed (exit {proc.returncode}): {tail}")
        # codex prints "tokens used\n12,345" on stderr; use it when present.
        m = re.search(r'tokens used\s*\n\s*([\d,]+)', proc.stderr or '')
        total = int(m.group(1).replace(',', '')) if m else None
        return {'content': answer, 'total': total, 'model': model or 'codex-default'}


def _run_claude(text: str, model: Optional[str], timeout: int) -> Dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if not k.startswith('CLAUDECODE') and not k.startswith('CLAUDE_CODE_')}
    with tempfile.TemporaryDirectory(prefix='voxlibri-claude-') as work:
        cmd = [_binary('claude'), '-p', '--output-format', 'json',
               '--disallowedTools', 'Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task']
        if model:
            cmd += ['--model', model]
        proc = subprocess.run(cmd, input=text, capture_output=True, text=True,
                              timeout=timeout, cwd=work, env=env)
    if proc.returncode != 0:
        raise SubscriptionLLMError(f"claude -p failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-400:]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise SubscriptionLLMError(f"claude -p returned non-JSON: {proc.stdout[:200]}") from e
    answer = str(data.get('result') or '').strip()
    if not answer or data.get('is_error'):
        raise SubscriptionLLMError(f"claude -p returned no answer: {proc.stdout[:300]}")
    usage = data.get('usage') or {}
    total = None
    if usage:
        total = sum(int(usage.get(k) or 0) for k in (
            'input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))
    return {'content': answer, 'total': total, 'model': model or 'claude-default'}


def complete(prompt: str, system_message: Optional[str] = None,
             timeout: Optional[int] = None) -> Dict[str, Any]:
    """Same result shape as OpenAIService.complete()."""
    which = provider()
    if which not in PROVIDERS:
        raise SubscriptionLLMError(f"VOXLIBRI_LLM_PROVIDER={which!r} is not a subscription provider")
    text = _full_prompt(prompt, system_message)
    model = os.getenv('VOXLIBRI_CLI_MODEL') or None
    t = timeout or DEFAULT_TIMEOUT
    logger.info(f"Subscription completion via {which} ({len(text)} chars in)")
    try:
        run = _run_codex(text, model, t) if which == 'codex' else _run_claude(text, model, t)
    except subprocess.TimeoutExpired as e:
        raise SubscriptionLLMError(f"{which} timed out after {t}s") from e

    prompt_tokens = _estimate_tokens(text)
    completion_tokens = _estimate_tokens(run['content'])
    total = run['total'] or (prompt_tokens + completion_tokens)
    return {
        'content': run['content'],
        'model': f"{which}:{run['model']}",
        'tokens_used': total,
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'finish_reason': 'stop',
    }
