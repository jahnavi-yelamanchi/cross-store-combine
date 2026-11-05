terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm" }
  }
}

variable "name" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "tags" { type = map(string) }

variable "filesystem_name" {
  type    = string
  default = "lake"
}

variable "cool_after_days" {
  type    = number
  default = 30
}

variable "deadletter_retention_days" {
  type    = number
  default = 30
}

# ADLS Gen2: StorageV2 with hierarchical namespace. Private by default: no
# public blobs, no shared keys (Entra ID only). The deny-by-default firewall
# lives in the root module (azurerm_storage_account_network_rules) because it
# names the Synapse workspace, which itself depends on this filesystem.
resource "azurerm_storage_account" "this" {
  name                            = var.name
  resource_group_name             = var.resource_group_name
  location                        = var.location
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  is_hns_enabled                  = true
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  tags                            = var.tags

  blob_properties {
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }
}

resource "azurerm_storage_data_lake_gen2_filesystem" "lake" {
  name               = var.filesystem_name
  storage_account_id = azurerm_storage_account.this.id
}

# Event Grid dead-letter destination.
resource "azurerm_storage_container" "deadletter" {
  name                  = "eventgrid-deadletter"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "this" {
  storage_account_id = azurerm_storage_account.this.id

  # Parquet deltas and Delta Lake files: cool after N days of no writes.
  rule {
    name    = "lake-cool"
    enabled = true
    filters {
      prefix_match = ["${var.filesystem_name}/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than = var.cool_after_days
      }
    }
  }

  rule {
    name    = "deadletter-expire"
    enabled = true
    filters {
      prefix_match = ["${azurerm_storage_container.deadletter.name}/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.deadletter_retention_days
      }
    }
  }
}

output "storage_account_id" {
  value = azurerm_storage_account.this.id
}

output "storage_account_name" {
  value = azurerm_storage_account.this.name
}

output "dfs_endpoint" {
  value = azurerm_storage_account.this.primary_dfs_endpoint
}

output "filesystem_id" {
  value = azurerm_storage_data_lake_gen2_filesystem.lake.id
}

output "filesystem_name" {
  value = azurerm_storage_data_lake_gen2_filesystem.lake.name
}

output "deadletter_container_name" {
  value = azurerm_storage_container.deadletter.name
}
