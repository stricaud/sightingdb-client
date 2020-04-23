import requests
import json

class SDBWrite:
    def __init__(self, connection):
        """Initialize the instance with the connection parameters.
        
        Arguments:

        * connection -- The connection object used to connect to SightingDB
        """
        self.con = connection
        self.items = {"items": []}

        if self.con.warnings_disabled:
            from requests.packages.urllib3.exceptions import InsecureRequestWarning
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
        
    def add(self, namespace, value, timestamp=0):
        """Adds a value to a namespace for writing.

        Arguments:

        * namespace -- The namespace where to store the value
        * value -- The value to increase
        * timestamp -- Set the timestamp, if not set, default to current UTC time on the server
        """
        if namespace[0] == "/":
            namespace = namespace[1:]
        self.items["items"].append({"namespace": namespace, "value": value, "timestamp": timestamp})

    def commit(self):
        """Sends all the values that have been added to SightingDB.
        """
        r = requests.post("https://%s:%d/wb" % (self.con.host, self.con.port), headers={"content-type": "application/json","Authorization": self.con.apikey},
                         data = json.dumps(self.items), verify=self.con.verify)
        self.items["items"] = []
        return r.text

    def write_one(self, namespace, value, timestamp=0):
        """Write one single value instead of a bulk.
        """
        self.items["items"] = []
        self.add(namespace, value, timestamp)
        return self.commit()

    

        
    
