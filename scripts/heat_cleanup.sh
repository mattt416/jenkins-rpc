#!/bin/bash

source /opt/jenkins/creds/mattt_jenkins_dfw
source /opt/jenkins/venvs/rpcheat/bin/activate

set -x

# Delete failed stacks
heat stack-list 2>/dev/null \
    |awk '/(DELETE|CREATE)_FAILED/{print $2}' \
    |while read stack; do heat stack-delete $stack; done

# Find stacks that are currently creating or complete
heat stack-list 2>/dev/null \
    |awk '$2~/-/ && $6~/CREATE_(COMPLETE|IN_PROGRESS)/{print $2}' \
    >stacks_to_keep

echo "*** Stacks to keep ***"
cat stacks_to_keep

# Find stacks that are referrenced by CBS volumes
cinder --os-volume-api-version 1 list 2>/dev/null\
    |awk '$6~/-/{sub(/-.*/,"",$6); t[$6]=1}; END{ for (k in t){ print k }}' \
    >stack_refs

echo "*** Stack refs ***"
cat stack_refs

# delete all volumes that don't reference a stack that should be kept.
while read stack_ref; do
    if ! grep -q $stack_ref stacks_to_keep; then
        cinder --os-volume-api-version 1 list 2>/dev/null\
            |awk '/'$stack_ref'/{print $2}' \
                |while read vol_id; do
                    cinder --os-volume-api-version 1 delete $vol_id 2>/dev/null
                done
    fi
done <stack_refs

# Delete all networks that are not associated with an active stack.

# I failed to get rackspace-novaclient to interface with rackspace networks.
# Shade won't either, so I resorted to using pyrax. However pyrax doesn't
# support orchestation, so I'm using shade for that.
python <<EOP
import os
import os_client_config
import pyrax
import shade

# Note that clouds.yaml and mattt_jenkins_dfw.pyrax contain the same creds
# but in different formats, and must be updated in unison.

# configure shade with creds from clouds.yaml
cloud_config = os_client_config.OpenStackConfig(
  config_files=['/opt/jenkins/creds/clouds.yaml']
).get_one_cloud('raxus', region_name='DFW')
osc = shade.OpenStackCloud(cloud_config=cloud_config)

# configure pyrax with creds from mattt_jenkins_dfw.pyrax
pyrax.set_setting('identity_type', 'rackspace')
pyrax.set_credential_file('/opt/jenkins/creds/mattt_jenkins_dfw.pyrax')
cnet = pyrax.connect_to_cloud_networks(region='DFW')

# get heat generated networks
networks = [x for x in cnet.list() if x.label.endswith('-rpc-network')]

# get stacks that are active (creating, created)
stacks = [x for x in osc.search_stacks()
          if x.stack_status in ['CREATE_COMPLETE', 'CREATE_IN_PROGRESS']]

# delete heat generated networks that don't belong to an active stack
for network in networks:
  stack_id_prefix = network.label.split('-')[0]
  for stack in stacks:
    if stack_id_prefix == stack.id.split('-')[0]:
      break
  else:
    print "Deleting network: {network}".format(network=network.label)
    cnet.delete(network)
EOP
