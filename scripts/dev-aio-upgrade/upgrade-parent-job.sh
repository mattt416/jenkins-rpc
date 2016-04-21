#!/bin/bash -x

rm -rf rpc-openstack
git clone --recursive $RPC_REPO

pushd rpc-openstack
  echo "UPGRADE_PATHS=$(../jenkins-rpc/scripts/dev-aio-upgrade/upgrade-branches.sh|tr '\n' ' ' )"\
    > ../upgrade.properties
popd
