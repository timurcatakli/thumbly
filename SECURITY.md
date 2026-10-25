# Security Policy

Thumbly controls a real iPhone, so we treat security reports as a priority.

## Supported versions

Only the latest [GitHub Release](https://github.com/timurcatakli/thumbly/releases) receives
security fixes. Update Thumbly to the latest release before you report a problem.

## Reporting a vulnerability

**Do not open a public issue, discussion, or pull request for a security problem.**

Report it privately through GitHub security advisories for
[timurcatakli/thumbly](https://github.com/timurcatakli/thumbly/security/advisories):
[Report a vulnerability](https://github.com/timurcatakli/thumbly/security/advisories/new).

Include:

- The affected version or commit.
- The component: Thumbly service (`apps/device`), Mac app (`apps/mac`), Python SDK
  (`packages/sdk-python`), or installer (`install.sh`).
- Steps to reproduce, and what an attacker gains.

Do not include real credentials, device identifiers, or screenshots of private data. Use
placeholders.

We aim to acknowledge a report within 3 business days and to agree on a disclosure date with
you. We credit reporters in the advisory unless you ask us not to.

## Scope

In scope are weaknesses that break the owner trust boundary, for example:

- Control of the phone, or of the owner API the Thumbly app uses, without owner approval.
- Bypassing owner binding, the loopback-only owner API, control leases, or explicit pairing.
- Reading the phone screen, clipboard, or typed content without authorization.
- Remote code execution on the Mac through Thumbly, or tampering with a release during install or update.

Out of scope:

- Attacks that need the owner's unlocked Mac account, admin rights on the Mac, or an unlocked
  phone in the attacker's hands.
- Findings in third-party dependencies with no demonstrated impact on Thumbly. Report those
  upstream.
- Denial of service from a device on the same LAN.
