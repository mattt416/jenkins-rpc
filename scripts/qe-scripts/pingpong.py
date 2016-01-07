__author__ = 'weez8031'
#ping function default interval is 1 sec, -c can be use for how many counts.

import subprocess, threading, getopt, sys


class pingPong(threading.Thread):
    def __init__(self, ip_address, path, count=''):
        threading.Thread.__init__(self)
        self.ip_address = ip_address
        self.path = path + "/" + self.ip_address + '.log'
        self.count = count

    def run(self):
        #print self.ip_address
        #print self.path
        #print self.count
        f = open(self.path, "w")
        subprocess.call("ping " + self.count + " " + self.ip_address + "| while read pong; do echo \"$(date): $pong\"; done", stdout=f, shell=True)

if __name__ == "__main__":
    count = ''
    path = '/var/log'
    try:
        opts, args = getopt.getopt(sys.argv[1:], 'c:p:h', ['count=', 'path=', 'help'])
    except getopt.GetoptError:
        sys.exit(2)
    for opt, arg in opts:
        if opt in ('-h', '--help'):
            "blablabla"
            sys.exit(2)
        elif opt in ('-c', '--count'):
            count = "-c " + arg
        elif opt in ('-p', '--path'):
            path = arg
        else:
            sys.exit(2)

    with open('hostname', 'r') as f:
        read_data = f.read().splitlines()
    f.closed
    poolSize = len(read_data)
    for i in read_data:
        thread = pingPong(i, path, count)
        #Start new Threads
        thread.start()

