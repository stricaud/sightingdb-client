import requests
import json

class SDBDelete:
    def __init__(self, connection):
        self.con = connection

    def delete(self, namespace):
        r = requests.post("https://%s:%d/d/%s?val=\"\"" % (self.con.host, self.con.port, namespace), headers={"content-type": "application/json","Authorization": self.con.apikey},
                          verify=self.con.verify)
        if r.status_code == 200:
            return True
        return False
    
