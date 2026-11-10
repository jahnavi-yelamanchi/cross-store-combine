terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm" }
  }
}

variable "name" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "create_federated_credential" { type = bool }
variable "oidc_issuer_url" { type = string }
variable "service_account_subject" { type = string }
variable "cosmos_account_id" { type = string }
variable "cosmos_account_name" { type = string }
variable "lake_storage_account_id" { type = string }
variable "eventhub_namespace_id" { type = string }
variable "tags" { type = map(string) }

# Reconciler identity. Pods using the bound Kubernetes service account get
# Entra tokens through workload identity federation; no secrets exist.
resource "azurerm_user_assigned_identity" "reconciler" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
}

resource "azurerm_federated_identity_credential" "aks" {
  count                     = var.create_federated_credential ? 1 : 0
  name                      = "aks-reconciler"
  user_assigned_identity_id = azurerm_user_assigned_identity.reconciler.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = var.oidc_issuer_url
  subject                   = var.service_account_subject
}

# Cosmos DB Built-in Data Contributor (data plane).
resource "azurerm_cosmosdb_sql_role_assignment" "reconciler" {
  resource_group_name = var.resource_group_name
  account_name        = var.cosmos_account_name
  role_definition_id  = "${var.cosmos_account_id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = azurerm_user_assigned_identity.reconciler.principal_id
  scope               = var.cosmos_account_id
}

resource "azurerm_role_assignment" "lake" {
  scope                = var.lake_storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.reconciler.principal_id
}

resource "azurerm_role_assignment" "eventhubs" {
  for_each             = toset(["Azure Event Hubs Data Sender", "Azure Event Hubs Data Receiver"])
  scope                = var.eventhub_namespace_id
  role_definition_name = each.value
  principal_id         = azurerm_user_assigned_identity.reconciler.principal_id
}

output "client_id" {
  value = azurerm_user_assigned_identity.reconciler.client_id
}

output "principal_id" {
  value = azurerm_user_assigned_identity.reconciler.principal_id
}

output "identity_id" {
  value = azurerm_user_assigned_identity.reconciler.id
}
