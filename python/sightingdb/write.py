import requests
import json

class SDBWrite:
    def __init__(self, connection):
        self.con = connection
        self.items = {"items": []}

    def add(self, namespace, value, timestamp=0):
        if namespace[0] == "/":
            namespace = namespace[1:]
        self.items["items"].append({"namespace": namespace, "value": value, "timestamp": timestamp})

    def commit(self):
        r = requests.post("https://%s:%d/wb" % (self.con.host, self.con.port), headers={"content-type": "application/json","Authorization": self.con.apikey},
                         data = json.dumps(self.items), verify=self.con.verify)
        return r.text

    def write_one(self, namespace, value, timestamp=0):
        self.add(namespace, value, timestamp)
        return self.commit()

    

        
    
