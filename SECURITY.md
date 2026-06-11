# Security Policy

Egress is a research and demonstration project. It does not place real orders,
hold customer funds, or connect to live trading venues. Even so, we take the
handling of credentials and data seriously.

## Reporting a vulnerability

If you discover a security issue, please open a private report rather than a
public issue. Email the maintainers at the address listed in the repository
metadata, or use the platform's private vulnerability reporting if available.
Include steps to reproduce and the affected component (engine, agents, gateway,
MCP server, or frontend). We aim to acknowledge reports within a few business
days.

## Credentials and authentication

- Gemini is reached **only** through Vertex AI using Application Default
  Credentials. The build never uses a Google AI Studio `GOOGLE_API_KEY`. See
  `.env.example` and `CLAUDE.md` for the exact configuration.
- Never commit `.env`, service-account JSON keys, or Application Default
  Credentials. These paths are excluded in `.gitignore`.
- Secrets in deployed environments are provided through the platform's secret
  manager, not baked into images or source.

## Data handling

- The system uses historical, authorised third-party market and news data.
  Every data source and SDK is declared in the submission's data-sources field.
- No personal or customer trading data is ingested.

## Scope

The simulation engine is deterministic and runs locally without any cloud
dependency in its baseline mode, which keeps most development off the network
and off paid credits.
