# Unmarked stale memory

These cases separate a marked revision from a decision that is merely outdated.

- **Marked predecessor:** `tests/test_storage.py::test_recall_flags_superseded_dependency_for_recheck` revises the dependency. Recall returns `needs_recheck` with `depends_on:superseded:<id>`.
- **Unmarked repository drift:** `tests/test_storage.py::test_recall_flags_changed_repository_dependency` stores `NOTES.md@<commit>` while the file says `fixture-old-port-1111`. After a clean commit changes that file to `fixture-current-port-2222`, recall returns `needs_recheck`. If Git cannot resolve the repository, the status is `unknown`.

The old decision remains visible. AMB does not infer staleness from prose and does not revise it automatically.
