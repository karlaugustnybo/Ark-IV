# CoreWeave pipeline tools

These optional tools plan bounded compute, stage authorized inputs, preserve
content-addressed artifacts, profile explicit fixtures, and restore verified
results. They use `cwsandbox[wandb]`, not Modal. The isolated UV project does not
change the application's dependencies.

Start with a local plan. Nothing in ordinary application checks starts compute,
loads credentials, queries a provider, uploads data, or rebuilds the global map.
The repository is a code release: [DATA_POLICY.md](../../DATA_POLICY.md) also
applies to private cloud transfers. A private project is not a redistribution
or transfer grant. Review the exact inputs and destination before using a
transfer or `--run` command.

- [Configuration and payloads](docs/CONFIGURATION.md): explicit reviewed files,
  hashes, source identities, prepared images, and commands.
- [Storage](docs/CLOUD_UPLOAD.md): resumable uploads and verified downloads.
- [Build and preflight](docs/FULL_RUN.md): full-plan contracts and failure limits.
- [Resource planning](docs/RESOURCE_RECOMMENDATION.md): measurements versus scenarios.
- [Transfer diagnostics](docs/STORAGE_SPEED_TEST.md) and [evidence](docs/FINDINGS.md).
- [Script index](scripts/README.md).

Run local pure tests with the main project's locked dependencies:

```sh
uv run --frozen python -m unittest discover -s tests -p 'test_cloud_tooling.py'
```

Use `uv run --project sandbox/coreweave --frozen python` for SDK commands; use
the main environment and `PYTHONPATH=.` for workers importing `ark_pipeline`.
Prepared remote images must include the locked main environment, the isolated
storage SDK environment where needed, and Tippecanoe for tile compilation.
Never infer that a successful code-only check proves a complete data build.

All payload manifests, commands containing private paths, receipts, logs,
profiles and generated results belong in ignored `results/` or an external
private directory. Supply credentials through the environment; no script reads
`.env`. Approved retired experiments and incident scripts are not runtime dependencies.
