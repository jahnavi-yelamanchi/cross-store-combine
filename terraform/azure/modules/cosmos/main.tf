terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm" }
  }
}

variable "name" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "tags" { type = map(string) }

variable "database_name" {
  type    = string
  default = "xstore"
}

# Containers from stores/mappings/cosmosdb.yaml plus the idempotency container
# (ADR-004) and the change feed processor lease container.
locals {
  containers = {
    customers         = "/id"
    batches           = "/id"
    "_xstore_applied" = "/id"
    leases            = "/id"
  }
}

# Serverless: pay per RU, no provisioned throughput to size. Keys are
# disabled; every data-plane caller needs a Cosmos SQL role assignment.
resource "azurerm_cosmosdb_account" "this" {
  name                               = var.name
  resource_group_name                = var.resource_group_name
  location                           = var.location
  offer_type                         = "Standard"
  kind                               = "GlobalDocumentDB"
  local_authentication_enabled       = false
  access_key_metadata_writes_enabled = false
  minimal_tls_version                = "Tls12"
  tags                               = var.tags

  capabilities {
    name = "EnableServerless"
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = var.location
    failover_priority = 0
  }
}

resource "azurerm_cosmosdb_sql_database" "this" {
  name                = var.database_name
  resource_group_name = var.resource_group_name
  account_name        = azurerm_cosmosdb_account.this.name
}

resource "azurerm_cosmosdb_sql_container" "this" {
  for_each              = local.containers
  name                  = each.key
  resource_group_name   = var.resource_group_name
  account_name          = azurerm_cosmosdb_account.this.name
  database_name         = azurerm_cosmosdb_sql_database.this.name
  partition_key_paths   = [each.value]
  partition_key_version = 2
}

output "account_id" {
  value = azurerm_cosmosdb_account.this.id
}

output "account_name" {
  value = azurerm_cosmosdb_account.this.name
}

output "endpoint" {
  value = azurerm_cosmosdb_account.this.endpoint
}

output "database_name" {
  value = azurerm_cosmosdb_sql_database.this.name
}

output "container_names" {
  value = keys(local.containers)
}
