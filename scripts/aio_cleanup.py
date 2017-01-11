#!/usr/bin/env python

# This script deletes instances whose name starts with "jra" if they are in
# error state or older than 48hrs.

import datetime
import dateutil.parser
import os

from novaclient import client

nova = client.Client(
    2,
    username=os.environ['OS_USERNAME'],
    api_key=os.environ['OS_PASSWORD'],
    tenant_id=os.environ['OS_TENANT_NAME'],
    auth_url=os.environ['OS_AUTH_URL'],
    region_name="IAD"
)

for s in nova.servers.list():
    created = dateutil.parser.parse(s.created)
    old_threshold = datetime.datetime.now(
        created.tzinfo)-datetime.timedelta(hours=48)
    error = s.status == 'ERROR'
    old = created < old_threshold
    print("Instance:{name} status:{status} old:{old}".format(
        name=s.name,
        status=s.status,
        old=old
    ))
    if (s.name.startswith('jra') and (error or old)):
        print("Deleting {name} Error:{error} Old:{old}".format(
            name=s.name, error=error, old=old))
        s.delete()
