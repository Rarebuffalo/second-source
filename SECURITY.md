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

## Supported versions

Only the latest release on the `main` branch receives fixes while the project is pre-1.0.
