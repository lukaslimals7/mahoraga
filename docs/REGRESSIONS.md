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

13 files, all stdlib, no pytest.
