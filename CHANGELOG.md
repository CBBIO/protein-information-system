# Changelog

This changelog starts at version 2.0.0.  
Previous history is not included.

## [3.2.0] - Unreleased

Errors in queue-based tasks are no longer silently lost: they are logged and
counted, can fail the run, and permanent errors no longer loop forever.

### Added
- `fail_on_task_errors` option (default `False`): raise `TaskFailuresError` at
  the end of `start()` when tasks were discarded or a worker process exited
  abnormally. With `False` the failures are logged as an error.
- `task_retry_delay` option (default `5` seconds) before a failed task is retried.
- `TaskFailuresError`, `QueueTaskInitializer.delete_task_queues()` and
  `is_task_queue()`.
- Summary warning with the number of tasks skipped because `process()` raised
  `ValueError`.

### Changed
- A computing task that fails is retried once after `task_retry_delay` and then
  discarded, instead of being requeued indefinitely. A message that was already
  redelivered (e.g. after a worker crash) is discarded on its first failure.
  Long outages of a remote service (UniProt, RCSB) now discard the affected
  tasks instead of retrying until the service returns.
- `delete_queues` deletes only the task's own queues instead of every queue on
  the broker. `delete_all_queues()` is unchanged.
- Queue monitoring considers only the task's own queues (exact names and
  `<task>_computing_*`), not every queue whose name contains the task's name.

### Fixed
- `start()` returned normally when post-processing failed, or when worker
  processes could not be started. Both errors are now raised; a post-processing
  error never masks an earlier pipeline error.
- With `GPUTaskInitializer`, a task that always failed was fetched again
  immediately and the pipeline never finished.
- Worker processes that crashed (e.g. killed by the OOM killer) went unnoticed;
  they are now reported as failures.
- Database connections use `client_encoding=utf8`, so clusters created under
  `LC_ALL=C` (`SQL_ASCII`) no longer fail on non-ASCII text.

### Known limitations
- `Structure3DiManager.store_entry()` still catches and logs per-record errors,
  so they are not counted.
- If every computing worker crashes, the computing queue never drains and the
  run waits for the database worker indefinitely.

Entries for 3.0.x and 3.1.x were not recorded in this file.

## [2.0.0] - 2025-07-28

### Added
- Full support for the **ESM3c** model for protein embeddings.
- Advanced task configuration options, including `batch_size`, model-specific activation.
- Integration of **Poetry** into the Docker workflow for improved environment reproducibility.
- `.dockerignore` file to optimize Docker image builds.

### Changed
- Embedding logic has been significantly improved:
  - **Removed forced truncation to 512 residues** across all models.
  - **Excluded special tokens** (e.g. CLS, PAD) from final residue embeddings, ensuring cleaner and biologically relevant representations.
  - Revised mean computation logic to reflect these changes.
- Unified parameter naming, such as `task_name` for ESM-based tasks, for better consistency.
- Refactored log directory handling: more robust directory creation and user path resolution.
- Minor formatting and whitespace improvements throughout the code.

### Removed
- Obsolete files.

### Fixed
- Improved stability in RabbitMQ connection handling to better tolerate failures.
