# Stdlib import
import datetime
import re
import sys

# 3rd Party imports
# Imports with C deps
# remember to install all the apt xml stuff - not just the pip packages.
from lxml import etree


class FilterException(Exception):
    pass


class Build(object):
    """Build Object

    Represents one RPC-AIO build. Contains functionality for intepreting
    the build.xml, injected_vars and log files.
    """
    def __init__(self, build_folder, job_name, build_num):
        self.build_start = datetime.datetime.now()
        self.stdlib_path_re = re.compile(
            "/usr/lib/python[0-9]*.[0-9]*/[^ /]*\.pyc?")
        self.uuid_re = re.compile("([0-9a-zA-Z]+-){4}[0-9a-zA-Z]+")
        self.ip_re = re.compile("([0-9]+\.){3}[0-9]+")
        self.colour_code_re = re.compile('(\[8mha:.*)?\[0m')
        self.maas_tx_id_re = re.compile('\S*k1k.me\S*')
        self.maas_entity_uri_re = re.compile(
            '/\d+/entities(/[^/]*)?|/\d+/agent_tokens')
        self.maas_httpd_tx_id_re = re.compile("'httpdTxnId': '[^']*'")
        self.ansible_tmp_re = re.compile('ansible-tmp-[^/]*')
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

        normalisers = [
            (self.uuid_re, '**UUID**'),
            (self.ip_re, '**IPv4**'),
            (self.colour_code_re, ''),
            (self.maas_tx_id_re, '**TX_ID**'),
            (self.maas_entity_uri_re, '**Entity**'),
            (self.maas_httpd_tx_id_re,
                '**HTTP_TX_ID**'),
            (self.ansible_tmp_re, 'ansible-tmp-**Removed**')
        ]

        for pattern, sub in normalisers:
            failure_string = pattern.sub(sub, failure_string)

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
        cause_elems = self.tree.xpath('//causes | //upstreamCauses')
        for cause_elem in reversed(cause_elems):
            cause_dict = {}
            upstream_project = cause_elem.find('.//upstreamProject')
            if upstream_project is not None:
                cause_dict['name'] = upstream_project.text
                cause_dict['build_num'] = \
                    cause_elem.find('.//upstreamBuild').text
                cause_dict['url'] = (
                    "{jenkins}/{job}/{build}".format(
                        jenkins=jenkins_base,
                        job=cause_elem.find('.//upstreamUrl').text,
                        build=cause_dict['build_num']))
                if cause_dict:
                    self.build_hierachy.append(cause_dict)
                    continue

            pullID = cause_elem.find('.//pullID')
            if pullID is not None:
                cause_dict['name'] = "PR: {title}".format(
                    title=cause_elem.find('.//title').text)
                cause_dict['build_num'] = pullID.text
                cause_dict['url'] = cause_elem.find('.//url').text
                self.trigger = "pr"
                self.gh_pull = pullID.text
                self.gh_target = cause_elem.find('.//targetBranch').text
                self.gh_title = cause_dict['name']
                if cause_dict:
                    self.build_hierachy.append(cause_dict)

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

        filters = [
            # Generic Failures
            self.timeout,
            self.ssh_fail,
            self.too_many_retries,
            self.ansible_task_fail,
            self.tempest_test_fail,
            self.traceback,
            self.cannot_find_role,
            self.invalid_ansible_param,
            self.jenkins_exception,
            self.pip_cannot_find,

            # Specific Failures
            self.service_unavailable,
            self.rebase_fail,
            self.rsync_fail,
            self.elasticsearch_plugin_install,
            self.tempest_filter_fail,
            self.tempest_testlist_fail,
            self.compile_fail,
            self.apt_fail,
            self.holland_fail,
            self.slave_died,
            self.cirros_dhcp,
            self.cirros_sshd,

            # Heat related failures
            self.create_fail,
            self.archive_fail,
            self.rate_limit
        ]
        timings = {}
        if self.result in ['ABORTED', 'FAILURE']:
            for lfilter in filters:
                start_time = datetime.datetime.now()
                lfilter(lines)
                end_time = datetime.datetime.now()
                delta = end_time - start_time
                timings[lfilter] = delta
        fail_end = datetime.datetime.now()
        total_duration = fail_end - self.build_start
        if total_duration > datetime.timedelta(seconds=5):
            sys.stderr.write("Slow Job. {total_duration}\n".format(
                total_duration=total_duration))
            for lfilter, duration in timings.items():
                if duration > datetime.timedelta(seconds=1):
                    sys.stderr.write("    {lfilter}: {duration}\n".format(
                        lfilter=lfilter.func_name,
                        duration=duration
                    ))

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

    def traceback(self, lines):
        start_re = re.compile(
            r'^(?P<prefix>.*)Traceback \(most recent call last\)')
        exc_re = re.compile(r'^\S')
        MAX_TB_LINES = 100

        def normalise(line, prefix):
            # prefixes may contain re specials such as [
            # which need to be escaped.
            escaped_prefix = re.escape(prefix)
            # prefixes may also contain timestamps that need to be generalised
            timestamped_prefix = re.sub('\d+', '\d+', escaped_prefix)
            return re.sub('^' + timestamped_prefix, '', line)

        skip_count = 0
        for i, line in enumerate(lines):
            if skip_count > 0:
                skip_count -= 1
                continue
            match = start_re.search(line)
            if not match or self.failure_ignored(i, lines):
                continue
            groups = match.groupdict()
            prefix = groups['prefix']
            # This inner loop is for reading each frame of the stack trace
            j = 0
            while True:
                j += 1
                line = normalise(lines[i+j], prefix)
                # Hard to find the last line of a Traceback
                # it may not even contain a :
                exc_match = exc_re.match(line)
                if exc_match:
                    exc_type, _, exc_msg = line.partition(': ')
                    skip_count = j
                    break
                elif j > MAX_TB_LINES:
                    raise FilterException("Failed to find end of trace"
                                          " {job}_{build}:{l}"
                                          .format(
                                              job=self.job_name,
                                              build=self.build_num,
                                              l=i))
                else:
                    continue

            prev = self.get_previous_task(i, lines)
            failure_string = (
                "Traceback. {exc_type}: {exc_msg} Previous Task {prev}"
                .format(
                    exc_type=exc_type.strip(),
                    exc_msg=exc_msg.strip(),
                    prev=prev
                )
            )
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
                # If we match the last task to be executed
                # chances are the failure happened post-ansible,
                # so the last task indicator isn't that useful.
                if (task_groups['task'].strip()
                        == 'Deploy RPC HAProxy configuration files'):
                    return 'N/A'
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

    def cirros_dhcp(self, lines):
        match_re = ('No lease, failing')
        pattern = re.compile(match_re)
        for i, line in enumerate(lines):
            if pattern.search(line):
                self.add_failure('Cirros DHCP address acquisition fail')
                break

    def cirros_sshd(self, lines):
        match_re = ('Starting dropbear sshd: FAIL')
        pattern = re.compile(match_re)
        for i, line in enumerate(lines):
            if pattern.search(line):
                self.add_failure('Cirros SSHd failed to start')
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
