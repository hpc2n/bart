#
# Slurm accounting log parsing -> UR module
#
# Module for the  SGAS Batch system Reporting Tool (BaRT).
#
# Author: Andreas Engelbredt Dalsgaard <andreas.dalsgaard@gmail.com>
# Author: Magnus Jonsson <magnus@hpc2n.umu.se>
# Copyright: Nordic Data Grid Facility (2010)

import os
import time
import datetime
import dateutil.parser
import logging
import subprocess
import sys
import re

from bart import config, common
from bart.usagerecord import usagerecord
from pwd import getpwuid

SECTION = 'slurm'

STATEFILE = 'statefile'
DEFAULT_STATEFILE = SECTION + '.state'

STATEFILE_DEFAULT = 'statefile_default'
DEFAULT_STATEFILE_DEFAULT = 2419200

IDTIMESTAMP = 'idtimestamp'
DEFAULT_IDTIMESTAMP = 'true'

MAX_DAYS = 'max_days'
MAX_DAYS_DEFAULT = 7

BILLING_UNIT         = 'billing_unit'
DEFAULT_BILLING_UNIT = 'cpu'

CONFIG = {
            STATEFILE:         { 'required': False },
            STATEFILE_DEFAULT: { 'required': False, type: 'int' },
            IDTIMESTAMP:       { 'required': False, type: 'bool' },
            MAX_DAYS:          { 'required': False, type: 'int' },
            BILLING_UNIT:      { 'required': False },
          }

COMMAND = 'sacct --allusers --duplicates --parsable2 --format=JobIDRaw,UID,Partition,Submit,Start,End,Account,Elapsed,UserCPU,AllocTRES,Nodelist,NNodes --state=%s --starttime="%s" --endtime="%s"'

def exec_cmd(cmd):
    """
    Execute the shell command 'cmd', and return the output
    """

    logging.debug("Executing command '%s'" % cmd)

    # subprocess can be more than 10x times slower than popen in python2.4
    if sys.version_info < (2, 5):
        process = os.popen(cmd)
        return process.readlines()
    else:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
        data, _ = process.communicate()
        return data.strip().split('\n')


def versioncmp(a, b):
    """
    return -1 if a < b, 0 if a = b, and +1 if a > b, where a and b are
    version number strings of the format "A.B.C[-D]" and A, B, C, D are numbers
    """

    aa = [ int(x) for x in re.findall(r"\d+", a) ]
    bb = [ int(x) for x in re.findall(r"\d+", b) ]

    for i in range(min(len(aa), len(bb))):
        if aa[i] < bb[i]:
            return -1
        elif aa[i] > bb[i]:
            return 1

    ## If we get here, all common components are equal. Decide by the number of components:
    return cmp(a_length, b_length)


class SlurmBackend:
    """
    DB backend for slurm accounting.
    """
    def __init__(self, state_starttime, max_days):

        self.end_str = datetime.datetime.now().isoformat().split('.')[0]
        # Check if number of days since last run is > max_days, if so only
        # advance max_days days
        max_days = int(max_days)     
        if max_days > 0 and datetime.datetime.now() - dateutil.parser.parse(state_starttime) > datetime.timedelta(days=max_days):
            self.end_str = dateutil.parser.parse(state_starttime) + datetime.timedelta(days=max_days)
            self.end_str = self.end_str.isoformat().split('.')[0]

        sacct_version = exec_cmd("sacct --version")[0].split(' ')[1]
        if versioncmp(sacct_version, "17.11.0") < 0:
            command = COMMAND % ('ca,cd,f,nf,pr,rq', state_starttime, self.end_str)
        else:
            command = COMMAND % ('ca,cd,f,nf,pr,rq,to,oom', state_starttime, self.end_str)

        self.results = exec_cmd(command)
        # remove description line
        self.results = self.results[1:]


    def getNextLogEntry(self):

        try:
            entry = self.results.pop(0)
            return entry.strip().split('|')
        except IndexError:
            return None

class Slurm:   
    
    state = None
    cfg = None
    missing_user_mappings = {}
    idtimestamp = DEFAULT_IDTIMESTAMP
    
    def __init__(self,cfg):
        self.cfg = cfg
        self.idtimestamp = cfg.getConfigValueBool(SECTION, IDTIMESTAMP, DEFAULT_IDTIMESTAMP)
        self.billing_unit = cfg.getConfigValue(SECTION, BILLING_UNIT, DEFAULT_BILLING_UNIT)

    def getStateFile(self):
        return self.cfg.getConfigValue(SECTION, STATEFILE, DEFAULT_STATEFILE)
    
    def getNodes(self,node_str):
        """
        Makes a list of nodes from strings like:

        "brother[13-14]"
        "brother13"
        "brother[13-14,16,19]"
        "brother[13-18]"
        "compute-3-29"
        "compute-10-[11,13-14,16]",
        "compute-1-[0-1,3-18,20-24,26,28-30,32],compute-11-12,compute-13-[25-26,28-32],compute-14-[1-12,15,30-31],compute-2-[1-2,6-18,21,23,26-29],compute-4-[4-5,7-9,12-13,15-18,20-21,23-28,30-34],compute-5-[2,5,9-11,13,15-16,22,26,28],compute-6-[28,31-34],compute-7-[2,4-5,7]"]
        """
        nodes = []
        if '],' in node_str:
            elements = node_str.split('],')
        else:
            elements = [node_str]

        for element in elements:
            if '[' in element:
                parts = element.split('[')

                for sequence in parts[1].split(','):
                    sequence = sequence.rstrip(']')
                    if '-' in  sequence:
                        numbers = sequence.split('-')
			numlength = len(numbers[0])

                        for i in range(int(numbers[0]),int(numbers[1])+1):
			    nodes.append(parts[0] + "{0:0>{width}}".format(i,width=numlength))
                    else:
                        nodes.append(parts[0] + sequence)
            else:
                nodes += [element]

        return nodes

    def extractBillingUnit(self, tres):
        """
        Extracts the configured billing unit from a TRES field.
        """

        if tres == '':
            return 0

        # Transforms a string 'billing=5,cpu=2,mem=24G,node=1' into a dict
        # { 'billing': 5, 'cpu': 2, 'mem': '24G', 'node': 1 }
        tresdict = dict((k.strip(), v.strip()) for k, v in
                        (item.split('=') for item in tres.split(',')))

        ## Extract the configured unit
        value = tresdict.get(self.billing_unit,0)

        ## Convert memory to MiB, if needed
        if self.billing_unit == 'mem':
            if value.endswith('M'):
                value = int(value[:-1])
            elif value.endswith('G'):
                value = int(value[:-1]) * 1024
            elif value.endswith('T'):
                value = int(value[:-1]) * 1024**2
            else:
                value = int(value)
        else:
            value = int(value)

        return value

    def createUsageRecord(self, log_entry, hostname, user_map, project_map):
        """
        Creates a Usage Record object given a slurm log entry.
        """
        
        if log_entry[1] == '' or log_entry[2] == '':
            return None

        # extract data from the workload trace (log_entry)
        job_id       = str(log_entry[0])
        user_name    = getpwuid(int(log_entry[1]))[0]
        queue        = log_entry[2]
        submit_time  = time.mktime(common.datetimeFromIsoStr(log_entry[3]).timetuple())
        start_time   = time.mktime(common.datetimeFromIsoStr(log_entry[4]).timetuple())
        end_time     = time.mktime(common.datetimeFromIsoStr(log_entry[5]).timetuple())
        account_name = log_entry[6]
        utilized_cpu = common.getSeconds(log_entry[8])
        wall_time    = common.getSeconds(log_entry[7])
        core_count   = self.extractBillingUnit(log_entry[9])
        hosts        = self.getNodes(log_entry[10])
        nnodes       = int(log_entry[11])

        # clean data and create various composite entries from the work load trace
        job_identifier = job_id
        fqdn_job_id = hostname + ':' + job_id
        if self.idtimestamp:
            record_id_timestamp = re.sub("[-:TZ]","",usagerecord.epoch2isoTime(start_time)) # remove characters
            record_id = fqdn_job_id + ':' + record_id_timestamp
        else:
            record_id = fqdn_job_id

        if not user_name in user_map.getMapping():
            self.missing_user_mappings[user_name] = True

        vo_info = []
        if account_name is not None:
            mapped_project = project_map.get(account_name)
            if mapped_project is not None:
                voi = usagerecord.VOInformation()
                voi.type = 'lrmsurgen-projectmap'
                voi.name = mapped_project
                vo_info = [voi]

        ## fill in usage record fields
        ur = usagerecord.UsageRecord()
        ur.record_id        = record_id
        ur.local_job_id     = job_identifier
        ur.global_job_id    = fqdn_job_id
        ur.local_user_id    = user_name
        ur.global_user_name = user_map.get(user_name)
        ur.machine_name     = hostname
        ur.queue            = queue
        ur.processors       = core_count
        ur.node_count       = nnodes
        ur.host             = ','.join(hosts)
        ur.submit_time      = usagerecord.epoch2isoTime(submit_time)
        ur.start_time       = usagerecord.epoch2isoTime(start_time)
        ur.end_time         = usagerecord.epoch2isoTime(end_time)
        ur.cpu_duration     = utilized_cpu
        ur.wall_duration    = wall_time
        ur.project_name     = account_name
        ur.vo_info         += vo_info

        return ur

    def generateUsageRecords(self, hostname, user_map, project_map):
        """
        Starts the UR generation process.
        """
        self.missing_user_mappings = {}

        tlp = SlurmBackend(self.state, self.cfg.getConfigValue(SECTION, MAX_DAYS, MAX_DAYS_DEFAULT))
        
        count = 0
        while True:
            log_entry = tlp.getNextLogEntry()

            if log_entry is None:
                break # no more log entries

            ur = self.createUsageRecord(log_entry, hostname, user_map, project_map)
            
            if ur is not None: 
                common.writeUr(ur,self.cfg)            
                count = count + 1
            
        # only update state if a entry i written
        if count > 0:
            self.state = tlp.end_str
            
        logging.info('Total number of UR written = %d' % count)

    def parseGeneratorState(self,state):        
        """
        Get state of where to the UR generation has reached in the log.
        This is returns the last jobid processed.
        """
        if state is None or len(state) == 0:
            # no statefile -> we start from 50000 (DEFAULT_STATEFILE_DEFAULT) seconds / 5.7 days ago
            sfd = int(self.cfg.getConfigValue(SECTION, STATEFILE_DEFAULT, DEFAULT_STATEFILE_DEFAULT))
            dt = datetime.datetime.now()-datetime.timedelta(seconds=sfd)
            state = dt.isoformat().split('.')[0]

        self.state = state

    def createGeneratorState(self):
        return self.state

