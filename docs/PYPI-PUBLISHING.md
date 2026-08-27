# PyPI publishing

Agent Memory Bridge is prepared to publish Python distributions through GitHub Actions using PyPI Trusted Publishing (OIDC). No long-lived PyPI API token is required or expected.

## Security model

The release workflow is `.github/workflows/release.yml`.

- A merge to `main` does **not** publish a package.
- There is no manual `workflow_dispatch` publishing path.
- Publishing starts only when a GitHub Release is **published**.
- The build job has read-only repository permissions.
- The publish job runs separately, receives only `id-token: write`, and uses the GitHub `pypi` environment.
- The release tag must exactly match `v<project.version>` from `pyproject.toml`; otherwise the build fails before publication.
- Built distributions must pass `twine check` before they are handed to the publish job.

## One-time PyPI setup

Before the first package publication, configure a PyPI Trusted Publisher (a pending publisher can be used if the project does not exist on PyPI yet) with these values:

| Field | Value |
|---|---|
| PyPI project | `agent-memory-bridge` |
| GitHub owner | `zzhang82` |
| Repository | `Agent-Memory-Bridge` |
| Workflow | `release.yml` |
| Environment | `pypi` |

Create a GitHub Actions environment named `pypi`. For stronger protection, configure an environment protection rule requiring approval before deployment.

## First publication

Do not republish `v0.32.0`. Its existing release documentation explicitly states that there is no `pip install agent-memory-bridge==0.32.0` route, and historical release claims should remain true.

Use a new patch release (for example `v0.32.1`) for the first PyPI publication:

1. Update the package/source version and the repository's release identity documentation in the normal release PR.
2. Run the existing CI and release-contract checks.
3. Merge the release PR.
4. Create tag `v0.32.1` from the intended release commit.
5. Publish the GitHub Release for that tag.
6. The release workflow builds the sdist/wheel, verifies them, then publishes through Trusted Publishing.
7. Verify from a clean environment:

   ```bash
   python -m venv /tmp/amb-pypi-smoke
   /tmp/amb-pypi-smoke/bin/python -m pip install agent-memory-bridge==0.32.1
   /tmp/amb-pypi-smoke/bin/agent-memory-bridge --help
   ```

On Windows, use the equivalent virtual-environment Python and script paths.

## Failure behavior

Publishing fails closed if:

- the GitHub Release tag and package version disagree;
- package build fails;
- `twine check` fails;
- the `pypi` environment is not approved (when approval is configured);
- the Trusted Publisher identity does not match; or
- PyPI rejects the distribution.

A failed publication should be investigated rather than bypassed with a repository secret or manual token upload.
