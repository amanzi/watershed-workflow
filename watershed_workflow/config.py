"""Configuration and global defaults."""

import os
import subprocess
import configparser

def home():
    return os.path.expanduser('~')

def get_default_config():
    """Dictionary of all config option defaults.

    Returns
    -------
    rcParams : configparser.ConfigParser
      A dict-like object containing parameters.
    """
    rcParams = configparser.ConfigParser()

    rcParams['DEFAULT']['data_directory'] = ""
    rcParams['DEFAULT']['ssl_cert'] = "True"  # note this can be True,
                                            # False (bad
                                            # idea/permissive) or a
                                            # path to ssl certs,
                                            # e.g. /etc/ssl/cert.perm
                                            # or similar
    rcParams['DEFAULT']['proj_network'] = "False"
    return rcParams

def get_config():
    try:
        data_directory = os.path.join(os.environ['WATERSHED_WORKFLOW_DATA_DIR'])
    except KeyError:
        data_directory = os.path.join(os.getcwd(), 'data')
    rc = get_default_config()
    rc['DEFAULT']['data_directory'] = data_directory
                                            
    rc.read([os.path.join(home(), '.watershed_workflowrc'),
             os.path.join(os.getcwd(), '.watershed_workflowrc'),
             os.path.join(os.getcwd(), 'watershed_workflowrc'),
             os.path.join(os.getcwd(), '.docker_watershed_workflowrc')])
             
    return rc

def set_data_directory(path):
    """Sets the directory in which all data is stored."""
    rcParams['DEFAULT']['data_directory'] = path

# global config
rcParams = get_config()






