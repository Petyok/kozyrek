# omp chat (Collab sessions in the bar)

The robot pill follows [omp](https://omp.sh) sessions shared with Collab. It shows how many sessions
you watch, pulses while one works, turns yellow while one waits for an answer and shows a dot when
one finished a turn while you were not looking. Click (or `qs -c kozyrek ipc call collab toggle`) opens
the chat:

- one tab per watched session, `+` for the session list; `✕` on a tab stops watching it;
- a header with the folder, model, context use and who else is connected;
- the subagents as chips; a click shows that subagent's transcript, with kill / revive, and its message
  box talks to that subagent;
- the transcript: your and other guests' messages, the agent's text (Markdown), thinking (click to
  expand), tool calls (click for arguments and full output; a running one shows its last lines),
  compaction and system notes (collapsed), images;
- the agent's questions (`ask`): click an option, or type an answer, or cancel;
- a message box (Enter sends, Shift+Enter is a new line) and Stop while the agent works.

Notifications: "✔ <session>" with the last reply when a watched session finishes a turn and you are not
looking at it, a critical "❓ <session>" when it asks something, and "<session> ended". A click opens the
session.

## Setup

1. `bun` on the laptop (the helper is a Bun script).
2. List the machines to look at in `~/.config/kozyrek/collab-hosts`, one per line: `label = command`.
   The command reaches a machine that runs omp; the helper appends `omp collab list --json` /
   `omp collab link …` to it. An empty command means this laptop.
   ```
   my-server = ssh my-server
   laptop =
   ```
3. On that machine, share the sessions: `/collab` in omp, or `collab.autoStart: control` (or `view`) to share
   every session as it starts.

The session list in the popup shows every shared session on those hosts; "watch" joins one. Nothing is
joined automatically. A link from anywhere (`/collab` prints one) can also be pasted into the field
under the list.

## How it works

- `Collab.qml` (data) and `modules/Collab.qml` (pill and popup) talk JSON lines over
  `$XDG_RUNTIME_DIR/kozyrek-collab.sock` to `collab/kozyrek-collab.ts`.
- The helper runs as the transient user unit `kozyrek-collab` (`systemd-run`, started by the bar), so a
  bar reload or restart keeps the watched sessions. It exits a minute after the last bar disconnects.
- It joins each room as a guest named `kozyrek` (visible to everyone in the session) through omp's own
  browser guest code, vendored unchanged in `collab/vendor/` (MIT): WebSocket to the relay
  (`wss://my.omp.sh` by default), end-to-end AES-256-GCM, protocol version check at join.
  `collab/rows.ts` turns session entries into the rows the popup draws.
- Room links are secrets (a control link can steer the session). They live only in the helper's memory:
  never on disk, never in the bar. The list command prints none; the helper asks for one link per joined
  session (`omp collab link <instance> --json`, `--view` for view-only hosts).
- A session that moves to a new room (session switch, `/resume`) is followed: the helper lists the host
  again and joins the new room. A listed session that is gone ends its tab.
- Tool output is clipped to 16 000 characters (head and tail) and images over ~2 MB are left out, so a
  huge `read` cannot stall the bar.

## Following omp updates

The wire protocol is omp's (`COLLAB_PROTO`, currently 3). When omp changes it, joining fails with
"protocol mismatch" in the popup. Then:

```
cd collab && ./vendor.sh <omp version>     # e.g. 18.15.0, fetches the guest code at that tag
```

and check that a session still loads. `vendor/VERSION` says which omp the copy came from.

## Limits

- A session joined from a pasted link whose host went away shows "reconnecting…" until you close the
  tab: without a host to ask, the helper cannot tell a pause from the end.
- Images pasted into the message box are not sent (text only).
- Host-only omp commands (`/model`, `/compact`, `!bash`, …) are not available to guests.
