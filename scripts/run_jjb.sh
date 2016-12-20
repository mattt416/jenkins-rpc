#!/bin/bash

set -e
set -x

. /opt/jenkins/venvs/jjb/bin/activate

set -u

# Read JENKINS_API_KEY
. /opt/jenkins/creds/jjb.creds

pushd rpc-jobs

# get operation
OPERATION=${1:-update}
[[ $# -gt 0 ]] && shift

# any remaining paramters are specific jobs to update, if none are specified
# all jobs will be updated

# Execute JJB
jenkins-jobs \
  --conf jenkins_jobs.ini \
  --password $JENKINS_API_KEY \
  $OPERATION \
  jobs.yaml \
  $@

