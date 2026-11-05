# Subscription and tenant come from ARM_SUBSCRIPTION_ID / ARM_TENANT_ID.
# Auth: Azure CLI locally, OIDC (ARM_USE_OIDC=true) in CI. No client secrets.
provider "azurerm" {
  features {}

  # Storage data-plane calls (filesystems, containers) use Entra ID, so the
  # lake account can run with shared keys disabled.
  storage_use_azuread = true
}

provider "azuread" {}
