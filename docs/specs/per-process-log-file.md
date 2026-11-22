# per-process-log-file — every process logs to its own file

Follow-up item 7 of the accuracy plan ("Windows log-rotation `PermissionError`").

`configure_logging()` runs on `import src`, so every process that imports the package opens the
same `logs/xbrlrag_app.log` through a `RotatingFileHandler`: the API server, the MCP server,
eval runs, ingest, scripts and pytest. Rotation renames the file, and on Windows a rename fails
while any other process holds the file open.

During the full run `eval-a42931beca30` on 2026-10-15:

- the file reached its 10 MB limit, and every later record failed with
  `PermissionError: [WinError 32]` (about 2,600 times);
- the run's file log was lost from 11:38 onward;
- only `.log` and `.log.5` existed.

The path is also relative to the current directory, so a process started elsewhere writes to a
different `logs/` folder.

## Requirement

1. **One file per process.** The file handler's filename is a template.
   `configs/logging.yaml` uses `logs/xbrlrag_{process}_{pid}.log`:
   - `{process}` is the entrypoint's name (`run_suite`, `uvicorn`, `ingest`, `pytest`, …);
   - `{pid}` is the process id.

   Each process rotates only its own file. A filename without placeholders keeps working as
   before.
2. **Anchored to the project root.** A relative filename resolves against `PROJECT_ROOT`, not
   the current directory.
3. **Pruning.**
   - At startup a process deletes the log files in that folder whose name matches
     `xbrlrag_*.log*` and that were last modified more than 7 days ago.
   - A file that cannot be deleted (open elsewhere) is skipped silently. Logging setup never
     fails because of pruning.
4. **`GET /api/logs`** tails the serving process's own log file, unless `XBRLRAG_LOG_FILE`
   names another.

## Acceptance criteria

- **C1**: `process_name(argv)` names the entrypoint from `sys.argv`:
  - start from the base name of `argv[0]` without its extension;
  - when that is `__main__`, use the name of its parent directory instead (`python -m pkg`);
  - when `argv` is empty or `argv[0]` is `-c`, `-m` or empty, use `python`;
  - lowercase the result, replace every run of characters outside `[a-z0-9_-]` with `_`, and
    strip `_` from both ends;
  - if nothing is left, use `python`.

  Point cases:
  - `scripts/eval/run_suite.py` gives `run_suite`;
  - `…\Scripts\uvicorn.exe` gives `uvicorn`;
  - `…/pytest/__main__.py` gives `pytest`;
  - `…/src/ingest/sec_edgar/ingest/__main__.py` gives `ingest`;
  - `["-c"]` and `[]` give `python`;
  - `C:/x/My Script!.py` gives `my_script`.

  Oracle: the definition.
- **C2**: `expand_log_filename(template, root, process, pid)` substitutes `{process}` and
  `{pid}`. It returns `root / result` for a relative result and the result itself for an
  absolute one. A template without placeholders is unchanged apart from that anchoring.

  Point cases:
  - `logs/xbrlrag_{process}_{pid}.log` with root `/r`, `run_suite` and 4242 gives
    `/r/logs/xbrlrag_run_suite_4242.log`;
  - `logs/xbrlrag_app.log` gives `/r/logs/xbrlrag_app.log`.

  Oracle: the definition.
- **C3**: `stale_log_files(entries, now, max_age_seconds, pattern)` returns, in input order, the
  paths among `(path, mtime)` entries whose file name matches the glob `pattern` (`fnmatch`)
  and for which `now - mtime > max_age_seconds`.

  Point case, with `now` = 1,000,000 s, 7 days = 604,800 s and pattern `xbrlrag_*.log*`:
  - an 8-day-old `xbrlrag_run_suite_1.log` is selected;
  - a 6-day-old `xbrlrag_uvicorn_2.log.1` is not;
  - an 8-day-old `other.log` is not.

  Oracle: the definition.
- **C4**: Properties:
  - `process_name` always returns a non-empty string matching `^[a-z0-9_-]+$`;
  - `expand_log_filename` gives distinct paths for distinct pids with a `{pid}` template;
  - `stale_log_files` is monotone: a larger `max_age_seconds` selects a subset;
  - it never selects a name that does not match the pattern.

  Oracle: the definitions.

## Interface

New functions (stubbed in the spec PR):

- `src.logging_config.process_name(argv: Sequence[str]) -> str`
- `src.logging_config.expand_log_filename(template: str, root: Path, process: str, pid: int) -> Path`
- `src.logging_config.stale_log_files(entries: Iterable[tuple[Path, float]], now: float, max_age_seconds: float, pattern: str) -> list[Path]`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- **`configure_logging`**
  - For each handler with a `filename`, it expands the name with `process_name(sys.argv)` and
    `os.getpid()`, anchored at `PROJECT_ROOT` (via `XBRLRAG_PROJECT_ROOT`, else the package
    root).
  - It creates the folder, then prunes `stale_log_files(...)` in that folder (7 days,
    `xbrlrag_*.log*`), ignoring `OSError`.
  - It records the resolved path. A new `current_log_file() -> Path | None` returns it.
- **`configs/logging.yaml`**: `filename: logs/xbrlrag_{process}_{pid}.log`.
- **`src/api/config.log_file_path()`**: returns `XBRLRAG_LOG_FILE` when set, else
  `current_log_file()`, else the old default.
- **Docs and Docker:** update the `xbrlrag_app.log` mentions in
  `docs/guides/xbrl-flow/01`, `07` and `08`, `src/api/README.md` and
  `infra/docker/Dockerfile`.

## Notes

- **Disk use** is bounded per process by the existing `maxBytes` × (`backupCount` + 1) = 60 MB,
  and in time by the 7-day pruning. pytest runs produce one small file each.
- **Containers** run one process each, so the file name only gains the entrypoint and pid.
  Their stdout/stderr console logging is unchanged.
