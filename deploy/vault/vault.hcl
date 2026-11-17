# Vault in SERVER mode with durable storage (docker-compose.vault.yml). Unlike `vault server -dev` it keeps its data on
# disk and starts SEALED: a person unseals it (see docs/pilot/PRODUCTION_HARDENING.md). The listener has no TLS because
# only the app and worker containers on the private Docker network reach it; put TLS in front of it (or use auto-unseal
# with a cloud KMS) before Vault is reachable from anywhere else.
ui            = false
disable_mlock = false

storage "file" {
  path = "/vault/file"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true
}

api_addr = "http://vault:8200"
