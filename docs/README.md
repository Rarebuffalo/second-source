# Second Source documentation

## Get started

- [Quickstart](quickstart.md): add the Action to a repository, or check one pull request from
  the command line.
- [FAQ](faq.md): short answers to common questions.

## Use

- [GitHub Action reference](action.md): the workflow, inputs, permissions, events, the check
  run, commands, pinning, cost and limits.
- [CLI reference](cli.md): `second-source check`, `init` and `bench`, options, exit codes and
  JSON output.
- [Claims and verdicts](claims.md): what is checked, the four verdicts, integrity guards and
  every reason a report can give.
- [Troubleshooting](troubleshooting.md): a check that waits, shows no claims, or looks wrong.

## Understand

- [How it works](how-it-works.md): from a pull request to a verdict.
- [Accuracy](accuracy.md): how extraction is measured, the benchmark results and their limits.
- [Security model](security-model.md): what is trusted, what is not, and why the Action is safe
  on `pull_request_target`.

## Contribute

- [CONTRIBUTING.md](../CONTRIBUTING.md): set-up, how changes are made, releasing.
- [Architecture](ARCHITECTURE.md): layers, contracts and the engine.
- [Coding pack](packs/coding.md): claim types, extraction rules, the evidence contract and the
  complete list of contradiction paths.
- [GitHub source](sources/github.md): endpoints, commits, caching and errors.
- [Benchmark method](benchmark.md) and the [run 2 data](benchmark-data/run2/README.md).
