terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.70"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
  }

  # Remote state: create the state account out of band, then uncomment and run
  # `terraform init -migrate-state`. Auth is Entra ID, no storage keys.
  # backend "azurerm" {
  #   resource_group_name  = "xstore-tfstate"
  #   storage_account_name = "xstoretfstate"
  #   container_name       = "tfstate"
  #   key                  = "azure/terraform.tfstate"
  #   use_azuread_auth     = true
  # }
}
