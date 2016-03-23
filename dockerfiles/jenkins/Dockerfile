FROM ubuntu:14.04
MAINTAINER Evan Callicoat <apsu@propter.net>

# Make apt quiet
ENV DEBIAN_FRONTEND noninteractive

# Update and grab curl and make sure we can apt over https
RUN apt-get update
RUN apt-get install -y curl apt-transport-https ca-certificates

# Jenkins repo and key -- per jenkins installation page
RUN echo deb http://pkg.jenkins-ci.org/debian binary/ > /etc/apt/sources.list.d/jenkins.list
RUN curl -s http://pkg.jenkins-ci.org/debian/jenkins-ci.org.key | apt-key add -

# Docker repo and key -- per docker installation page
RUN echo deb https://apt.dockerproject.org/repo ubuntu-trusty main > /etc/apt/sources.list.d/docker.list
RUN  sudo apt-key adv --keyserver hkp://p80.pool.sks-keyservers.net:80 --recv-keys 58118E89F3A912897C070ADBF76221572C52609D

# Update again
RUN apt-get update

# Upgrade and install things
RUN apt-get upgrade -y
RUN apt-get install -y jenkins git ssh-client docker-engine build-essential libffi-dev python-dev libxslt1-dev libxml2 libxml2-dev

# install pip
RUN curl https://bootstrap.pypa.io/get-pip.py > get-pip.py
RUN python get-pip.py

# Install Requests
RUN pip install requests pyyaml virtualenv "ansible<2.0" jinja2 python-heatclient

# Use volume for Jenkins home
ENV JENKINS_HOME /opt/jenkins
VOLUME /opt/jenkins

# Fire up jenkins
CMD java -jar /opt/jenkins/jenkins.war
