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
        self.uuid_re = re.compile("([0-9a-zA-Z]+-){4}[0-9a-zA-Z]+")
        self.ip_re = re.compile("([0-9]+\.){3}[0-9]+")
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
        self.raw_branch = self.env_vars.get('ghprbTargetBranch', '')
        if self.raw_branch == '':
            self.raw_branch = self.env_vars.get('RPC_RELEASE', '')
        self.branch = self.raw_branch.replace('-', '_').replace('.', '_')
        self.commit = self.env_vars.get('ghprbActualCommit', '')
        if self.env_vars.get('DEPLOY_CEPH') == 'yes':
            self.btype = 'ceph'
        elif self.env_vars.get('HEAT_TEMPLATE', ''):
            self.btype = 'multinode'
        else:
            self.btype = 'full'
        self.get_parent_info()
        self.failures = set()
        if self.result != 'SUCCESS':
            self.get_failure_info()

    def normalise_failure(self, failure_string):
        """Remove identifiers from failures

        This prevents multiple incidents of the same failure being counted as
        multiple failures
        """

        failure_string = self.uuid_re.sub('** uuid removed **',
                                          failure_string)
        failure_string = self.ip_re.sub('** ip removed **',
                                        failure_string)
        return failure_string

    def add_failure(self, failure):
        self.failures.add(self.normalise_failure(failure))

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
        jenkins_base = "http://jenkins.propter.net/"
        self.trigger = "periodic"
        self.build_hierachy = []
        cause_elem = self.tree.xpath(
            '//causes | //causeBag/entry')[0].getchildren()[0]
        def normalise_job_name(name):
            # ensure that long names can be wrapped by inserting spaces
            return re.sub('([/=,.])','\\1 ', name)
        while True:
            cause_dict = {}
            tag = cause_elem.tag
            if tag == 'hudson.model.Cause_-UpstreamCause':
                cause_dict['name'] = normalise_job_name(
                    cause_elem.find('./upstreamProject').text)
                cause_dict['build_num'] = \
                    cause_elem.find('./upstreamBuild').text
                cause_dict['url'] = (
                    "{jenkins}/{job}/{build}".format(
                        jenkins=jenkins_base,
                        job=cause_elem.find('./upstreamUrl').text,
                        build=cause_dict['build_num']))
                self.build_hierachy.append(cause_dict)
            elif tag == 'org.jenkinsci.plugins.ghprb.GhprbCause':
                pullID = cause_elem.find('./pullID')
                cause_dict['name'] = "PR: {title}".format(
                    title=normalise_job_name(cause_elem.find('./title').text))
                cause_dict['build_num'] = pullID.text
                cause_dict['url'] = cause_elem.find('./url').text
                self.trigger = "pr"
                self.gh_pull = pullID.text
                self.gh_target = cause_elem.find('./targetBranch').text
                self.gh_title = cause_dict['name']
                self.build_hierachy.append(cause_dict)
            elif tag == 'hudson.triggers.TimerTrigger_-TimerTriggerCause':
                self.build_hierachy.append({
                    'name': 'TimerTrigger (Periodic)',
                    'build_num': '',
                    'url': '#'
                })
            elif tag == 'hudson.model.Cause_-UserIdCause':
                user = cause_elem.find('./userId').text
                self.trigger = "user"
                self.build_hierachy.append({
                    'name': 'Manual Trigger by {user}'.format(user=user),
                    'build_num': '',
                    'url': '{jenkins}user/{user}'.format(
                        jenkins=jenkins_base,
                        user=user),
                })
            else:
                self.build_hierachy.append({
                    'name': 'Unknown Trigger: {tag}'.format(
                        tag=normalise_job_name(tag)),
                    'build_num': '',
                    'url': '#'
                })

            # Go round again if the current cause has upstream causes
            upstream_causes = cause_elem.find('./upstreamCauses')
            if upstream_causes is not None:
                cause_elem = upstream_causes.getchildren()[0]
                continue

            # Otherwise found the root cause, exit loop.
            break

        # causes are collected from the AIO job working up to the root causes
        # reverse the list to have the root cause as the first item.
        self.build_hierachy.reverse()

        # Add currrent job to causes as its the last step in the hierachy
        self.build_hierachy.append(dict(
            name=self.job_name,
            build_num=self.build_num,
            url="{jenkins}/job/{job}/{build_num}".format(
                jenkins=jenkins_base,
                job=self.job_name,
                build_num=self.build_num)))

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
            self.tempest_test_fail(lines)
            self.tempest_exception(lines)
            self.cannot_find_role(lines)
            self.invalid_ansible_param(lines)
            self.jenkins_exception(lines)
            self.pip_cannot_find(lines)

            # Specific Failures
            self.service_unavailable(lines)
            self.rebase_fail(lines)
            self.rsync_fail(lines)
            self.elasticsearch_plugin_install(lines)
            self.tempest_filter_fail(lines)
            self.tempest_testlist_fail(lines)
            self.compile_fail(lines)
            self.apt_fail(lines)
            self.holland_fail(lines)
            self.slave_died(lines)

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
            self.add_failure("Unknown Failure")

    def holland_fail(self, lines):
        match_re = re.compile("HOLLAND_RC=1")
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                fail = lines[i-1]
                self.add_failure("Holland failure: {fail}".format(
                                 fail=fail))

    def pip_cannot_find(self, lines):
        match_re = re.compile("Could not find a version that satisfies "
                              "the requirement ([^ ]*)")
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                if not self.failure_ignored(i, lines):
                    self.add_failure("Can't find pip package: {fail}".format(
                                     fail=match.group(1)))

    def apt_fail(self, lines):
        match_re = re.compile(".: Failed to fetch (.*)")
        for line in lines:
            match = match_re.search(line)
            if match:
                self.add_failure("Apt Fetch Fail: {fail}".format(
                                 fail=match.group(1)))
                break

    def compile_fail(self, lines):
        match_re = re.compile("fatal error:(.*)")
        for line in lines:
            match = match_re.search(line)
            if match:
                self.add_failure("gcc fail: {fail}".format(
                                 fail=match.group(1)))

    def tempest_filter_fail(self, lines):
        match_re = re.compile("'Filter (.*) failed\.")
        for line in lines:
            match = match_re.search(line)
            if match:
                self.add_failure("Openstack Tempest Gate test "
                                 "set filter {fail} failed.".format(
                                     fail=match.group(1)))

    def tempest_testlist_fail(self, lines):
        match_re = re.compile("exit_msg 'Failed to generate test list'")
        for line in lines:
            match = match_re.search(line)
            if match:
                self.add_failure("Openstack Tempest Gate: "
                                 "failed to generate test list")

    def jenkins_exception(self, lines):
        match_re = re.compile("hudson\.[^ ]*Exception.*")
        for line in lines:
            match = match_re.search(line)
            if match:
                self.add_failure(match.group())

    def invalid_ansible_param(self, lines):
        match_re = re.compile("ERROR:.*is not a legal parameter in an "
                              "Ansible task or handler")
        for line in lines:
            match = match_re.search(line)
            if match:
                self.add_failure(match.group())

    def rate_limit(self, lines):
        match_re = re.compile("Rate limit has been reached.")
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                self.add_failure('Rate limit has been reached.')

    def archive_fail(self, lines):
        match_re = re.compile(
            "Build step 'Archive the artifacts' "
            "changed build result to FAILURE")
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                self.add_failure('Failed on archiving artifacts')

    def create_fail(self, lines):
        match_re = re.compile(
            'CREATE_FAILED  Resource CREATE failed:(?P<error>.*)$')
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                self.add_failure('Heat Resource Fail: {error}'.format(
                    error=match.groupdict()['error']))

    def ansible_task_fail(self, lines):
        match_re = re.compile('(fatal|failed):.*=>')
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                previous_task = self.get_previous_task(i, lines)
                if not self.failure_ignored(i, lines):
                    self.add_failure('Task Failed: {task}'.format(
                        task=previous_task))

    def setup_tools_sql_alchemy(self, lines):
        match_str = ("error in SQLAlchemy-Utils setup command: "
                     "'extras_require' must be a dictionary")
        for i, line in enumerate(lines):
            if match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.add_failure(
                    "Setup Tools / SQL Alchemy Fail. PrevTask: {task}".format(
                        task=previous_task))
                break

    def maas_alarm(self, lines):
        match_str = 'Checks and Alarms with failures:'
        for i, line in enumerate(lines):
            if match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.add_failure(
                    "Maas Alarm in alert state. PrevTask: {task}".format(
                        task=previous_task))
                break

    def dpkg_locked(self, lines):
        match_str = 'dpkg status database is locked by another process'
        alt_match_str = 'Could not get lock /var/lib/dpkg/lock'
        for i, line in enumerate(lines):
            if match_str in line or alt_match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.add_failure(
                    "dpkg locked. PrevTask: {task}".format(
                        task=previous_task))
                break

    def ceilometer_user_not_found(self, lines):
        match_str = 'user [ ceilometer ] was not found'
        for i, line in enumerate(lines):
            if match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.add_failure(
                    "user ceilometer not found. PrevTask: {task}".format(
                        task=previous_task))
                break

    def cannot_find_role(self, lines):
        match_str = 'cannot find role in'
        for i, line in enumerate(lines):
            if match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.add_failure(
                    "Cannot find role. PrevTask: {task}".format(
                        task=previous_task))
                break

    def secgroup_in_use(self, lines):
        match_re = re.compile('Security Group [^ ]* in use')
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                self.add_failure('Nova/Neutron Error: '
                                 'Security Group ... in use')
                break

    def tempest_test_fail(self, lines):
        match_re = re.compile('\{0\} (?P<test>tempest[^ ]*).*\.\.\. FAILED')
        for i, line in enumerate(lines):
            match = match_re.search(line)
            if match:
                test = match.groupdict()['test']
                self.add_failure('Tempest Test Failed: {test}'.format(
                    test=test))

    def tempest_exception(self, lines):
        exc_re = re.compile('tempest\.lib\.exceptions.*')
        class_re = re.compile("<class '([^']*)'>")
        details_re = re.compile("Details: (.*)$")
        for i, line in enumerate(lines):
            exc_match = exc_re.search(line)
            if exc_match:
                exc = exc_match.group(0)
                cls = ""
                details = ""
                for line in lines[i:i+5]:
                    details_match = details_re.search(line)
                    if details_match:
                        details = details_match.group(1)
                    class_match = class_re.search(line)
                    if class_match:
                        cls = class_match.group(1)
                failure_string = (
                    'Tempest Exception: tempest.lib.exceptions.'
                    '{exc} {details} {cls}'.format(exc=exc,
                                                   details=details,
                                                   cls=cls))
                self.add_failure(failure_string)

    def elasticsearch_plugin_install(self, lines):
        match_str = 'failed to download out of all possible locations...'
        for i, line in enumerate(lines):
            if match_str in line:
                previous_task = self.get_previous_task(i, lines)
                self.add_failure(
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
                self.add_failure("Unkown:" + " ".join(beforecontext))
                break

    def rsync_fail(self, lines):
        match_re = re.compile('failed:.*rsync -avzlHAX')
        for i, line in enumerate(lines):
            if match_re.search(line):
                previous_task = self.get_previous_task(i, lines)
                self.add_failure(
                    'Failure Running Rsync. PrevTask: {task}'.format(
                        task=previous_task))
                break

    def ssh_fail(self, lines):
        match_str = ("SSH Error: data could not be sent to the remote host. "
                     "Make sure this host can be reached over ssh")
        for line in lines:
            if match_str in line:
                self.add_failure(match_str.strip())
                break

    def rebase_fail(self, lines):
        match_str = "Rebase failed, quitting\n"
        try:
            lines.index(match_str)
            self.add_failure("Merge Conflict: " + match_str.strip())
        except ValueError:
            return

    def too_many_retries(self, lines):
        match_str = 'msg: Task failed as maximum retries was encountered'
        for i, line in enumerate(lines):
            if match_str in line and '...ignoring' not in lines[i+1]:
                previous_task = self.get_previous_task(i, lines)
                self.add_failure(
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
        self.add_failure(
            'Service Unavailable 503. PrevTask: {previous_task}'.format(
                previous_task=previous_task))

    def timeout(self, lines):
        match_re = ('Build timed out \(after [0-9]* minutes\). '
                    'Marking the build as aborted.')
        pattern = re.compile(match_re)
        for i, line in enumerate(lines):
            if pattern.search(line):
                previous_task = self.get_previous_task(i, lines)
                self.add_failure(
                    'Build Timeout: {previous_task}'.format(
                        previous_task=previous_task))
                break

    def apt_mirror_fail(self, lines):
        match_str = ("WARNING: The following packages cannot be "
                     "authenticated!\n")
        try:
            i = lines.index(match_str)
            previous_task = self.get_previous_task(i, lines)
            self.add_failure("Apt Mirror Fail: {line} {task}".format(
                line=match_str.strip(),
                task=previous_task))
        except ValueError:
            return

    def glance_504(self, lines):
        match_str = ("glanceclient.exc.HTTPException: 504 Gateway Time-out: "
                     "The server didn't respond in time. (HTTP N/A)\n")
        try:
            lines.index(match_str)
            self.add_failure("Cirros upload fail: " + match_str.strip())
        except ValueError:
            return

    def slave_died(self, lines):
        match_re = ('Agent went offline during the build')
        pattern = re.compile(match_re)
        for i, line in enumerate(lines):
            if pattern.search(line):
                previous_task = self.get_previous_task(i, lines)
                self.add_failure(
                    'Slave Died / Agent went offline during the build: '
                    '{previous_task}'.format(
                        previous_task=previous_task))
                break

    def __str__(self):
        s = ("{timestamp} {result} {job_name}/{build_num} --> "
             "{upstream_project}/{upstream_build_num}").format(
            timestamp=self.timestamp.isoformat(),
            job_name=self.job_name,
            build_num=self.build_num,
            result=self.result,
            upstream_project=self.upstream_project,
            upstream_build_num=self.upstream_build_num)
        if hasattr(self, 'gh_pull'):
            s += ' pr/{gh_pull} target:{gh_target} "{gh_title}"'.format(
                gh_pull=self.gh_pull,
                gh_target=self.gh_target,
                gh_title=self.gh_title)
        if self.failures:
            s += " " + ",".join(self.failures)
        return s
