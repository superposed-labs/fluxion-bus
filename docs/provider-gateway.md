# Provider Gateway

The Provider Gateway (`fluxion-provider`) exposes Fluxion's local agent executors (such as Claude, Codex, or Antigravity) as an API provider endpoint. This allows external tools and coding assistants (such as Codex) to delegate sub-agents or subtasks to your local Fluxion executors without extra API charges.

---

## Getting Started

### 1. Enable the Service

In the Fluxion macOS app, open **Preferences** $\rightarrow$ **Services**, and toggle on **Provider Gateway**.

* **Default Port**: `8787` (`http://127.0.0.1:8787`)
* **Security**: Listens only on local loopback (`127.0.0.1`) with auto-generated Bearer Token authentication (`data/provider.token`, mode `0600`).

---

## Client Integration

### Codex

Fluxion provides a dedicated CLI helper to configure Codex to route sub-agent tasks to Fluxion.

#### One-Click Installation
Run the following command in your terminal:

```bash
fluxion-provider install-codex-config
```

> **Note**: This command presents an interactive diff preview of changes to `~/.codex/config.toml` before saving, and automatically creates a backup file.

#### Inspect Configuration
To inspect the configuration block without modifying any files:

```bash
fluxion-provider print-codex-config
```

#### Diagnostic Check
To verify that your Provider Gateway service and token setup are healthy:

```bash
fluxion-provider doctor
```

#### One-Click Uninstall
To safely remove the Fluxion configuration block from `~/.codex/config.toml`:

```bash
fluxion-provider uninstall-codex-config
```

---

## Configuration & Environment Variables

The Provider Gateway can be customized via `.env` or environment variables:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `FLUXION_PROVIDER_ENABLED` | `false` | Enables/disables automatic launch of `fluxion-provider` |
| `FLUXION_PROVIDER_HOST` | `127.0.0.1` | Host IP to bind the gateway HTTP daemon |
| `FLUXION_PROVIDER_PORT` | `8787` | Port to bind the gateway HTTP daemon |
| `FLUXION_PROVIDER_TOKEN_FILE` | `data/provider.token` | Path to the Bearer auth token file |
| `FLUXION_PROVIDER_CONFIG_FILE` | `config/provider_routes.json` | Path to the routing and policy configuration file |
