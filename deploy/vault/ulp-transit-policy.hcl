# What the application's token may do: only the per-user transit keys of PII (`user-<uuid>`), nothing else.
# `encrypt` has no "create": Transit would otherwise silently re-create a key that erasure destroyed.
path "transit/keys/user-*" {
  capabilities = ["create", "read", "update", "delete"]
}
path "transit/encrypt/user-*" {
  capabilities = ["update"]
}
path "transit/decrypt/user-*" {
  capabilities = ["update"]
}
