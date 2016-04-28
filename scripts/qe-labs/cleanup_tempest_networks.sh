#!/bin/bash

## Shell Opts ----------------------------------------------------------------
set -x

# Ensure python-neutronclient is installed
type neutron >/dev/null 2>&1 || { echo >&2 "python-neutronclient is not installed.  Aborting."; exit 1; }

# All tempest neutron networks
TEMPEST_NETWORKS="$(neutron net-list | grep tempest | awk '{print $2}')"

# All tempest subnets
TEMPEST_SUBNETS="$(neutron net-list | grep tempest | awk '{print $6}')"

# Remove all tempest ports
for SUBNET in $TEMPEST_SUBNETS; do
    for PORT in `neutron port-list | grep $SUBNET | awk '{print $2}'`; do 
        neutron port-delete $PORT;
    done
done

# Remove all tempest networks
for NET in $TEMPEST_NETWORKS; do neutron net-delete $NET; done

# print all neutron networks
neutron net-list
