# Security policy

## Reporting a vulnerability

Please report security issues **privately** through GitHub's private vulnerability reporting:
open the repository's **Security** tab and choose **Report a vulnerability**. Do not open a
public issue.

Include what you found, how to reproduce it, and the impact you expect. We aim to acknowledge
reports within 5 working days.

## What is in scope

Second Source reads untrusted text (pull request bodies, comments, commit messages) and talks
to the GitHub API with the user's token. We especially want to hear about:

- any way a token could be printed, logged, cached or sent anywhere other than GitHub;
- PR text that makes Second Source run code, read local files or make unexpected requests;
- crafted PR text or API responses that cause a false "contradicted" or "verified" verdict
  in a way a reviewer would not notice;
- dependency vulnerabilities that affect how Second Source is used.

## Known limits

- **Your own workflows can edit the check.** Every workflow in a repository acts as the same
  GitHub Actions app, so a workflow you run (or one a same-repository PR changes) can create or
  overwrite the "Second Source" check run. Fork PRs cannot on public repositories, because
  their token is read-only. If you make the check required, protect the branch, review changes
  to `.github/workflows/` (Second Source flags them with a caution), and treat the check as
  evidence for a reviewer, not as a lock.
- **Pinning.** `second-source init` uses the moving `@v0` tag, so fixes arrive without edits.
  To run exactly the code you reviewed, pin a full commit SHA instead
  (`uses: Rarebuffalo/second-source@<40-character sha>  # v0.5.0`) and update it on purpose.
- **Limits per PR.** To protect your repository's API quota from crafted PR text, at most 20
  issue references, 8 commits with CI claims and 4 with deploy claims are looked up (the PR
  body's first); claims past that are reported unverifiable.

## Supported versions

Only the latest release on the `main` branch receives fixes while the project is pre-1.0.
