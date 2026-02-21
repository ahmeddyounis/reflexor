# Secrets (refs only)

Reflexor treats secrets as **sensitive runtime inputs** that must never be persisted in run packets,
logs, or other audit artifacts. Instead, store and pass **references** to secrets.

## `SecretRef`

`reflexor.security.secrets.SecretRef` is a small Pydantic model that points to a secret managed
outside the system:

- `provider`: which secrets backend to use (e.g., `env`)
- `key`: a backend-specific identifier (for `env`, this is the environment variable name)
- `version`: optional hint for backends that support versioning/rotation

Because a `SecretRef` does **not** include the raw secret value, it is safe to serialize.

## `SecretsProvider`

`reflexor.security.secrets.SecretsProvider` defines a synchronous API:

```python
from reflexor.security.secrets import EnvSecretsProvider, SecretRef

provider = EnvSecretsProvider()
token = provider.resolve(SecretRef(provider="env", key="API_TOKEN"))
```

The returned `token` must be treated as sensitive: do not log it, do not store it, and never include
it in `RunPacket` payloads.

## `EnvSecretsProvider`

`EnvSecretsProvider` resolves secrets from environment variables:

```sh
export API_TOKEN="..."
```

```python
provider.resolve(SecretRef(provider="env", key="API_TOKEN"))
```

