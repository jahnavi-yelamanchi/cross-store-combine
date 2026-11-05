terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm" }
  }
}

variable "name" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "tags" { type = map(string) }

variable "capacity" {
  description = "Throughput units (Standard)."
  type        = number
  default     = 1
}

variable "consumer_groups" {
  description = "Consumer groups on xstore.changes (Kafka consumer groups map to these)."
  type        = list(string)
  default     = ["reconciler", "detector", "lake-sink"]
}

# Standard tier exposes the Kafka endpoint (:9093) automatically; it is the
# Kafka-compatible target for Debezium. SAS keys are disabled: producers and
# consumers use Entra ID (OAUTHBEARER for Kafka clients).
resource "azurerm_eventhub_namespace" "this" {
  name                          = var.name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  sku                           = "Standard"
  capacity                      = var.capacity
  auto_inflate_enabled          = true
  maximum_throughput_units      = 4
  local_authentication_enabled  = false
  public_network_access_enabled = true
  minimum_tls_version           = "1.2"
  tags                          = var.tags
}

# Mirrors cdc/schema-registry/subjects.yaml.
resource "azurerm_eventhub" "changes" {
  name              = "xstore.changes"
  namespace_id      = azurerm_eventhub_namespace.this.id
  partition_count   = 6
  message_retention = 7
}

resource "azurerm_eventhub" "dlq" {
  name              = "xstore.cdc.dlq"
  namespace_id      = azurerm_eventhub_namespace.this.id
  partition_count   = 1
  message_retention = 7
}

# BlobCreated notifications from the lake (Event Grid -> Event Hubs).
resource "azurerm_eventhub" "lake_events" {
  name              = "xstore.lake.events"
  namespace_id      = azurerm_eventhub_namespace.this.id
  partition_count   = 2
  message_retention = 1
}

resource "azurerm_eventhub_consumer_group" "changes" {
  for_each            = toset(var.consumer_groups)
  name                = each.value
  namespace_name      = azurerm_eventhub_namespace.this.name
  eventhub_name       = azurerm_eventhub.changes.name
  resource_group_name = var.resource_group_name
}

resource "azurerm_eventhub_consumer_group" "dlq_triage" {
  name                = "dlq-triage"
  namespace_name      = azurerm_eventhub_namespace.this.name
  eventhub_name       = azurerm_eventhub.dlq.name
  resource_group_name = var.resource_group_name
}

resource "azurerm_eventhub_consumer_group" "lake_indexer" {
  name                = "lake-indexer"
  namespace_name      = azurerm_eventhub_namespace.this.name
  eventhub_name       = azurerm_eventhub.lake_events.name
  resource_group_name = var.resource_group_name
}

# Avro schema group; register cdc/schema-registry/change_event.avsc here.
resource "azurerm_eventhub_namespace_schema_group" "xstore" {
  name                 = "xstore"
  namespace_id         = azurerm_eventhub_namespace.this.id
  schema_compatibility = "Backward"
  schema_type          = "Avro"
}

output "namespace_id" {
  value = azurerm_eventhub_namespace.this.id
}

output "namespace_name" {
  value = azurerm_eventhub_namespace.this.name
}

output "namespace_fqdn" {
  value = "${azurerm_eventhub_namespace.this.name}.servicebus.windows.net"
}

output "changes_hub_name" {
  value = azurerm_eventhub.changes.name
}

output "dlq_hub_name" {
  value = azurerm_eventhub.dlq.name
}

output "lake_events_hub_id" {
  value = azurerm_eventhub.lake_events.id
}

output "schema_group_name" {
  value = azurerm_eventhub_namespace_schema_group.xstore.name
}
