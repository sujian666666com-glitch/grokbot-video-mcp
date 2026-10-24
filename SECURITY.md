# Security and data handling

This bridge exposes a small loopback HTTP service through a temporary Cloudflare HTTPS tunnel. Per-job random capability tokens authorize reference downloads and result uploads; the health endpoint returns no job data. Possession of a capability URL grants access until its expiry, so treat the entire URL as a secret.

- Only images explicitly supplied to a generation call are copied into a job and exposed to the Bot. There is no arbitrary filesystem download endpoint.
- Images pass through Cloudflare to the user's Grok Bot. Generated files return over the same transport. Use these services only for content you are authorized to share.
- The webhook sender key is stored in a local `0600` configuration file. Setup is a one-use loopback form, with an origin check and a ten-minute timeout.
- MCP clients run locally and may request local image reads or output writes. Connect trusted MCP clients only.
- Jobs, source images, videos, tokens and logs remain in the configured data directory. They are not automatically deleted. Capability expiry does not erase local files.
- Uploaded videos are untrusted files processed by the installed FFmpeg tools. Keep those tools current. This is a personal/development bridge, not a hardened multi-tenant upload service.
- Do not expose a general shell, relax the routine's URL checks, publish configuration, or disable provider safeguards to make a job succeed.

The repository excludes credentials, user media, account configuration backups, virtual environments and downloaded third-party executables. Before publishing your own fork, check that runtime state has not been force-added.

For a suspected vulnerability, use GitHub's private vulnerability reporting feature if it is enabled. Otherwise open an issue without credentials, capability URLs, personal media or exploit payloads and ask for a private reporting channel.
