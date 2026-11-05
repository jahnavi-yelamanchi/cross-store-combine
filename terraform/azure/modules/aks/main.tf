terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm" }
  }
}

variable "name" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "tenant_id" { type = string }
variable "tags" { type = map(string) }

variable "node_vm_size" {
  type    = string
  default = "Standard_D2s_v5"
}

variable "node_count" {
  type    = number
  default = 1
}

variable "admin_group_object_ids" {
  type    = list(string)
  default = []
}

# Small cluster for the reconciler Helm chart (issue #48). OIDC issuer and
# workload identity let pods exchange service account tokens for Entra tokens.
resource "azurerm_kubernetes_cluster" "this" {
  name                      = var.name
  resource_group_name       = var.resource_group_name
  location                  = var.location
  dns_prefix                = var.name
  sku_tier                  = "Free"
  oidc_issuer_enabled       = true
  workload_identity_enabled = true
  local_account_disabled    = true
  automatic_upgrade_channel = "patch"
  node_os_upgrade_channel   = "NodeImage"
  tags                      = var.tags

  default_node_pool {
    name                        = "system"
    vm_size                     = var.node_vm_size
    node_count                  = var.node_count
    temporary_name_for_rotation = "systmp"
  }

  identity {
    type = "SystemAssigned"
  }

  azure_active_directory_role_based_access_control {
    tenant_id              = var.tenant_id
    azure_rbac_enabled     = true
    admin_group_object_ids = var.admin_group_object_ids
  }
}

output "oidc_issuer_url" {
  value = azurerm_kubernetes_cluster.this.oidc_issuer_url
}

output "cluster_id" {
  value = azurerm_kubernetes_cluster.this.id
}
