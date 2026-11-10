terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm" }
    azuread = { source = "hashicorp/azuread" }
  }
}

variable "name" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "tenant_id" { type = string }
variable "filesystem_id" { type = string }
variable "storage_account_id" { type = string }
variable "tags" { type = map(string) }

variable "allowed_ip_ranges" {
  type = list(object({
    name     = string
    start_ip = string
    end_ip   = string
  }))
  default = []
}

data "azuread_client_config" "current" {}

# Entra group that administers the workspace and queries the lake with its
# own identity (serverless SQL passes the caller's token through to ADLS).
resource "azuread_group" "admins" {
  display_name     = "${var.name}-sql-admins"
  security_enabled = true
  owners           = [data.azuread_client_config.current.object_id]
  members          = [data.azuread_client_config.current.object_id]
}

# Entra-only auth: no SQL admin login or password exists. Only the built-in
# serverless SQL pool is used (no dedicated pool, no Spark pool).
resource "azurerm_synapse_workspace" "this" {
  name                                 = var.name
  resource_group_name                  = var.resource_group_name
  location                             = var.location
  storage_data_lake_gen2_filesystem_id = var.filesystem_id
  azuread_authentication_only          = true
  managed_virtual_network_enabled      = false
  public_network_access_enabled        = true
  tags                                 = var.tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_synapse_workspace_aad_admin" "this" {
  synapse_workspace_id = azurerm_synapse_workspace.this.id
  login                = azuread_group.admins.display_name
  object_id            = azuread_group.admins.object_id
  tenant_id            = var.tenant_id
}

resource "azurerm_synapse_firewall_rule" "allowed" {
  for_each             = { for r in var.allowed_ip_ranges : r.name => r }
  name                 = each.key
  synapse_workspace_id = azurerm_synapse_workspace.this.id
  start_ip_address     = each.value.start_ip
  end_ip_address       = each.value.end_ip
}

resource "azurerm_synapse_firewall_rule" "azure_services" {
  name                 = "AllowAllWindowsAzureIps"
  synapse_workspace_id = azurerm_synapse_workspace.this.id
  start_ip_address     = "0.0.0.0"
  end_ip_address       = "0.0.0.0"
}

# Workspace identity: required on the primary ADLS filesystem.
resource "azurerm_role_assignment" "workspace_lake" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_synapse_workspace.this.identity[0].principal_id
}

# Admins read Parquet / Delta files through Entra pass-through.
resource "azurerm_role_assignment" "admins_lake" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azuread_group.admins.object_id
}

output "workspace_id" {
  value = azurerm_synapse_workspace.this.id
}

output "serverless_sql_endpoint" {
  value = azurerm_synapse_workspace.this.connectivity_endpoints["sqlOnDemand"]
}

output "admins_group_object_id" {
  value = azuread_group.admins.object_id
}
