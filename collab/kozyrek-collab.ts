#!/usr/bin/env bun
// kozyrek-collab: follows omp Collab sessions for the bar.
//
// Joins sessions as a guest named "kozyrek" through omp's own guest code
// (vendor/, see vendor.sh), keeps their transcripts as rows (rows.ts) and serves
// them to the bar as JSON lines on a Unix socket. Room links (secrets) live in
// this process's memory only. Sessions are found by running `omp collab list`
// and `omp collab link` through the commands in ~/.config/kozyrek/collab-hosts,
// or joined from a pasted link. Runs detached from the bar so a reload keeps
// the sessions; exits a minute after the last bar disconnects.
import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { createConnection } from "node:net";
import { homedir } from "node:os";
import type { Socket } from "bun";
import type { AgentSnapshot, CollabUiRequest, SessionEntry } from "@oh-my-pi/pi-wire";
import { GuestClient, type GuestSnapshot } from "./vendor/web/client.ts";
import { parseJsonl } from "./vendor/web/jsonl.ts";
import { assistantRows, row, type Row, Transcript } from "./rows.ts";

const SOCK = process.env.KOZYREK_COLLAB_SOCK || `${process.env.XDG_RUNTIME_DIR || "/tmp"}/kozyrek-collab.sock`;
const HOSTS_FILE = process.env.KOZYREK_COLLAB_HOSTS || `${homedir()}/.config/kozyrek/collab-hosts`;
const NOTIFY = process.env.KOZYREK_COLLAB_NOTIFY !== "0";
const NAME = "kozyrek";
const IDLE_EXIT_MS = 60_000;
const LIVE_MS = 120; // streaming text and running-tool output reach the bar at most this often
const META_MS = 200;
const AGENT_POLL_MS = 1_500;
const CMD_TIMEOUT_MS = 30_000;

const log = (...a: unknown[]): void => console.error("kozyrek-collab:", ...a);

// ── hosts: `label = command`, the command reaches a machine that runs omp ──────

interface HostCfg {
	label: string;
	cmd: string[];
}

interface Listed {
	instanceId: string;
	generation: number;
	sessionName?: string;
	cwd: string;
	model?: { provider: string; id: string };
	busy: boolean | null;
	inputRequired: boolean;
	access: "view" | "control";
}

function readHosts(): HostCfg[] {
	if (!existsSync(HOSTS_FILE)) return [];
	const out: HostCfg[] = [];
	for (const raw of readFileSync(HOSTS_FILE, "utf8").split("\n")) {
		const line = raw.trim();
		if (!line || line.startsWith("#")) continue;
		const eq = line.indexOf("=");
		if (eq < 0) {
			log(`collab-hosts: skipping "${line}" (want: label = command)`);
			continue;
		}
		out.push({ label: line.slice(0, eq).trim(), cmd: line.slice(eq + 1).trim().split(/\s+/).filter(Boolean) });
	}
	return out;
}

async function run(cmd: string[]): Promise<{ ok: boolean; out: string; err: string }> {
	try {
		const p = Bun.spawn(cmd, { stdout: "pipe", stderr: "pipe", stdin: "ignore", signal: AbortSignal.timeout(CMD_TIMEOUT_MS) });
		const [out, err, code] = await Promise.all([new Response(p.stdout).text(), new Response(p.stderr).text(), p.exited]);
		return { ok: code === 0, out, err: err.trim() || (code === 0 ? "" : `exit ${code}`) };
	} catch (e) {
		return { ok: false, out: "", err: e instanceof Error ? e.message : String(e) };
	}
}

function omp(host: HostCfg, ...args: string[]): Promise<{ ok: boolean; out: string; err: string }> {
	return run([...host.cmd, "omp", "collab", ...args]);
}

async function listHost(host: HostCfg): Promise<{ hosts: Listed[]; error: string }> {
	const r = await omp(host, "list", "--json");
	if (!r.ok) return { hosts: [], error: r.err.split("\n").pop() ?? r.err };
	try {
		const parsed: unknown = JSON.parse(r.out);
		const hosts = parsed && typeof parsed === "object" && "hosts" in parsed && Array.isArray(parsed.hosts) ? parsed.hosts : [];
		return { hosts: hosts as Listed[], error: "" }; // shape owned by `omp collab list --json` v1
	} catch {
		return { hosts: [], error: "omp collab list printed something that is not JSON" };
	}
}

async function linkFor(host: HostCfg, listed: Listed): Promise<string> {
	const args = ["link", listed.instanceId, "--json"];
	if (listed.access !== "control") args.push("--view");
	const r = await omp(host, ...args);
	if (!r.ok) throw new Error(r.err.split("\n").pop() || "omp collab link failed");
	const parsed: unknown = JSON.parse(r.out);
	if (!parsed || typeof parsed !== "object" || !("url" in parsed) || typeof parsed.url !== "string") {
		throw new Error("omp collab link gave no url");
	}
	return parsed.url;
}

const listing = new Map<string, { hosts: Listed[]; error: string }>();
let discovering: Promise<void> | null = null;

function discover(): Promise<void> {
	discovering ??= (async () => {
		const hosts = readHosts();
		for (const label of listing.keys()) if (!hosts.some(h => h.label === label)) listing.delete(label);
		await Promise.all(
			hosts.map(async h => {
				listing.set(h.label, await listHost(h));
			}),
		);
		broadcast(hostsMsg());
	})().finally(() => {
		discovering = null;
	});
	return discovering;
}

function hostsMsg(): object {
	return {
		t: "hosts",
		configured: existsSync(HOSTS_FILE),
		hosts: [...listing].map(([label, l]) => ({
			label,
			error: l.error,
			sessions: l.hosts.map(h => ({
				instanceId: h.instanceId,
				title: h.sessionName || h.cwd.split("/").pop() || h.instanceId,
				cwd: h.cwd,
				model: h.model?.id ?? "",
				busy: h.busy === true,
				ask: h.inputRequired,
				access: h.access,
				watching: sessions.has(`${label}/${h.instanceId}`),
			})),
		})),
	};
}

// ── sessions ───────────────────────────────────────────────────────────────────

interface AgentView {
	tr: Transcript;
	cursor: number;
	carry: string;
	error: string;
	timer: Timer | null;
	busy: boolean;
}

class Session {
	readonly sid: string;
	readonly label: string;
	readonly host: HostCfg | null;
	readonly instanceId: string;
	generation = 0;
	client!: GuestClient;
	snap!: GuestSnapshot;
	tr = new Transcript();
	live: Row[] = [];
	unread = false;
	final = ""; // set once the session is gone for good
	#seen = 0;
	#firstId: string | undefined;
	#entries: readonly SessionEntry[] | null = null;
	#stream: GuestSnapshot["stream"] = null;
	#tools: GuestSnapshot["activeTools"] | null = null;
	#notice = 0;
	#working = false;
	#welcomed = false;
	#askId = -1;
	#phase = "";
	#liveTimer: Timer | null = null;
	#livePatched = new Map<string, Row>();
	#recheck: Timer | null = null;
	#recheckTries = 0;
	agentViews = new Map<string, AgentView>();

	constructor(sid: string, label: string, host: HostCfg | null, instanceId: string) {
		this.sid = sid;
		this.label = label;
		this.host = host;
		this.instanceId = instanceId;
	}

	/** (Re)joins through a fresh link; a new room starts a new transcript. */
	attach(link: string): void {
		this.client?.close();
		const client = new GuestClient(link, NAME); // throws on a bad link
		this.client = client;
		this.snap = client.getSnapshot();
		this.tr = new Transcript();
		this.live = [];
		this.#seen = 0;
		this.#firstId = undefined;
		this.#entries = null;
		this.#welcomed = false;
		this.#working = false;
		this.#notice = 0;
		this.#askId = -1;
		this.final = "";
		for (const v of this.agentViews.values()) clearInterval(v.timer ?? undefined);
		this.agentViews.clear();
		client.subscribe(() => {
			if (this.client !== client) return;
			try {
				this.#onCommit(client.getSnapshot());
			} catch (e) {
				log(`${this.sid}: commit failed`, e);
			}
		});
		client.connect();
		toViewers(this.sid, "", () => ({ t: "reset", sid: this.sid, agent: "", rows: [] }));
	}

	get title(): string {
		const listed = this.host ? listing.get(this.label)?.hosts.find(h => h.instanceId === this.instanceId) : undefined;
		const cwd = this.snap.state?.cwd ?? this.snap.header?.cwd ?? listed?.cwd ?? "";
		return this.snap.state?.sessionName || this.snap.header?.title || listed?.sessionName || cwd.split("/").pop() || this.sid;
	}

	#onCommit(s: GuestSnapshot): void {
		this.snap = s;
		if (s.entries !== this.#entries) this.#onEntries(s.entries);
		if (s.stream !== this.#stream) {
			this.#stream = s.stream;
			this.live = s.stream ? assistantRows(s.stream, "live") : [];
			this.#scheduleLive();
		}
		if (s.activeTools !== this.#tools) {
			this.#tools = s.activeTools;
			for (const t of s.activeTools.values()) {
				if (t.partialResult === undefined) continue;
				const r = this.tr.partial(t.toolCallId, t.partialResult);
				if (r) this.#livePatched.set(r.id, r);
			}
			this.#scheduleLive();
		}
		for (const n of s.notices) {
			if (n.id <= this.#notice) continue;
			this.#notice = n.id;
			const r = this.tr.note(row(`notice:${n.id}`, n.level === "error" ? "error" : "meta", n.message));
			toViewers(this.sid, "", () => ({ t: "rows", sid: this.sid, agent: "", added: [r], patched: [] }));
		}
		this.#onState(s);
		scheduleMeta();
	}

	#onEntries(entries: readonly SessionEntry[]): void {
		this.#entries = entries;
		// A resync (reconnect, new welcome) republishes the whole transcript: same
		// session → same prefix, so only the tail is new; anything else restarts.
		const reset = entries.length < this.#seen || (this.#seen > 0 && entries[0]?.id !== this.#firstId);
		if (reset) {
			this.tr = new Transcript();
			this.#seen = 0;
		}
		const { added, patched } = this.tr.push(entries.slice(this.#seen));
		this.#seen = entries.length;
		this.#firstId = entries[0]?.id;
		if (reset) toViewers(this.sid, "", () => ({ t: "reset", sid: this.sid, agent: "", rows: this.tr.rows }));
		else if (added.length || patched.length) toViewers(this.sid, "", () => ({ t: "rows", sid: this.sid, agent: "", added, patched }));
	}

	#onState(s: GuestSnapshot): void {
		const live = s.phase === "live";
		if (live && !this.#welcomed) {
			// First complete snapshot: nothing that happened before it is news.
			this.#welcomed = true;
			this.#working = s.working;
			this.#askId = s.uiRequest?.reqId ?? -1;
			this.#recheckTries = 0;
		}
		if (this.#welcomed) {
			if (this.#working && !s.working) this.#finished();
			this.#working = s.working;
			const ask = s.uiRequest?.reqId ?? -1;
			if (ask !== -1 && ask !== this.#askId && !viewed(this.sid)) {
				notify(this.sid, `❓ ${this.title}`, s.uiRequest?.title ?? "", true);
			}
			this.#askId = ask;
		}
		if (s.phase !== this.#phase) {
			this.#phase = s.phase;
			if (s.phase === "ended") this.#gone(s.endedReason ?? "ended");
			else if (s.phase === "reconnecting") this.#scheduleRecheck(20_000);
			else if (this.#recheck && s.phase === "live") {
				clearTimeout(this.#recheck);
				this.#recheck = null;
			}
		}
	}

	#finished(): void {
		if (viewed(this.sid)) return;
		this.unread = true;
		const last = [...this.tr.rows].reverse().find(r => r.k === "text" || r.k === "error");
		const body = last ? (last.text.length > 300 ? `${last.text.slice(0, 300)}…` : last.text) : "";
		notify(this.sid, `✔ ${this.title}`, body, false);
	}

	/** The room ended. A listed session may just have moved to a new room (session switch, /resume). */
	#gone(reason: string): void {
		if (this.host) this.#scheduleRecheck(2_000, reason);
		else this.#end(reason);
	}

	#end(reason: string): void {
		if (this.final) return;
		this.final = reason;
		notify(this.sid, `${this.title} ended`, reason, false);
		scheduleMeta();
	}

	#scheduleRecheck(ms: number, reason = "connection lost"): void {
		if (this.#recheck || !this.host || this.final) return;
		this.#recheck = setTimeout(async () => {
			this.#recheck = null;
			if (!sessions.has(this.sid)) return;
			const l = await listHost(this.host!);
			if (l.error) listing.set(this.label, { ...(listing.get(this.label) ?? { hosts: [] }), error: l.error });
			else listing.set(this.label, l);
			const found = l.hosts.find(h => h.instanceId === this.instanceId);
			const phase = this.client.getSnapshot().phase;
			if (!l.error && !found) return this.#end(reason);
			if (found && (found.generation !== this.generation || phase === "ended")) {
				try {
					const link = await linkFor(this.host!, found);
					if (!sessions.has(this.sid)) return;
					this.generation = found.generation;
					this.attach(link);
					return;
				} catch (e) {
					log(`${this.sid}: relink failed`, e);
				}
			}
			if (phase === "live") return;
			if (++this.#recheckTries >= 6) return this.#end(reason);
			this.#scheduleRecheck(10_000 * this.#recheckTries, reason);
		}, ms);
	}

	#scheduleLive(): void {
		this.#liveTimer ??= setTimeout(() => {
			this.#liveTimer = null;
			const patched = [...this.#livePatched.values()];
			this.#livePatched.clear();
			toViewers(this.sid, "", () => ({ t: "live", sid: this.sid, rows: this.live }));
			if (patched.length) toViewers(this.sid, "", () => ({ t: "rows", sid: this.sid, agent: "", added: [], patched }));
		}, LIVE_MS);
	}

	meta(): object {
		const s = this.snap;
		const st = s.state;
		return {
			sid: this.sid,
			label: this.label,
			title: this.title,
			cwd: st?.cwd ?? s.header?.cwd ?? "",
			model: st?.model?.name ?? "",
			thinking: st?.thinkingLevel ?? "",
			ctx: st?.contextUsage?.percent ?? -1,
			working: s.working,
			aborting: st?.isAborting === true,
			queued: st?.queuedMessageCount ?? 0,
			phase: this.final ? "ended" : s.phase,
			ended: this.final || (s.phase === "ended" ? (s.endedReason ?? "") : ""),
			readOnly: s.readOnly,
			loading: s.loading ? `${s.loading.received}/${s.loading.total}` : "",
			people: (st?.participants ?? []).map(p => p.name),
			unread: this.unread,
			ask: s.uiRequest ? askOut(s.uiRequest) : null,
			agents: s.agents.filter(a => a.kind !== "main").map(a => this.#agentOut(a)),
		};
	}

	#agentOut(a: AgentSnapshot): object {
		const p = this.snap.progress.get(a.id)?.progress;
		const l = this.snap.lifecycle.get(a.id);
		return {
			id: a.id,
			name: a.displayName,
			status: a.status,
			file: a.hasSessionFile,
			task: p?.description || l?.description || p?.task || "",
			intent: p?.lastIntent ?? "",
			tool: p?.currentTool ? `${p.currentTool} ${p.currentToolArgs ?? ""}`.trim() : "",
			tokens: p?.tokens ?? 0,
			cost: p?.cost ?? 0,
			ms: p?.durationMs ?? 0,
		};
	}

	/** Subagent transcript: the host's session file, read incrementally while someone looks at it. */
	watchAgent(agentId: string): void {
		let v = this.agentViews.get(agentId);
		if (!v) {
			v = { tr: new Transcript(), cursor: 0, carry: "", error: "", timer: null, busy: false };
			this.agentViews.set(agentId, v);
		}
		const view = v;
		toViewers(this.sid, agentId, () => ({ t: "reset", sid: this.sid, agent: agentId, rows: view.tr.rows }));
		if (view.timer || view.error) return;
		const poll = async (): Promise<void> => {
			if (view.busy) return;
			if (!isViewed(this.sid, agentId)) {
				clearInterval(view.timer ?? undefined);
				view.timer = null;
				return;
			}
			view.busy = true;
			try {
				const reply = await this.client.fetchTranscript(agentId, view.cursor);
				if (reply === null) return; // transient: same cursor next time
				if (reply.kind === "error") {
					view.error = reply.message;
					clearInterval(view.timer ?? undefined);
					view.timer = null;
					const r = view.tr.note(row(`agent-error`, "error", `transcript: ${reply.message}`));
					toViewers(this.sid, agentId, () => ({ t: "rows", sid: this.sid, agent: agentId, added: [r], patched: [] }));
					return;
				}
				const parsed = parseJsonl(reply.text, view.carry);
				view.cursor = reply.newSize;
				view.carry = parsed.carry;
				// Transcript lines; Transcript skips entry types it does not draw.
				const lines = parsed.items.filter(
					(item): item is SessionEntry => !!item && typeof item === "object" && "type" in item && item.type !== "session",
				);
				const { added, patched } = view.tr.push(lines);
				if (added.length || patched.length) {
					toViewers(this.sid, agentId, () => ({ t: "rows", sid: this.sid, agent: agentId, added, patched }));
				}
			} finally {
				view.busy = false;
			}
		};
		view.timer = setInterval(poll, AGENT_POLL_MS);
		void poll();
	}

	close(): void {
		clearTimeout(this.#recheck ?? undefined);
		clearTimeout(this.#liveTimer ?? undefined);
		for (const v of this.agentViews.values()) clearInterval(v.timer ?? undefined);
		this.client?.close();
	}
}

function askOut(r: CollabUiRequest): object {
	if (r.kind === "editor") return { reqId: r.reqId, kind: "editor", title: r.title, prefill: r.prefill ?? "", options: [], help: "" };
	return {
		reqId: r.reqId,
		kind: "select",
		title: r.title,
		prefill: "",
		help: r.helpText ?? "",
		options: r.options.map((o, i) => {
			const label = typeof o === "string" ? o : o.label;
			const checked = r.checkedIndices?.includes(i) ?? false;
			const mark = r.selectionMarker === "checkbox" ? (checked ? "☑ " : "☐ ") : r.selectionMarker === "radio" ? (checked ? "◉ " : "○ ") : "";
			return { label, mark, desc: typeof o === "string" ? "" : (o.description ?? "") };
		}),
	};
}

const sessions = new Map<string, Session>();

let metaTimer: Timer | null = null;
function scheduleMeta(): void {
	metaTimer ??= setTimeout(() => {
		metaTimer = null;
		broadcast(sessionsMsg());
	}, META_MS);
}
function sessionsMsg(): object {
	return { t: "sessions", list: [...sessions.values()].map(s => s.meta()) };
}

async function joinListed(label: string, instanceId: string): Promise<void> {
	const sid = `${label}/${instanceId}`;
	if (sessions.has(sid)) return;
	const host = readHosts().find(h => h.label === label);
	if (!host) throw new Error(`no host "${label}" in ${HOSTS_FILE}`);
	let listed = listing.get(label)?.hosts.find(h => h.instanceId === instanceId);
	let link: string;
	try {
		if (!listed) throw new Error("not listed");
		link = await linkFor(host, listed);
	} catch {
		// stale_generation or not listed yet: list again, once
		const l = await listHost(host);
		listing.set(label, l);
		listed = l.hosts.find(h => h.instanceId === instanceId);
		if (!listed) throw new Error(l.error || "that session is gone");
		link = await linkFor(host, listed);
	}
	const s = new Session(sid, label, host, instanceId);
	s.generation = listed.generation;
	sessions.set(sid, s);
	s.attach(link);
	broadcast(hostsMsg());
	scheduleMeta();
}

function joinLink(link: string): void {
	const room = /([A-Za-z0-9_-]{10,64})[#.][A-Za-z0-9_-]+\s*$/.exec(link.trim())?.[1];
	if (!room) throw new Error("that is not a collab link");
	const sid = `link/${room}`;
	if (sessions.has(sid)) return;
	const s = new Session(sid, "link", null, "");
	s.attach(link); // throws before it is listed when the link does not parse
	sessions.set(sid, s);
	scheduleMeta();
}

function leave(sid: string): void {
	const s = sessions.get(sid);
	if (!s) return;
	sessions.delete(sid);
	s.close();
	broadcast(hostsMsg());
	scheduleMeta();
}

// ── notifications ──────────────────────────────────────────────────────────────

function notify(sid: string, summary: string, body: string, critical: boolean): void {
	if (!NOTIFY) return;
	const p = Bun.spawn(
		["notify-send", "-a", "kozyrek", "-u", critical ? "critical" : "normal", "-A", "default=Open", "--wait", summary, body],
		{ stdout: "pipe", stderr: "ignore", stdin: "ignore" },
	);
	void new Response(p.stdout).text().then(out => {
		if (out.trim() === "default") broadcast({ t: "focus", sid });
	});
}

// ── bar clients ────────────────────────────────────────────────────────────────

interface Client {
	buf: string;
	out: Buffer[];
	sid: string; // what this bar's popup shows; "" = closed or the session picker
	agent: string;
}

const clients = new Set<Socket<Client>>();
let idleTimer: Timer | null = null;

function flush(sock: Socket<Client>): void {
	const c = sock.data;
	while (c.out.length) {
		const b = c.out[0]!;
		const n = sock.write(b);
		if (n <= 0) return;
		if (n < b.length) {
			c.out[0] = b.subarray(n);
			return;
		}
		c.out.shift();
	}
}

function send(sock: Socket<Client>, msg: object): void {
	sock.data.out.push(Buffer.from(`${JSON.stringify(msg)}\n`));
	flush(sock);
}

function broadcast(msg: object): void {
	if (clients.size === 0) return;
	const line = Buffer.from(`${JSON.stringify(msg)}\n`);
	for (const sock of clients) {
		sock.data.out.push(line);
		flush(sock);
	}
}

/** Sends to bars showing this view; `make` runs only when one does. */
function toViewers(sid: string, agent: string, make: () => object): void {
	let line: Buffer | null = null;
	for (const sock of clients) {
		if (sock.data.sid !== sid || sock.data.agent !== agent) continue;
		line ??= Buffer.from(`${JSON.stringify(make())}\n`);
		sock.data.out.push(line);
		flush(sock);
	}
}

const isViewed = (sid: string, agent: string): boolean => [...clients].some(c => c.data.sid === sid && c.data.agent === agent);
const viewed = (sid: string): boolean => [...clients].some(c => c.data.sid === sid);

function str(v: unknown): string {
	return typeof v === "string" ? v : "";
}

async function handle(sock: Socket<Client>, msg: Record<string, unknown>): Promise<void> {
	const s = sessions.get(str(msg.sid));
	switch (msg.t) {
		case "discover":
			await discover();
			return;
		case "join":
			try {
				if (msg.link) joinLink(str(msg.link));
				else await joinListed(str(msg.label), str(msg.instanceId));
			} catch (e) {
				send(sock, { t: "error", message: e instanceof Error ? e.message : String(e) });
			}
			return;
		case "leave":
			leave(str(msg.sid));
			return;
		case "view": {
			sock.data.sid = s ? s.sid : "";
			sock.data.agent = s ? str(msg.agent) : "";
			if (!s) return;
			if (s.unread) {
				s.unread = false;
				scheduleMeta();
			}
			if (sock.data.agent) s.watchAgent(sock.data.agent);
			else {
				send(sock, { t: "reset", sid: s.sid, agent: "", rows: s.tr.rows });
				send(sock, { t: "live", sid: s.sid, rows: s.live });
			}
			return;
		}
		case "prompt":
			if (s && str(msg.text).trim()) s.client.sendPrompt(str(msg.text));
			return;
		case "abort":
			s?.client.sendAbort();
			return;
		case "answer":
			if (s && typeof msg.reqId === "number") s.client.sendUiResponse(msg.reqId, typeof msg.value === "string" ? msg.value : undefined);
			return;
		case "agent": {
			const cmd = msg.cmd;
			if (s && (cmd === "chat" || cmd === "kill" || cmd === "revive")) s.client.sendAgentCmd(cmd, str(msg.agentId), str(msg.text) || undefined);
			return;
		}
	}
}

// One helper per socket: a second start finds the first answering and leaves.
async function alreadyRunning(): Promise<boolean> {
	if (!existsSync(SOCK)) return false;
	const { promise, resolve } = Promise.withResolvers<boolean>();
	const c = createConnection(SOCK);
	c.once("connect", () => {
		c.destroy();
		resolve(true);
	});
	c.once("error", () => resolve(false));
	return promise;
}

if (await alreadyRunning()) {
	log("already running on", SOCK);
	process.exit(0);
}
if (existsSync(SOCK)) unlinkSync(SOCK);

function armIdleExit(): void {
	if (clients.size > 0 || idleTimer) return;
	idleTimer = setTimeout(() => {
		log("no bar connected for a minute, exiting");
		shutdown();
	}, IDLE_EXIT_MS);
}

function shutdown(): never {
	for (const s of sessions.values()) s.close();
	try {
		unlinkSync(SOCK);
	} catch {}
	process.exit(0);
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

Bun.listen<Client>({
	unix: SOCK,
	socket: {
		open(sock) {
			sock.data = { buf: "", out: [], sid: "", agent: "" };
			clients.add(sock);
			clearTimeout(idleTimer ?? undefined);
			idleTimer = null;
			send(sock, hostsMsg());
			send(sock, sessionsMsg());
		},
		data(sock, chunk) {
			const lines = (sock.data.buf + chunk.toString()).split("\n");
			sock.data.buf = lines.pop() ?? "";
			for (const line of lines) {
				if (!line.trim()) continue;
				let msg: unknown;
				try {
					msg = JSON.parse(line);
				} catch {
					log("bad line from bar:", line.slice(0, 200));
					continue;
				}
				if (msg && typeof msg === "object") void handle(sock, msg as Record<string, unknown>).catch(e => log("handle", e));
			}
		},
		drain(sock) {
			flush(sock);
		},
		close(sock) {
			clients.delete(sock);
			armIdleExit();
		},
		error(sock, e) {
			log("socket", e);
			clients.delete(sock);
			armIdleExit();
		},
	},
});
log("listening on", SOCK);
armIdleExit();
