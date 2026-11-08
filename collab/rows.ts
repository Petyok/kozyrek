// Session entries → flat rows the bar can draw. One row per thing on screen: a
// user message, an assistant text block, a thinking block, a tool call (its
// result patches the same row), a note or a one-line meta event. Every row has
// every field, so a QML ListModel gets the same roles for all of them.
import type { AssistantMessage, ImageContent, SessionEntry, TextContent } from "@oh-my-pi/pi-wire";
import { COLLAB_PROMPT_MESSAGE_TYPE } from "@oh-my-pi/pi-wire";

export type Kind = "user" | "text" | "think" | "tool" | "note" | "meta" | "error";

export interface Row {
	id: string;
	k: Kind;
	/** Markdown for user/text/note, plain text for the rest; tool: its result. */
	text: string;
	/** user: who sent it; tool: tool name; note: label. */
	title: string;
	/** tool: intent or the main argument. */
	sub: string;
	/** tool: arguments, pretty JSON. */
	args: string;
	/** tool: result arrived. */
	done: boolean;
	/** tool: result is an error. */
	err: boolean;
	/** Image data URLs, one per line (a flat string keeps the QML ListModel roles flat). */
	images: string;
}

// Tool output can be megabytes (a full file read); the bar shows head and tail.
const MAX_RESULT = 16_000;
const MAX_ARGS = 6_000;
// Images bigger than this stay out of the bar (data URLs travel as JSON lines).
const MAX_IMAGE_B64 = 3_000_000;

export function row(id: string, k: Kind, text: string, extra: Partial<Row> = {}): Row {
	return { id, k, text, title: "", sub: "", args: "", done: true, err: false, images: "", ...extra };
}

export function clip(text: string, max: number): string {
	if (text.length <= max) return text;
	const half = Math.floor(max / 2);
	return `${text.slice(0, half)}\n\n… ${text.length - max} characters cut …\n\n${text.slice(-half)}`;
}

type Part = TextContent | ImageContent | { type: string };

function partsText(content: string | readonly Part[] | undefined): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.filter((p): p is TextContent => p.type === "text")
		.map(p => p.text)
		.join("\n");
}

function partsImages(content: string | readonly Part[] | undefined): string {
	if (!Array.isArray(content)) return "";
	return content
		.filter((p): p is ImageContent => p.type === "image")
		.filter(p => p.data.length <= MAX_IMAGE_B64)
		.map(p => `data:${p.mimeType};base64,${p.data}`)
		.join("\n");
}

/** Tool results and partial results: `{ content: Part[] }`, a bare Part[], or anything else. */
export function resultText(result: unknown): { text: string; images: string } {
	const content = Array.isArray(result)
		? result
		: result && typeof result === "object" && "content" in result
			? result.content
			: undefined;
	if (content !== undefined) {
		return { text: clip(partsText(content as Part[]), MAX_RESULT), images: partsImages(content as Part[]) };
	}
	if (result === undefined || result === null) return { text: "", images: "" };
	return { text: clip(typeof result === "string" ? result : JSON.stringify(result, null, 2), MAX_RESULT), images: "" };
}

const SUMMARY_KEYS = ["command", "path", "file", "pattern", "query", "url", "uri", "op", "task", "content", "code"];

/** The one argument that says what a call does: `bash` → its command, `read` → its path. */
export function toolSummary(args: Record<string, unknown>): string {
	let value: unknown;
	for (const key of SUMMARY_KEYS) if (typeof args[key] === "string") { value = args[key]; break; }
	if (value === undefined) value = Object.values(args).find(v => typeof v === "string");
	if (typeof value !== "string") return "";
	const line = value.trim().split("\n")[0] ?? "";
	return line.length > 140 ? `${line.slice(0, 140)}…` : line;
}

function toolRow(id: string, name: string, args: Record<string, unknown>, intent?: string): Row {
	const summary = toolSummary(args);
	const said = intent ?? (typeof args.i === "string" ? args.i : "");
	return row(id, "tool", "", {
		title: name,
		sub: said ? (summary ? `${said} · ${summary}` : said) : summary,
		args: clip(JSON.stringify(args, null, 2), MAX_ARGS),
		done: false,
	});
}

/** Rows of one assistant message; `prefix` keeps a streaming copy apart from the committed one. */
export function assistantRows(msg: AssistantMessage, prefix: string): Row[] {
	const out: Row[] = [];
	msg.content.forEach((part, i) => {
		const id = `${prefix}:${i}`;
		if (part.type === "text" && part.text.trim()) out.push(row(id, "text", part.text));
		else if (part.type === "thinking" && part.thinking.trim()) out.push(row(id, "think", part.thinking));
		else if (part.type === "toolCall") out.push(toolRow(part.id, part.name, part.arguments ?? {}, part.intent));
	});
	if (msg.stopReason === "error" && msg.errorMessage) out.push(row(`${prefix}:err`, "error", msg.errorMessage));
	else if (msg.stopReason === "aborted") out.push(row(`${prefix}:abort`, "meta", "stopped"));
	return out;
}

/** A session's rows, built incrementally from its entries. */
export class Transcript {
	rows: Row[] = [];
	readonly #index = new Map<string, number>();

	/**
	 * Adds entries; returns the rows they appended and the earlier rows they changed. A
	 * tool result fills in its call's row, also when the call came in the same batch, so
	 * `added` always carries the final version of each new row.
	 */
	push(entries: readonly SessionEntry[]): { added: Row[]; patched: Row[] } {
		const start = this.rows.length;
		const changed = new Set<number>();
		for (const entry of entries) {
			for (const r of this.#entryRows(entry)) {
				const at = this.#index.get(r.id);
				if (at === undefined) {
					this.#index.set(r.id, this.rows.length);
					this.rows.push(r);
					continue;
				}
				const call = this.rows[at]!;
				// A tool result fills in the call's row instead of adding one.
				this.rows[at] = r.k === "tool" && r.done ? { ...call, text: r.text, err: r.err, done: true, images: r.images } : r;
				if (at < start) changed.add(at);
			}
		}
		return { added: this.rows.slice(start), patched: [...changed].map(i => this.rows[i]!) };
	}

	/** A row that is not a session entry (a host notice). */
	note(r: Row): Row {
		this.#index.set(r.id, this.rows.length);
		this.rows.push(r);
		return r;
	}

	/** Live output of a running tool; returns the changed row, or null if the call is unknown or finished. */
	partial(id: string, result: unknown): Row | null {
		const at = this.#index.get(id);
		if (at === undefined || this.rows[at]!.done) return null;
		const { text, images } = resultText(result);
		const next = { ...this.rows[at]!, text, images };
		this.rows[at] = next;
		return next;
	}

	#entryRows(entry: SessionEntry): Row[] {
		const id = entry.id;
		switch (entry.type) {
			case "message": {
				const m = entry.message;
				switch (m.role) {
					case "user":
						return [row(id, m.synthetic ? "meta" : "user", partsText(m.content), { images: partsImages(m.content) })];
					case "developer":
						return [row(id, "note", partsText(m.content), { title: "system" })];
					case "assistant":
						return assistantRows(m, id);
					case "toolResult": {
						const { text, images } = resultText(m.content);
						return [row(m.toolCallId, "tool", text, { title: m.toolName, err: m.isError, images })];
					}
				}
				return [];
			}
			case "custom_message": {
				if (!entry.display) return [];
				const text = partsText(entry.content);
				if (entry.customType === COLLAB_PROMPT_MESSAGE_TYPE) {
					const d = entry.details;
					const from = d && typeof d === "object" && "from" in d && typeof d.from === "string" ? d.from : "";
					return [row(id, "user", text, { title: from, images: partsImages(entry.content) })];
				}
				return [row(id, "note", text, { title: entry.customType, images: partsImages(entry.content) })];
			}
			case "compaction":
				return [row(id, "note", entry.summary, { title: `context compacted (${entry.tokensBefore} tokens before)` })];
			case "branch_summary":
				return [row(id, "note", entry.summary, { title: "branch summary" })];
			case "model_change":
				return [row(id, "meta", `model → ${entry.model}`)];
			case "thinking_level_change":
				return [row(id, "meta", `thinking → ${entry.thinkingLevel ?? "default"}`)];
			default:
				return [];
		}
	}
}
