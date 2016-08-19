#!/bin/bash

set -e
set -x

. /opt/jenkins/venvs/jjb/bin/activate

set -u

# Read JENKINS_API_KEY
. /opt/jenkins/creds/jjb.creds

pushd rpc-jobs

OPERATION=${1:-update}

# Execute JJB
jenkins-jobs \
  --conf jenkins_jobs.ini \
  --password $JENKINS_API_KEY \
  $OPERATION \
  jobs.yaml

