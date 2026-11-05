terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm" }
  }
}

variable "name" { type = string }
variable "storage_account_name" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "cosmos_account_id" { type = string }
variable "cosmos_account_name" { type = string }
variable "cosmos_endpoint" { type = string }
variable "cosmos_database" { type = string }
variable "eventhub_namespace_id" { type = string }
variable "eventhub_namespace_fqdn" { type = string }
variable "changes_hub_name" { type = string }
variable "tags" { type = map(string) }

# Host storage for the Flex Consumption app (deployment packages, triggers
# state). Keyless: the app reaches it with its system-assigned identity.
resource "azurerm_storage_account" "host" {
  name                            = var.storage_account_name
  resource_group_name             = var.resource_group_name
  location                        = var.location
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  tags                            = var.tags
}

resource "azurerm_storage_container" "deployments" {
  name                  = "deployments"
  storage_account_id    = azurerm_storage_account.host.id
  container_access_type = "private"
}

resource "azurerm_service_plan" "this" {
  name                = "asp-${var.name}"
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = "FC1"
  tags                = var.tags
}

# Python 3.12 app in terraform/azure/functions/cosmos_change_feed: Cosmos DB
# change feed trigger -> xstore.cdc.cosmos_change_feed -> Event Hubs output.
resource "azurerm_function_app_flex_consumption" "this" {
  name                        = var.name
  resource_group_name         = var.resource_group_name
  location                    = var.location
  service_plan_id             = azurerm_service_plan.this.id
  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.host.primary_blob_endpoint}${azurerm_storage_container.deployments.name}"
  storage_authentication_type = "SystemAssignedIdentity"
  runtime_name                = "python"
  runtime_version             = "3.12"
  maximum_instance_count      = 40
  instance_memory_in_mb       = 2048
  https_only                  = true
  tags                        = var.tags

  identity {
    type = "SystemAssigned"
  }

  # Identity-based binding connections: `<name>__<property>` settings, no keys.
  app_settings = {
    AzureWebJobsStorage__accountName            = azurerm_storage_account.host.name
    CosmosDbConnection__accountEndpoint         = var.cosmos_endpoint
    EventHubConnection__fullyQualifiedNamespace = var.eventhub_namespace_fqdn
    XSTORE_COSMOS_DATABASE                      = var.cosmos_database
    XSTORE_CHANGES_HUB                          = var.changes_hub_name
  }

  site_config {
    minimum_tls_version = "1.2"
  }
}

locals {
  principal_id = azurerm_function_app_flex_consumption.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "host_storage" {
  scope                = azurerm_storage_account.host.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = local.principal_id
}

resource "azurerm_role_assignment" "eventhubs_sender" {
  scope                = var.eventhub_namespace_id
  role_definition_name = "Azure Event Hubs Data Sender"
  principal_id         = local.principal_id
}

# Change feed processor reads the containers and writes leases.
resource "azurerm_cosmosdb_sql_role_assignment" "function" {
  resource_group_name = var.resource_group_name
  account_name        = var.cosmos_account_name
  role_definition_id  = "${var.cosmos_account_id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = local.principal_id
  scope               = var.cosmos_account_id
}

output "function_app_name" {
  value = azurerm_function_app_flex_consumption.this.name
}

output "principal_id" {
  value = local.principal_id
}
