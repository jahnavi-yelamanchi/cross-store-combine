variable "location" {
  description = "Azure region for every resource."
  type        = string
  default     = "westeurope"
}

variable "prefix" {
  description = "Short lowercase name prefix."
  type        = string
  default     = "xstore"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,9}$", var.prefix))
    error_message = "prefix must be 3-10 lowercase alphanumerics."
  }
}

variable "suffix" {
  description = "Lowercase suffix that makes global names (storage, Cosmos, Event Hubs) unique."
  type        = string
  default     = "dev01"

  validation {
    condition     = can(regex("^[a-z0-9]{2,6}$", var.suffix))
    error_message = "suffix must be 2-6 lowercase alphanumerics."
  }
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = { project = "xstore", managed_by = "terraform" }
}

variable "lake_allowed_ip_ranges" {
  description = "Public CIDRs allowed through the ADLS firewall (operator IPs). Empty = Azure services only."
  type        = list(string)
  default     = []
}

variable "synapse_allowed_ip_ranges" {
  description = "Public CIDR ranges allowed to reach the Synapse serverless SQL endpoint, as start/end pairs."
  type = list(object({
    name     = string
    start_ip = string
    end_ip   = string
  }))
  default = []
}

variable "enable_aks" {
  description = "Create the AKS cluster that hosts the reconciler Helm chart."
  type        = bool
  default     = false
}

variable "aks_oidc_issuer_url" {
  description = "OIDC issuer of an existing cluster; used for the federated credential when enable_aks is false."
  type        = string
  default     = ""
}

variable "reconciler_namespace" {
  description = "Kubernetes namespace of the reconciler service account."
  type        = string
  default     = "xstore"
}

variable "reconciler_service_account" {
  description = "Kubernetes service account bound to the reconciler managed identity."
  type        = string
  default     = "xstore-reconciler"
}
