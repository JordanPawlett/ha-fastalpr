# Security Policy

Thank you for helping keep FastALPR for Home Assistant secure.

## Supported versions

Security fixes are considered for the current code on the default (`main`)
branch and the latest version distributed through HACS, together with the
current **FastALPR Inference** Home Assistant app. Older versions and
unmaintained forks are not guaranteed to receive security updates.

## Reporting a vulnerability

**Please do not disclose an unpatched vulnerability in a public GitHub issue,
discussion, pull request, or log.**

1. Prefer GitHub's [**Report a vulnerability**](https://github.com/JordanPawlett/ha-fastalpr/security/advisories/new)
   feature, which allows a private security advisory if private vulnerability
   reporting has been enabled for this repository.
2. If that option is unavailable, [open an issue](https://github.com/JordanPawlett/ha-fastalpr/issues/new)
   **asking the maintainer for a private reporting channel only**. Do not
   include exploit details, sensitive configurations, or proof-of-concept code
   in that public request.

A useful private report includes:

- The affected component (Home Assistant integration, inference app, or both)
  and the relevant version or commit.
- A clear description of the issue, potential impact, and reproduction steps.
- A minimal proof of concept, if safe to share, and any suggested mitigation.
- Whether the issue could expose recordings, licence plates, configuration
  secrets, or affect automations controlling physical devices.

**Do not include** real vehicle registrations, camera images, home addresses,
private network topology, authentication tokens, or other personal data.
Redact logs and configuration exports before sharing them.

Reports are assessed on a best-effort basis; there is no guaranteed response
or remediation timeline. Please allow time for investigation and a coordinated
fix before publishing technical details.

## Security-sensitive deployment guidance

- Run the **FastALPR Inference** app on a trusted Home Assistant network.
  Its internal HTTP API is not intended to be published to a LAN or the
  public internet, and should not be exposed through a reverse proxy.
- Keep Home Assistant, the FastALPR integration, and the inference app updated.
- Treat snapshots, OCR results, event history, and stored registrations as
  sensitive data. Apply appropriate access controls and retention policies.
- Number plates can be misread or copied. **Do not rely on licence plate
  recognition as the sole authorisation factor for opening gates, unlocking
  doors, or granting physical access.**

Security improvements and vulnerability fixes are welcome through the normal
pull-request process once disclosure can be handled safely.
