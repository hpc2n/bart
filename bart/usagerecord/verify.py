#
# Usage Record verification.
#
# Module for the SGAS Batch system Reporting Tool (BaRT).
#
# Author: Magnus Jonsson <magnus@hpc2n.umu.se>
# Copyright: NeIC 2014

import logging
from bart.usagerecord import urparser

logger = logging.getLogger(__name__)

def verify(ur):
    try:
        d = urparser.xmlToDict(ur)
        if d['record_id'] is None:
            logger.error("No record_id found")
            return False
    except:
        logger.error("Failed to convert UR XML into dict")
        return False
    return True

