""" Classes handling the Delete operations. """
import requests
import json

class SDBDelete:
    """ SightingDB Delete handler
    This class deletes a namespace entirely. Use with care!
    """
    def __init__(self, connection):
        """Initialize the instance with the connection parameters.
        
        Arguments:

        * connection -- The connection object used to connect to SightingDB
        """        
        self.con = connection

        if self.con.warnings_disabled:
            from requests.packages.urllib3.exceptions import InsecureRequestWarning
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

        
    def delete(self, namespace):
        """Deletes a namespace with all their attributes

        Arguments:

        * namespace -- Namespace to delete
        """
        r = requests.post("https://%s:%d/d/%s" % (self.con.host, self.con.port, namespace), headers={"content-type": "application/json","Authorization": self.con.apikey},
                          verify=self.con.verify)
        if r.status_code == 200:
            return True
        return False
    
