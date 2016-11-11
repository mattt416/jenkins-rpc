#!/usr/bin/env python

import datetime
import dateutil.parser
import os
import requests

from novaclient import client

nova = client.Client(
    2,
    username=os.environ['OS_USERNAME'],
    api_key=os.environ['OS_PASSWORD'],
    tenant_id=os.environ['OS_TENANT_NAME'],
    auth_url=os.environ['OS_AUTH_URL'],
    region_name="IAD"
)

jenkins_api_user = os.environ['JENKINS_API_USER']
jenkins_api_pass = os.environ['JENKINS_API_PASS']

slaves = requests.get("http://jenkins.propter.net/computer/api/json",
                      auth=(jenkins_api_user, jenkins_api_pass)).json()['computer']
slave_names = [x['displayName'] for x in slaves]
print("Jenkins Slaves: %s" %slave_names)
def jenkins_node(name):
  return name in slave_names


slaves=0
instances=0
for s in nova.servers.list():
    created = dateutil.parser.parse(s.created)
    sixhoursago = datetime.datetime.now(created.tzinfo)-datetime.timedelta(hours=6)
    comingofage = datetime.datetime.now(created.tzinfo)-datetime.timedelta(minutes=7)
    adult = created < comingofage
    error = s.status == 'ERROR'
    old = created < sixhoursago
    is_slave = jenkins_node(s.name)
    instances+=1
    if is_slave:
        slaves+=1
    print("Instance:{name} slave:{slave} status:{status} adult:{adult} old:{old}".format(
        name=s.name,
        slave=is_slave,
        status=s.status,
        adult=adult,
        old=old
    ))
    if (s.name.startswith('jrpcaio')
            and (
                s.status == 'ERROR'
                or ((not is_slave) and adult)
                or old
            )):
        print("Deleting %(name)s Error:%(error)s Old:%(old)s"
              % dict(name=s.name, error=error, old=old))
        s.delete()

print("Slaves: {numslaves}, Instances that aren't active slaves: {non_slave_instances}".format(
    numslaves=slaves,
    non_slave_instances=instances-slaves))
