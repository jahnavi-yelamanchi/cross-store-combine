terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm" }
  }
}

variable "name" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "storage_account_id" { type = string }
variable "filesystem_name" { type = string }
variable "deadletter_container" { type = string }
variable "eventhub_id" { type = string }
variable "tags" { type = map(string) }

# System topic on the ADLS account. Its managed identity delivers to Event
# Hubs and writes dead letters, so no keys or SAS are involved.
resource "azurerm_eventgrid_system_topic" "lake" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  source_resource_id  = var.storage_account_id
  topic_type          = "Microsoft.Storage.StorageAccounts"
  tags                = var.tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_role_assignment" "sender" {
  scope                = var.eventhub_id
  role_definition_name = "Azure Event Hubs Data Sender"
  principal_id         = azurerm_eventgrid_system_topic.lake.identity[0].principal_id
}

resource "azurerm_role_assignment" "deadletter" {
  scope                = "${var.storage_account_id}/blobServices/default/containers/${var.deadletter_container}"
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_eventgrid_system_topic.lake.identity[0].principal_id
}

# BlobCreated under lake/ -> xstore.lake.events. On HNS accounts a file is
# complete at FlushWithClose; PutBlob/PutBlockList cover blob-API writers.
resource "azurerm_eventgrid_system_topic_event_subscription" "lake_blob_created" {
  name                  = "lake-blob-created"
  system_topic          = azurerm_eventgrid_system_topic.lake.name
  resource_group_name   = var.resource_group_name
  event_delivery_schema = "CloudEventSchemaV1_0"
  eventhub_endpoint_id  = var.eventhub_id
  included_event_types  = ["Microsoft.Storage.BlobCreated"]

  subject_filter {
    subject_begins_with = "/blobServices/default/containers/${var.filesystem_name}/blobs/"
    subject_ends_with   = ".parquet"
  }

  advanced_filter {
    string_in {
      key    = "data.api"
      values = ["FlushWithClose", "PutBlob", "PutBlockList", "CopyBlob"]
    }
  }

  delivery_identity {
    type = "SystemAssigned"
  }

  dead_letter_identity {
    type = "SystemAssigned"
  }

  storage_blob_dead_letter_destination {
    storage_account_id          = var.storage_account_id
    storage_blob_container_name = var.deadletter_container
  }

  retry_policy {
    max_delivery_attempts = 10
    event_time_to_live    = 1440
  }

  depends_on = [
    azurerm_role_assignment.sender,
    azurerm_role_assignment.deadletter,
  ]
}

output "system_topic_id" {
  value = azurerm_eventgrid_system_topic.lake.id
}
