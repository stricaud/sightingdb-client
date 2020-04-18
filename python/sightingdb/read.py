import requests
import json

class SDBRead:
    def __init__(self, connection):
        self.con = connection
        self.items = {"items": []}

    def add(self, namespace, value, timestamp=0):
        if namespace[0] == "/":
            namespace = namespace[1:]
        self.items["items"].append({"namespace": namespace, "value": value, "timestamp": timestamp})

    def fetch(self):
        r = requests.post("https://%s:%d/rb" % (self.con.host, self.con.port), headers={"content-type": "application/json","Authorization": self.con.apikey},
                         data = json.dumps(self.items), verify=self.con.verify)
        return json.loads(r.text)["items"]

    def read_one(self, namespace, value, timestamp=0):
        self.add(namespace, value, timestamp)
        return self.fetch()[0]


