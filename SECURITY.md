# Security

## Supported versions

Only the current `main` branch is supported for security fixes.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability. Report it privately to
the repository owner using GitHub's private vulnerability reporting when it is
enabled, or by email if the repository publishes a security contact.

Include:

- the affected command or workflow
- steps to reproduce
- expected impact
- relevant platform details

## Scope

Relevant issues include unsafe file writes, path traversal, provenance bypasses
for generated images, dependency risks, and handling of user-supplied reference
images. Model output quality problems are usually product bugs rather than
security issues unless they enable one of those failure modes.

