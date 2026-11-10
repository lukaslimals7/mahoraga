# Regressions ledger

Every bug that was found and fixed, with the test that stops it coming back.
Before changing the TUI, read this list — most of these were *one line* away from
reappearing, and each one looked like "the terminal is broken".

**Standing rule:** a change is not done until the existing tests still pass. Add a
test for anything new; if a fix has no test, it will regress.

**Adding a fix — three steps, in this order:**

1. **Pin the current behaviour first.** If the thing you're about to touch has no
   test, write one that passes *today*. Then change the code and watch it still pass.
2. **Fix, then add a row here** with the symptom in the user's words ("the output
   gets scrambled"), the real cause, and the guard.
3. **`./run-tests.sh`** — one command, one exit code. `tests/test_ledger.py` fails if
   a row names a test that doesn't exist.

| # | Symptom | Cause | Fix | Guarded by |
|---|---|---|---|---|
| 1 | Wheel looked "pressed and compressed" | Art is parametric (`rx = w*0.285`, `ry = h*0.245`), so shape follows the w/h ratio; height was shrunk while width stayed fixed | Scale both axes from `_WHEEL_ASPECT`, and never render below the 21-row design height | `test_stream_order.py` (wheel modes) |
| 2 | Answer appeared **above** the command card it followed | `_stream_idx` was reset only at turn boundaries; after a tool call the next sentence appended to the previous answer block | `tool_call()` and `on_thinking()` clear `_stream_idx` | `test_stream_order.py` |
| 3 | Text painted over other rows — "scrambled output" | Command output was rendered raw; CSI/OSC escapes from `ls --color`, apt, nmap move the cursor inside an absolutely-addressed pane | `_clean()` strips every escape and control char before wrapping | `test_stream_order.py` (escape leak assertions) |
| 4 | View drifted while streaming, jumped on resize | `scroll_top` was an absolute row index into a list that grows and rewraps | `scroll_off` = rows back from the tail, incremented by the rows that arrive | `test_scroll.py` |
| 5 | Old band visible behind a plain row | Rows cleared to end-of-line (`EL`) instead of whole-line (`2K`) | every pane row starts with `CSI 2K` | `test_render.py` |
| 6 | `SQLite objects created in a thread can only be used in that same thread` | One module-global connection; turns moved to a worker thread | thread-local connections + WAL | manual (needs a threaded run) |
| 7 | Queue position reported wrong | `submit()` used `qsize()`, which the worker decrements instantly | explicit counter, and a test pinning "1 = next in line" | `test_queue.py` |
| 8 | `Ctrl+C` did nothing while a reply streamed | The tty keeps `ISIG` on, so `^C` arrives as **SIGINT**, not a `0x03` byte; the editor's INTERRUPT branch never runs | the `except KeyboardInterrupt` around `input_line()` routes to the same handler | `test_cancel.py` + pty run |
| 9 | Cancelling surfaced as an error | `resp.close()` from another thread makes the reader raise `AttributeError`, not `OSError` | catch `Exception`, then convert to `Cancelled` only if the flag is set | `test_cancel.py` |
| 10 | Wheel sprang back after every reply | `busy` was the only condition, so height returned to full the moment the turn ended | `_ever_busy` latch + `/wheel sticky|auto|full|<rows>` | `test_stream_order.py` |
| 11 | `/cd`, `/pwd`, `/mouse`, `/reasoning`, `/wheel` invisible to Tab | `COMMANDS` was a second, hand-maintained list that drifted from the dispatcher | one `COMMAND_SPEC` table feeds completion, `/help`, and a drift test | `test_commands.py` |
| 12 | Tab froze the prompt on `/model ` | completion called `available_models()`, which does a server round-trip on the input thread | `model_names_cached()` — config + current only; discovery stays on `/model refresh` | `test_commands.py` |
| 13 | A worker error printed over the UI | `print()` in `Turns._worker` writes into an absolutely-addressed screen | report into the pane; the worker only re-raises to `run_turn` | code review |
| 15 | Cursor parked **under the banner**, not on the prompt row | the slim wheel branch saved the cursor (`\033[s`) but never restored it, so each 80 ms repaint left it at row 4 and the later paints restored *to that* | restore at the end of the slim branch too | `test_cursor_position.py` |
| 14 | Cursor sat still (no blink) | Only shown/hidden (`?25h`/`?25l`); terminals keep whatever DECSCUSR they were last given | `CURSOR_BLINK` (`?12h`, `5 q`) at the prompt, `CURSOR_DEFAULT` on exit | `test_cursor.py` |
| 16 | Provider showed in `/provider`, the key was accepted, `/v1/models` listed 84 ids — and **every request answered 404** (`Function … not found for account`) | Nothing ever proved a model answered. `/models` lists models the account is *not granted* (404) and retired ones stay listed (410), so a hand-written `model:` looked fine and was dead | `/key <paste>` writes the provider block, stores the secret in `.env`, and **proves a model with a real one-token completion** before activating; `/model refresh` re-scans and keeps only ids that answered; a failing turn now prints what the code actually means | `test_keycmd.py` + `test_key_tui.py` |
| 17 | `/key <oc_sk_…>` answered "can't tell which service that key is for" | the catalog had no OpenCode entry, so a Zen/Go key had no provider to land in | added `opencode` to the catalog — exact `oc_sk_`, weak `sk-`, free-tier model presets, listing never trusted | `test_keycmd.py` |
| 18 | A Cloudflare-fronted provider (OpenCode) listed **0 models**, then every call failed | urllib's default User-Agent is on Cloudflare's block list — 403 `error code: 1010` — and the client sent no User-Agent of its own | send `User-Agent: mahoraga/1.0` on every request | `test_baseurl.py` |
| 19 | A table's content was trimmed — a "Summary" column read `Routes incoming requests to b…` and the rest of the answer never showed | `_render_table` capped every column at 30 chars and ellipsized anything longer; a table wider than the pane had its columns crushed to 6 chars and could still overflow it | cells wrap inside their column instead of being cut; the 30-char cap and the `max(20, width)` floor are gone, and the shrink loop fits the table to the pane | `test_markdown.py` |

## Modal windows (pickers)

`/model` and `/provider` paint a list into the pane. Two rules keep it from
corrupting the screen, and both already had a bug behind them elsewhere:

1. **The render thread must not paint under a modal.** It repaints the pane
   every 80 ms whenever something marks it dirty (a tool result, a status
   change). `Screen.modal` makes `_loop` and `relayout` skip the pane while a
   picker owns it; the pane is repainted once on the way out.
2. **Paint absolutely, save/restore the cursor, and start every row with
   `CSI 2K`.** Clearing to end-of-line leaves the previous frame's band behind
   a plain row (row 5 in the table above — same failure, same fix).

The window maths (`window_slice`) is pure and tested in `test_picker.py`; the
key handling is only provable in a terminal, so `test_key_tui.py` drives the
real app with arrow keys.

## Testing a TUI change

Unit tests cannot see most of these. Reproduce with a pty:

```python
pid, master = pty.fork()          # pty.fork, NOT setsid: ^C needs a controlling tty
# child: os.execvpe(python, ["-m", "mahoraga"], env)
```

Then assert on the captured bytes: no stray `\x1b[` in rendered text, the expected
order of blocks, the wheel height in the paint log, the stream stopping early.
`pty.fork()` matters — with `os.setsid()` the child has no controlling terminal,
so `^C` is swallowed by the line discipline and no signal is delivered.

## Running everything

```bash
for t in tests/test_*.py; do printf '%-30s ' "$t"; python3 "$t" >/tmp/o 2>&1 \
  && echo PASS || { echo FAIL; tail -5 /tmp/o; }; done
```

19 files, all stdlib, no pytest.
