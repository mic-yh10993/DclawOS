/*
 * DclawOS Anthropic (Claude Messages) executor branch
 * (GPL-2.0-or-later; DclawOS-written code). Phase 4 module 1.
 *
 * Converts the agent loop's OpenAI-shaped request into an Anthropic
 * Messages request and the Messages response back into the
 * OpenAI-shaped body the loop consumes (choices[0].message with
 * tool_calls, usage as prompt_tokens/completion_tokens).
 *
 * // reuse-check: ADAPTED from OmniRoute's translator pair
 *   open-sse/translator/request/openai-to-claude.ts
 *   (openaiToClaudeRequest, :167) and
 *   open-sse/translator/response/claude-to-openai.ts
 *   (claudeToOpenAIResponse, :62) — the core message mapping only:
 *   system hoisting, role/block conversion, tool_use/tool_result
 *   pairing, usage field mapping. NOT copied verbatim in this
 *   module, reason: the upstream pair's closure pulls the whole
 *   translator registry plus thinking-block replay state
 *   (services/claudeThinkingStore) and cache-control/deferred-tool
 *   machinery built for streaming gateway traffic; a verbatim copy
 *   is tracked as gap A2's completion step and this adaptation is
 *   replaced if that closure bundles cleanly. Behaviour for
 *   standard chat + tool-use turns is equivalent; thinking-block
 *   replay across turns is the known delta.
 */

import type { LlmRequest } from "./llm";

interface RegistryEntryLike {
  baseUrl?: string;
  baseUrls?: string[];
  urlSuffix?: string;
  authHeader?: string;
  headers?: Record<string, string>;
  extraHeaders?: Record<string, string>;
}

interface OaiMessage {
  role: string;
  content?: unknown;
  tool_calls?: Array<{
    id: string;
    function?: { name: string; arguments: string };
  }>;
  tool_call_id?: string;
  name?: string;
}

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (part && typeof part === "object" && "text" in part)
          return String((part as { text: unknown }).text ?? "");
        return "";
      })
      .join("");
  }
  return content == null ? "" : String(content);
}

export function openaiToClaudeBody(
  request: LlmRequest,
  model: string
): Record<string, unknown> {
  const messages = request.messages as OaiMessage[];
  let system = "";
  const out: Array<Record<string, unknown>> = [];
  for (const msg of messages) {
    if (msg.role === "system") {
      system += (system ? "\n" : "") + textOf(msg.content);
      continue;
    }
    if (msg.role === "tool") {
      out.push({
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: msg.tool_call_id ?? "",
            content: textOf(msg.content),
          },
        ],
      });
      continue;
    }
    if (msg.role === "assistant" && msg.tool_calls?.length) {
      const blocks: Array<Record<string, unknown>> = [];
      const text = textOf(msg.content);
      if (text) blocks.push({ type: "text", text });
      for (const call of msg.tool_calls) {
        let input: unknown = {};
        try {
          input = JSON.parse(call.function?.arguments ?? "{}");
        } catch {
          input = {};
        }
        blocks.push({
          type: "tool_use",
          id: call.id,
          name: call.function?.name ?? "",
          input,
        });
      }
      out.push({ role: "assistant", content: blocks });
      continue;
    }
    out.push({ role: msg.role, content: textOf(msg.content) });
  }
  const body: Record<string, unknown> = {
    model,
    messages: out,
    max_tokens: typeof request.maxTokens === "number" ? request.maxTokens : 4096,
  };
  if (system) body.system = system;
  if (request.tools && request.tools.length > 0) {
    body.tools = (request.tools as Array<{
      function?: { name: string; description?: string; parameters?: unknown };
      name?: string;
      description?: string;
      input_schema?: unknown;
    }>).map((tool) => {
      const fn = tool.function ?? tool;
      return {
        name: fn.name ?? "",
        description: fn.description ?? "",
        input_schema: (fn as { parameters?: unknown }).parameters ??
          (tool as { input_schema?: unknown }).input_schema ?? {
            type: "object",
            properties: {},
          },
      };
    });
  }
  return body;
}

export function claudeToOpenaiBody(
  claude: Record<string, unknown>,
  model: string
): Record<string, unknown> {
  const blocks = Array.isArray(claude.content) ? claude.content : [];
  let text = "";
  const toolCalls: Array<Record<string, unknown>> = [];
  for (const block of blocks as Array<Record<string, unknown>>) {
    if (block.type === "text") text += String(block.text ?? "");
    if (block.type === "tool_use") {
      toolCalls.push({
        id: String(block.id ?? ""),
        type: "function",
        function: {
          name: String(block.name ?? ""),
          arguments: JSON.stringify(block.input ?? {}),
        },
      });
    }
  }
  const usage = (claude.usage ?? {}) as {
    input_tokens?: number;
    output_tokens?: number;
  };
  const message: Record<string, unknown> = { role: "assistant", content: text };
  if (toolCalls.length > 0) message.tool_calls = toolCalls;
  return {
    id: String(claude.id ?? ""),
    model: String(claude.model ?? model),
    choices: [
      {
        index: 0,
        message,
        finish_reason: toolCalls.length > 0 ? "tool_calls" : "stop",
      },
    ],
    usage: {
      prompt_tokens: Number(usage.input_tokens ?? 0),
      completion_tokens: Number(usage.output_tokens ?? 0),
    },
  };
}

export async function executeClaudeTarget(
  request: LlmRequest,
  target: { provider: string; model: string },
  entry: RegistryEntryLike,
  signal: AbortSignal | undefined
): Promise<{ ok: boolean; status: number; body: unknown }> {
  const base =
    request.baseUrlOverrides?.[target.provider] ??
    entry.baseUrl ??
    entry.baseUrls?.[0];
  if (!base) {
    return { ok: false, status: 0, body: { error: "no endpoint for anthropic" } };
  }
  const url = entry.urlSuffix && !base.includes("?") ? base + entry.urlSuffix : base;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(entry.headers ?? {}),
    ...(entry.extraHeaders ?? {}),
  };
  const key = request.keys[target.provider];
  if (key) headers[entry.authHeader ?? "x-api-key"] = key;
  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(openaiToClaudeBody(request, target.model)),
    signal,
  });
  let parsed: unknown = null;
  try {
    parsed = await res.json();
  } catch {
    parsed = null;
  }
  if (!res.ok || !parsed || typeof parsed !== "object") {
    return { ok: res.ok, status: res.status, body: parsed };
  }
  return {
    ok: true,
    status: res.status,
    body: claudeToOpenaiBody(parsed as Record<string, unknown>, target.model),
  };
}
