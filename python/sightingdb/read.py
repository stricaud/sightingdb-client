""" Classes for handling SightingDB data reading """
import requests
import json

class SDBRead:
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
        """Adds a value to a namespace for reading.

        Arguments:

        * namespace -- The namespace where to store the value
        * value -- The value to increase
        * timestamp -- Set the timestamp, if not set, default to current UTC time on the server
        """
        if namespace[0] == "/":
            namespace = namespace[1:]
        self.items["items"].append({"namespace": namespace, "value": value, "timestamp": timestamp})

    def fetch(self):
        """Read all values that have been added.
        """
        r = requests.post("https://%s:%d/rb" % (self.con.host, self.con.port), headers={"content-type": "application/json","Authorization": self.con.apikey},
                         data = json.dumps(self.items), verify=self.con.verify)
        self.items["items"] = []
        return json.loads(r.text)["items"]

    def fetch_with_stats(self):
        """Read all values that have been added, including statistics.
        """
        r = requests.post("https://%s:%d/rbs" % (self.con.host, self.con.port), headers={"content-type": "application/json","Authorization": self.con.apikey},
                         data = json.dumps(self.items), verify=self.con.verify)
        return json.loads(r.text)["items"]

    def read_one(self, namespace, value, timestamp=0):
        """Read one value only.
        """
        self.items["items"] = []
        self.add(namespace, value, timestamp)
        return self.fetch()[0]

    def read_one_with_stats(self, namespace, value, timestamp=0):
        """Read one value only with statistics.
        """
        self.items["items"] = []
        self.add(namespace, value, timestamp)
        return self.fetch_with_stats()[0]


