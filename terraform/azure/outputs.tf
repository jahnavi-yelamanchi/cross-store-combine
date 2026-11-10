output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "kafka_bootstrap_servers" {
  description = "Event Hubs Kafka endpoint for Debezium (SASL_SSL, OAUTHBEARER with Entra ID)."
  value       = "${module.eventhubs.namespace_fqdn}:9093"
}

output "schema_registry_group" {
  value = module.eventhubs.schema_group_name
}

output "cosmos_endpoint" {
  value = module.cosmos.endpoint
}

output "lake_dfs_endpoint" {
  value = module.adls.dfs_endpoint
}

output "lake_abfss_root" {
  description = "abfss:// root used by Synapse SQL and the adls_parquet store."
  value       = "abfss://${module.adls.filesystem_name}@${module.adls.storage_account_name}.dfs.core.windows.net"
}

output "synapse_serverless_sql_endpoint" {
  value = module.synapse.serverless_sql_endpoint
}

output "synapse_admins_group_object_id" {
  value = module.synapse.admins_group_object_id
}

output "function_app_name" {
  value = module.functions.function_app_name
}

output "reconciler_client_id" {
  description = "Set as azure.workload.identity/client-id on the reconciler service account."
  value       = module.identity.client_id
}

output "aks_oidc_issuer_url" {
  value = var.enable_aks ? module.aks[0].oidc_issuer_url : var.aks_oidc_issuer_url
}
