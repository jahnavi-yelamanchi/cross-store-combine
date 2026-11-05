data "azurerm_client_config" "current" {}

locals {
  name = "${var.prefix}-${var.suffix}"
  # Storage account names: 3-24 lowercase alphanumerics, globally unique.
  compact = "${var.prefix}${var.suffix}"
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = var.tags
}

module "eventhubs" {
  source              = "./modules/eventhubs"
  name                = "evhns-${local.name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = var.tags
}

module "cosmos" {
  source              = "./modules/cosmos"
  name                = "cosmos-${local.name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = var.tags
}

module "adls" {
  source              = "./modules/adls"
  name                = "st${local.compact}lake"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = var.tags
}

# Lake firewall: deny by default; trusted Azure services (Event Grid
# dead-lettering), the Synapse workspace as a resource instance, operator IPs.
resource "azurerm_storage_account_network_rules" "lake" {
  storage_account_id = module.adls.storage_account_id
  default_action     = "Deny"
  bypass             = ["AzureServices"]
  ip_rules           = var.lake_allowed_ip_ranges

  private_link_access {
    endpoint_resource_id = module.synapse.workspace_id
    endpoint_tenant_id   = data.azurerm_client_config.current.tenant_id
  }
}

module "synapse" {
  source              = "./modules/synapse"
  name                = "synw-${local.name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tenant_id           = data.azurerm_client_config.current.tenant_id
  filesystem_id       = module.adls.filesystem_id
  storage_account_id  = module.adls.storage_account_id
  allowed_ip_ranges   = var.synapse_allowed_ip_ranges
  tags                = var.tags
}

module "functions" {
  source                  = "./modules/functions"
  name                    = "func-${local.name}"
  storage_account_name    = "st${local.compact}func"
  resource_group_name     = azurerm_resource_group.this.name
  location                = var.location
  cosmos_account_id       = module.cosmos.account_id
  cosmos_account_name     = module.cosmos.account_name
  cosmos_endpoint         = module.cosmos.endpoint
  cosmos_database         = module.cosmos.database_name
  eventhub_namespace_id   = module.eventhubs.namespace_id
  eventhub_namespace_fqdn = module.eventhubs.namespace_fqdn
  changes_hub_name        = module.eventhubs.changes_hub_name
  tags                    = var.tags
}

module "eventgrid" {
  source               = "./modules/eventgrid"
  name                 = "evgt-${local.name}-lake"
  resource_group_name  = azurerm_resource_group.this.name
  location             = var.location
  storage_account_id   = module.adls.storage_account_id
  filesystem_name      = module.adls.filesystem_name
  deadletter_container = module.adls.deadletter_container_name
  eventhub_id          = module.eventhubs.lake_events_hub_id
  tags                 = var.tags
}

module "aks" {
  count               = var.enable_aks ? 1 : 0
  source              = "./modules/aks"
  name                = "aks-${local.name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tenant_id           = data.azurerm_client_config.current.tenant_id
  tags                = var.tags
}

module "identity" {
  source                      = "./modules/identity"
  name                        = "id-${local.name}-reconciler"
  resource_group_name         = azurerm_resource_group.this.name
  location                    = var.location
  create_federated_credential = var.enable_aks || var.aks_oidc_issuer_url != ""
  oidc_issuer_url             = var.enable_aks ? module.aks[0].oidc_issuer_url : var.aks_oidc_issuer_url
  service_account_subject     = "system:serviceaccount:${var.reconciler_namespace}:${var.reconciler_service_account}"
  cosmos_account_id           = module.cosmos.account_id
  cosmos_account_name         = module.cosmos.account_name
  lake_storage_account_id     = module.adls.storage_account_id
  eventhub_namespace_id       = module.eventhubs.namespace_id
  tags                        = var.tags
}
