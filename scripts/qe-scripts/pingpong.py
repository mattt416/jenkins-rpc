#!/usr/bin/env python

import argparse
import threading
import subprocess


def args():
    """Setup argument Parsing."""
    parser = argparse.ArgumentParser(
        usage='%(prog)s',
        description='Internet Protocol Address ICMP Tester',
        epilog='Internet Protocol Address ICMP Tester Licensed "Apache 2.0"')

    parser.add_argument(
        '-f',
        '--file',
        help="File with list of IPs",
        required=True,
        default='hostname')

    parser.add_argument(
        '-c',
        '--count',
        help='Count of ICMP checks',
        required=False,
        type=int,
        default=None
    )

    parser.add_argument(
        '-p',
        '--path',
        help='Path to store log file(s)',
        required=False,
        default='/tmp/log'
    )

    return vars(parser.parse_args())


class pingPong(threading.Thread):
    def __init__(self, ipaddress, path, count=''):
        threading.Thread.__init__(self)
        self.ipaddress = ipaddress
        self.path = path + "/" + self.ipaddress + '.log'
        self.count = count

    def run(self):
        print "Running ping with count {0} against ipaddress: {1}".format(
            self.count, self.ipaddress)

        command = "ping "

        # Append count if set
        if self.count is not None:
            command += "-c {0} ".format(self.count)

        # Append ipaddress
        command += "{0}".format(self.ipaddress)

        # Run the ping
        try:
            f = open(self.path, "w")
            subprocess.call(command, stdout=f, shell=True)
            f.close()
        except (KeyboardInterrupt, SystemExit):
            raise


def main():
    """Run the main application."""

    # Parse user args
    user_args = args()

    with open(user_args['file'], 'r') as f:
        read_data = f.read().splitlines()
    f.closed

    for i in read_data:
        thread = pingPong(i, user_args['path'], user_args['count'])

        # Start new Threads
        thread.start()

if __name__ == "__main__":
    main()
