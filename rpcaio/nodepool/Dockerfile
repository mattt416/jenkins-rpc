FROM ubuntu:14.04
MAINTAINER Hugh Saunders <hugh@wherenow.org>

# Make apt quiet
ENV DEBIAN_FRONTEND noninteractive

# Update and grab curl and make sure we can apt over https
RUN apt-get update
RUN apt-get install -y curl apt-transport-https

# Docker repo and key -- per docker installation page
RUN echo deb https://get.docker.io/ubuntu docker main > /etc/apt/sources.list.d/docker.list
RUN apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys 36A1D7869245C8950F966E92D8576A8BA88D21E9

# Update again
RUN apt-get update

# Upgrade and install things
RUN apt-get upgrade -y
RUN apt-get install -y git ssh-client python build-essential python-dev mysql-client mysql-server libmysqlclient-dev

# install pip
RUN curl https://bootstrap.pypa.io/get-pip.py > get-pip.py
RUN python get-pip.py

# Install pip packages
RUN pip install nodepool mysql-python

# Use volume for Jenkins home
ENV JENKINS_HOME /opt/jenkins
VOLUME /opt/jenkins

# point nodepool at its creds
env OS_CLIENT_CONFIG_FILE /opt/jenkins/creds/clouds.yaml

# Fire up nodepool
CMD nodepoold -d -c /opt/jenkins/jenkins-rpc/rpcaio/nodepool/nodepool.yml -s /opt/jenkins/creds/nodepoolsecure.ini 
