# Stdlib import
import datetime
import re

# 3rd Party imports
# Imports with C deps
# remember to install all the apt xml stuff - not just the pip packages.
from lxml import etree


class Build(object):
    """Build Object

    Represents one RPC-AIO build. Contains functionality for intepreting
    the build.xml, injected_vars and log files.
    """
    def __init__(self, build_folder, job_name, build_num):
        self.tree = etree.parse('{bf}/build.xml'.format(
            bf=build_folder,
            job_name=job_name,
            build_num=build_num))
        self.result = self.tree.find('./result').text
        # jenkins uses miliseconds not seconds
        self.timestamp = datetime.datetime.fromtimestamp(
            float(self.tree.find('startTime').text)/1000)
        self.build_folder = build_folder
        self.job_name = job_name
        self.build_num = build_num
        self.env_file = '{build_folder}/injectedEnvVars.txt'.format(
            build_folder=self.build_folder)
        self.env_vars = self.read_env_file(self.env_file)
        self.branch = self.env_vars.get('ghprbTargetBranch', '')
        self.commit = self.env_vars.get('ghprbActualCommit', '')
        if self.env_vars.get('DEPLOY_CEPH') == 'yes':
            self.btype = 'ceph'
        elif self.env_vars.get('DEPLOY_MAAS') == 'yes':
            self.btype = 'maas'
        elif 'defcore' in self.env_vars.get('TEMPEST_TESTS', ''):
            self.btype = 'defcore'
        else:
            self.btype = 'full'
        self.get_parent_info()
        self.failures = set()
        if self.result != 'SUCCESS':
            self.get_failure_info()

    def read_env_file(self, path):
        kvs = {}
        with open(path) as env_file:
            for line in env_file:
                line = line.split('=')
                if len(line) < 2:
                    continue
                kvs[line[0].strip()] = line[1].strip()
        return kvs

    def get_parent_info(self):

        upstream_project = self.tree.find('.//upstreamProject')
        if upstream_project is not None:
            self.upstream_project = upstream_project.text
            self.upstream_build_no = self.tree.find('.//upstreamBuild').text
        else:
            self.upstream_project = ""
        self.trigger = "periodic"
        prinfo = self.tree.find('.//org.jenkinsci.plugins.ghprb.GhprbCause')
        if prinfo is not None:
            # build started by pr
            self.trigger = "pr"
            self.gh_pull = self.tree.find('.//pullID').text
            self.gh_target = self.tree.find('.//targetBranch').text
            self.gh_title = self.tree.find(
                './/org.jenkinsci.plugins.ghprb.GhprbCause/title').text

    def get_failure_info(self):
        def open_log(filename):
            log_file = '{build_folder}/{filename}'.format(
                build_folder=self.build_folder,
                filename=filename)
            lines = []
            try:
                with open(log_file, 'r') as f:
                    lines = f.readlines()
            except IOError:
                return []

            try:
                post_build = lines.index(
                    '[PostBuildScript] - Execution post build scripts.\n')
                return lines[0: post_build]
            except ValueError:
                return lines

        lines = []
        lines += open_log('log')
        lines += open_log('archive/artifacts/runcmd-bash.log')
        lines += open_log('archive/artifacts/deploy.sh.log')

        if self.result in ['ABORTED', 'FAILURE']:
            # Generic Failures
            self.timeout(lines)
            self.ssh_fail(lines)
            self.too_many_retries(lines)
            self.ansible_task_fail(lines)
            self.tempestfail(lines)
            self.cannot_find_role(lines)

            # Specific Failures
            self.service_unavailable(lines)
            self.rebase_fail(lines)
            self.rsync_fail(lines)
            self.elasticsearch_plugin_install(lines)
            self.portnotfound(lines)

            # Heat related failures
            self.create_fail(lines)
            self.archive_fail(lines)
            self.rate_limit(lines)

            # Disabled Failures
            # self.setup_tools_sql_alchemy(lines)
            # self.ceilometer_user_not_found(lines)
            # self.apt_mirror_fail(lines)
            # self.dpkg_locked(lines)
            # self.secgroup_in_use(lines)
            # self.maas_alarm(lines)
            # self.glance_504(lines)

            # if not self.failures:
            #    self.deploy_rc(lines)

        if not self.failures:
            self.failures.add("Unknown Failure")

    def rate_limit(self, lines):
        match_re = re.compile("Rate limit has been reached.")
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                self.failures.add('Rate limit has been reached.')

    def archive_fail(self, lines):
        match_re = re.compile(
            "Build step 'Archive the artifacts' "
            "changed build result to FAILURE")
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                self.failures.add('Failed on archiving artifacts')

    def create_fail(self, lines):
        match_re = re.compile(
            'CREATE_FAILED  Resource CREATE failed:(?P<error>.*)$')
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                self.failures.add('Heat Resource Fail: {error}'.format(
                    error=match.groupdict()['error']))

    def ansible_task_fail(self, lines):
        match_re = re.compile('(fatal|failed):.*=>')
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                previous_task = self.get_previous_task(i, lines)
                if not self.failure_ignored(i, lines):
                    self.failures.add('Task Failed: {task}'.format(
                        task=previous_task))

    def setup_tools_sql_alchemy(self, lines):
        match_str = ("error in SQLAlchemy-Utils setup command: "
                     "'extras_require' must be a dictionary")
        for i, line in enumerate(lines):
            if match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.failures.add(
                    "Setup Tools / SQL Alchemy Fail. PrevTask: {task}".format(
                        task=previous_task))
                break

    def maas_alarm(self, lines):
        match_str = 'Checks and Alarms with failures:'
        for i, line in enumerate(lines):
            if match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.failures.add(
                    "Maas Alarm in alert state. PrevTask: {task}".format(
                        task=previous_task))
                break

    def dpkg_locked(self, lines):
        match_str = 'dpkg status database is locked by another process'
        alt_match_str = 'Could not get lock /var/lib/dpkg/lock'
        for i, line in enumerate(lines):
            if match_str in line or alt_match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.failures.add(
                    "dpkg locked. PrevTask: {task}".format(
                        task=previous_task))
                break

    def ceilometer_user_not_found(self, lines):
        match_str = 'user [ ceilometer ] was not found'
        for i, line in enumerate(lines):
            if match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.failures.add(
                    "user ceilometer not found. PrevTask: {task}".format(
                        task=previous_task))
                break

    def cannot_find_role(self, lines):
        match_str = 'cannot find role in'
        for i, line in enumerate(lines):
            if match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.failures.add(
                    "Cannot find role. PrevTask: {task}".format(
                        task=previous_task))
                break

    def secgroup_in_use(self, lines):
        match_re = re.compile('Security Group [^ ]* in use')
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                self.failures.add('Nova/Neutron Error: '
                                  'Security Group ... in use')
                break

    def portnotfound(self, lines):
        match_re = re.compile('neutronclient.common.exceptions.'
                              'PortNotFoundClient')
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                self.failures.add('Nova/Neutron Exception: '
                                  'neutronclient.common.exceptions'
                                  '.PortNotFoundClient')
                break

    def tempestfail(self, lines):
        match_re = re.compile('\{0\} (?P<test>tempest[^ ]*).*\.\.\. FAILED')
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                test = match.groupdict()['test']
                self.failures.add('Tempest Test Failed: {test}'.format(
                    test=test))

    def elasticsearch_plugin_install(self, lines):
        match_str = 'failed to download out of all possible locations...'
        for i, line in enumerate(lines):
            if match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.failures.add(
                    "Elasticsearch Plugin Install Fail. "
                    "PrevTask: {task}".format(
                        task=previous_task))
                break

    def deploy_rc(self, lines):
        match_re = re.compile("DEPLOY_RC=[123456789]")
        remove_colour = re.compile('ha:[^ ]+AAA=+')
        for i, line in enumerate(lines):
            if match_re.search(line):
                beforecontext = lines[i-1:i-4:-1]
                for j, cline in enumerate(beforecontext):
                    beforecontext[j] = remove_colour.sub('', cline)
                self.failures.add("Unkown:" + " ".join(beforecontext))
                break

    def rsync_fail(self, lines):
        match_re = re.compile('failed:.*rsync -avzlHAX')
        for i, line in enumerate(lines):
            if match_re.search(line):
                previous_task = self.get_previous_task(i, lines)
                self.failures.add(
                    'Failure Running Rsync. PrevTask: {task}'.format(
                        task=previous_task))
                break

    def ssh_fail(self, lines):
        match_str = ("SSH Error: data could not be sent to the remote host. "
                     "Make sure this host can be reached over ssh")
        for line in lines:
            if match_str in line:
                self.failures.add(match_str.strip())
                break

    def rebase_fail(self, lines):
        match_str = "Rebase failed, quitting\n"
        try:
            lines.index(match_str)
            self.failures.add("Merge Conflict: " + match_str.strip())
        except ValueError:
            return

    def too_many_retries(self, lines):
        match_str = 'msg: Task failed as maximum retries was encountered'
        for i, line in enumerate(lines):
            if match_str in line and '...ignoring' not in lines[i+1]:
                previous_task = self.get_previous_task(i, lines)
                self.failures.add(
                    "Too many retries. PrevTask: {task}".format(
                        task=previous_task))

    def get_previous_task(self, line, lines, order=-1, get_line_num=False):
        previous_task_re = re.compile(
            'TASK: \[((?P<role>.*)\|)?(?P<task>.*)\]')
        previous_play_re = re.compile(
            'PLAY \[(?P<play>.*)\]')
        task_match = None
        play_match = None
        if order == -1:
            end = 0
        else:
            end = len(lines)
        for index in range(line, end, order):
            if not task_match:
                task_match = previous_task_re.search(lines[index])
            if task_match and get_line_num:
                return index
            play_match = previous_play_re.search(lines[index])
            if task_match and play_match:
                task_groups = task_match.groupdict()
                play_groups = play_match.groupdict()
                if 'role' in task_groups and task_groups['role']:
                    return '{play} / {role} / {task}'.format(
                        role=task_groups['role'],
                        play=play_groups['play'],
                        task=task_groups['task'])
                else:
                    return '{play} / {task}'.format(
                        play=play_groups['play'],
                        task=task_groups['task'])

        return ""

    def failure_ignored(self, fail_line, lines):
        next_task_line = self.get_previous_task(fail_line,
                                                lines,
                                                order=1,
                                                get_line_num=True)

        if next_task_line == "":
            return False

        for line in lines[fail_line:next_task_line]:
            if '...ignoring' in line:
                return True
        return False

    def service_unavailable(self, lines):
        match_str = ('ERROR: Service Unavailable (HTTP 503)')
        for i, line in enumerate(lines):
            if match_str in line:
                fail_line = i
                break
        else:
            # didn't find a match
            return
        previous_task = self.get_previous_task(fail_line, lines)
        self.failures.add(
            'Service Unavailable 503. PrevTask: {previous_task}'.format(
                previous_task=previous_task))

    def timeout(self, lines):
        match_str = ('Build timed out (after 20 minutes). '
                     'Marking the build as aborted.\n')
        try:
            timeout_line = lines.index(match_str)
        except ValueError:
            return
        previous_task = self.get_previous_task(timeout_line, lines)
        self.failures.add(
            'Inactivity Timeout: {previous_task}'.format(
                previous_task=previous_task))

    def apt_mirror_fail(self, lines):
        match_str = ("WARNING: The following packages cannot be "
                     "authenticated!\n")
        try:
            i = lines.index(match_str)
            previous_task = self.get_previous_task(i, lines)
            self.failures.add("Apt Mirror Fail: {line} {task}".format(
                line=match_str.strip(),
                task=previous_task))
        except ValueError:
            return

    def glance_504(self, lines):
        match_str = ("glanceclient.exc.HTTPException: 504 Gateway Time-out: "
                     "The server didn't respond in time. (HTTP N/A)\n")
        try:
            lines.index(match_str)
            self.failures.add("Cirros upload fail: " + match_str.strip())
        except ValueError:
            return

    def __str__(self):
        s = ("{timestamp} {result} {job_name}/{build_num} --> "
             "{upstream_project}/{upstream_build_no}").format(
            timestamp=self.timestamp.isoformat(),
            job_name=self.job_name,
            build_num=self.build_num,
            result=self.result,
            upstream_project=self.upstream_project,
            upstream_build_no=self.upstream_build_no)
        if hasattr(self, 'gh_pull'):
            s += ' pr/{gh_pull} target:{gh_target} "{gh_title}"'.format(
                gh_pull=self.gh_pull,
                gh_target=self.gh_target,
                gh_title=self.gh_title)
        if self.failures:
            s += " " + ",".join(self.failures)
        return s
